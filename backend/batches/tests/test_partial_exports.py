import csv
import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from batches.files import LostLease
from batches.models import Batch, Engine, Event, Number, PartialExport, UserQueue
from batches.partial_exports import build_partial_export, claim_partial_export, partial_path, request_partial
from batches.queue import claim_number, control_batch, finish_number, pause_all
from batches.upstream import Result


@override_settings(BATCH_CHUNK_SIZE=2, BATCH_ACCEL_DOWNLOADS=False, BATCH_LOOKUP_INTERVAL=0)
class PartialExportTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(BATCH_DATA_ROOT=self.temp.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user('partial-owner')
        self.other = get_user_model().objects.create_user('partial-other')
        self.client.force_login(self.user)
        self.batch = Batch.objects.create(user=self.user, original_name='snapshot.csv', source_sha256='',
            status='paused', valid_rows=5, good_rows=2, bad_rows=1, error_rows=1, processed_rows=4)
        UserQueue.objects.create(user=self.user, enabled=False, awaiting_decision=True)
        completed = timezone.now() - timedelta(seconds=5)
        for sequence, ani, status in [(1,'2345678901','good'), (2,'2345678902','bad'),
                (3,'2345678903','error'), (4,'2345678904','pending'), (5,'2345678901','good')]:
            Number.objects.create(batch=self.batch, sequence=sequence, source_row=sequence,
                ani=ani, status=status, completed_at=None if status == 'pending' else completed,
                raw_response='saved response', accepted_1=1 if status in ('good','bad') else None,
                accepted_2=1 if status == 'good' else 0 if status == 'bad' else None)

    def prepare(self):
        return self.client.post(f'/api/batches/{self.batch.pk}/partial-export/', {}, content_type='application/json')

    def build(self):
        task = claim_partial_export()
        self.assertIsNotNone(task)
        build_partial_export(task)
        task.refresh_from_db()
        return task

    def download_url(self, task):
        return f'/api/batches/{self.batch.pk}/partial-exports/{task.pk}/download/'

    def rows(self, task):
        return list(csv.reader(io.StringIO(partial_path(task).read_text())))

    def test_snapshot_contents_counts_duplicates_and_pause_state(self):
        response = self.prepare()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['resolved_rows'], 3)
        task = self.build()
        self.assertEqual(task.status, 'ready')
        self.assertEqual(self.rows(task), [['sqGOOD','sqBAD'],['2345678901',''],['','2345678902'],['2345678901','']])
        self.assertEqual(task.export_bytes, partial_path(task).stat().st_size)
        self.assertEqual(len(task.export_sha256), 64)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'paused')
        queue = UserQueue.objects.get(user=self.user)
        self.assertFalse(queue.enabled)
        self.assertTrue(queue.awaiting_decision)
        self.assertEqual(self.batch.processed_rows, 4)
        response = self.client.get(self.download_url(task))
        self.assertEqual(response.status_code, 200)
        self.assertIn('partial-3.csv', response['Content-Disposition'])
        self.assertEqual(response['X-Export-Rows'], '3')
        response.close()
        self.assertTrue(Event.objects.filter(batch=self.batch, action='partial_export_downloaded').exists())
        self.assertEqual(self.client.get(f'/api/batches/{self.batch.pk}/download/').status_code, 409)

    def test_requests_and_repeated_downloads_are_cached_without_number_queries(self):
        with CaptureQueriesContext(connection) as queries:
            first, second = self.prepare(), self.prepare()
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(PartialExport.objects.count(), 1)
        self.assertFalse(any('batches_number' in q['sql'].lower() for q in queries))
        task = self.build()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.prepare().status_code, 200)
            for _ in range(2):
                response = self.client.get(self.download_url(task))
                self.assertEqual(response.status_code, 200)
                response.close()
        self.assertFalse(any('batches_number' in q['sql'].lower() for q in queries))
        self.assertEqual(Event.objects.filter(action='partial_export_requested').count(), 1)

    def test_resume_retry_during_build_does_not_change_snapshot(self):
        snapshot = request_partial(self.batch.pk, self.user)
        control_batch(self.batch.pk, self.user, 'retry')
        claim = claim_number()
        self.assertEqual(claim['ani'], '2345678903')
        finish_number(claim, Result('good', 'new response', 200, 1, 1))
        task = self.build()
        self.assertEqual(task.pk, snapshot.pk)
        self.assertEqual(len(self.rows(task)), 4)
        self.assertNotIn(['2345678903',''], self.rows(task))
        dashboard = self.client.get('/api/batches/').json()
        self.assertEqual(dashboard['active'][0]['resolved_rows'], 4)
        self.assertEqual(dashboard['active'][0]['partial_export']['resolved_rows'], 3)
        self.assertEqual(dashboard['active'][0]['partial_export']['status'], 'ready')
        old_bytes = partial_path(task).read_bytes()
        control_batch(self.batch.pk, self.user, 'pause')
        self.assertEqual(self.prepare().json()['resolved_rows'], 4)
        newer = self.build()
        self.assertNotEqual(task.pk, newer.pk)
        self.assertEqual(len(self.rows(newer)), 5)
        self.assertIn(['2345678903',''], self.rows(newer))
        self.assertEqual(partial_path(task).read_bytes(), old_bytes)

    def test_waits_for_manual_pause_to_checkpoint_in_flight_number(self):
        control_batch(self.batch.pk, self.user, 'resume')
        claim = claim_number()
        control_batch(self.batch.pk, self.user, 'pause')
        self.assertEqual(self.prepare().status_code, 409)
        finish_number(claim, Result('good', 'saved', 200, 1, 1))
        self.assertEqual(self.prepare().json()['resolved_rows'], 4)
        self.assertEqual(len(self.rows(self.build())), 5)

    def test_safety_paused_snapshot_excludes_late_in_flight_result(self):
        control_batch(self.batch.pk, self.user, 'resume')
        claim = claim_number()
        pause_all('Test safety pause')
        self.assertEqual(self.prepare().json()['resolved_rows'], 3)
        finish_number(claim, Result('good', 'saved late', 200, 1, 1))
        self.assertEqual(len(self.rows(self.build())), 4)
        self.assertTrue(Engine.objects.get().paused)

    def test_empty_running_deleted_and_unready_jobs_reject_requests(self):
        for status, good, bad in [('paused',0,0),('queued',2,1),('running',2,1),('pausing',2,1),('preprocessing',2,1),('completed',2,1)]:
            Batch.objects.filter(pk=self.batch.pk).update(status=status, good_rows=good, bad_rows=bad)
            self.assertEqual(self.prepare().status_code, 409)
        self.assertFalse(PartialExport.objects.exists())
        Batch.objects.filter(pk=self.batch.pk).update(status='deleted')
        self.assertEqual(self.prepare().status_code, 404)

    def test_private_csrf_protected_post_and_snapshot_batch_matching(self):
        self.prepare()
        task = PartialExport.objects.get()
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 409)
        self.client.force_login(self.other)
        self.assertEqual(self.prepare().status_code, 404)
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 404)
        self.client.logout()
        self.assertEqual(self.prepare().status_code, 401)
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 401)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(f'/api/batches/{self.batch.pk}/partial-export/').status_code, 403)
        self.assertEqual(csrf.get(f'/api/batches/{self.batch.pk}/partial-export/').status_code, 405)
        self.client.force_login(self.user)
        other = Batch.objects.create(user=self.user, original_name='other.csv', source_sha256='')
        mismatch = f'/api/batches/{other.pk}/partial-exports/{task.pk}/download/'
        self.assertEqual(self.client.get(mismatch).status_code, 404)

    def test_failure_does_not_resume_or_pause_engine_and_can_be_retried(self):
        self.prepare()
        with patch('batches.partial_exports.os.replace', side_effect=OSError('disk full')):
            task = self.build()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(partial_path(task).exists())
        self.assertFalse(Engine.objects.get().paused)
        self.assertEqual(self.prepare().status_code, 202)
        retry = self.build()
        self.assertEqual(retry.pk, task.pk)
        self.assertEqual(retry.status, 'ready')
        self.assertFalse(list(partial_path(task).parent.glob('*.tmp')))

    def test_expired_lease_fences_stale_worker_and_recovers(self):
        self.prepare()
        stale = claim_partial_export()
        self.assertIsNone(claim_partial_export())
        PartialExport.objects.filter(pk=stale.pk).update(task_expires_at=timezone.now()-timedelta(seconds=1))
        recovered = claim_partial_export()
        self.assertNotEqual(stale.task_token, recovered.task_token)
        with self.assertRaises(LostLease):
            build_partial_export(stale)
        build_partial_export(recovered)
        recovered.refresh_from_db()
        self.assertEqual(recovered.status, 'ready')

    def test_deleted_batch_revokes_download_and_cancels_background_work(self):
        self.prepare()
        task = self.build()
        control_batch(self.batch.pk, self.user, 'delete')
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 404)
        self.assertEqual(self.prepare().status_code, 404)
        self.assertIsNone(claim_partial_export())

    def test_corrupt_snapshot_counts_fail_closed_and_never_publish(self):
        self.prepare()
        Number.objects.filter(batch=self.batch, sequence=1).update(status='error')
        task = self.build()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(partial_path(task).exists())
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 409)

    @override_settings(BATCH_ACCEL_DOWNLOADS=True)
    def test_internal_redirect_and_metadata_remain_private(self):
        self.prepare()
        task = self.build()
        response = self.client.get(self.download_url(task))
        self.assertEqual(response.content, b'')
        self.assertEqual(response['X-Accel-Redirect'], f'/protected-batch-exports/{self.batch.pk}/partial-{task.pk}.csv')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get('/api/batches/').json()['active'][0]['partial_export']['id'], str(task.pk))

    def test_missing_cached_file_can_be_regenerated_without_new_snapshot(self):
        self.prepare()
        task = self.build()
        partial_path(task).unlink()
        self.assertEqual(self.client.get(self.download_url(task)).status_code, 503)
        dashboard = self.client.get('/api/batches/').json()
        self.assertEqual(dashboard['active'][0]['partial_export']['status'], 'failed')
        self.assertEqual(self.prepare().status_code, 202)
        regenerated = self.build()
        self.assertEqual(regenerated.pk, task.pk)
        self.assertTrue(partial_path(regenerated).is_file())

    def test_worker_records_unexpected_partial_failure_without_stopping_lookups(self):
        self.prepare()
        with patch('batches.management.commands.batch_worker.build_partial_export', side_effect=RuntimeError('test failure')):
            with self.assertLogs('batches.management.commands.batch_worker', level='ERROR'):
                call_command('batch_worker', role='files', once=True, stdout=io.StringIO())
        self.assertEqual(PartialExport.objects.get().status, 'failed')
        self.assertFalse(Engine.objects.get().paused)
        self.assertEqual(self.prepare().status_code, 202)

    def test_dashboard_snapshot_metadata_query_is_bounded_and_uses_no_phone_rows(self):
        self.prepare()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/batches/')
        count = len(queries)
        self.assertEqual(response.status_code, 200)
        for index in range(6):
            batch = Batch.objects.create(user=self.user, original_name=f'file{index}.csv', source_sha256='',
                status='paused', valid_rows=10, good_rows=1)
            PartialExport.objects.create(batch=batch, resolved_rows=1, good_rows=1, bad_rows=0, cutoff_at=timezone.now())
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/batches/')
        self.assertEqual(len(queries), count)
        self.assertEqual(len(response.json()['active']), 7)
        self.assertFalse(any('batches_number' in q['sql'].lower() for q in queries))
