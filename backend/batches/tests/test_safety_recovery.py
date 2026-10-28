import io
from datetime import timedelta
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from batches.models import Alert, Batch, Engine, Event, Failure, Number, UserQueue
from batches.notifications import deliver_one
from batches.queue import claim_number, clear_safety, finish_number, pause_all
from batches.upstream import Result


@override_settings(BATCH_LOOKUP_INTERVAL=0)
class SafetyRecoveryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('recovery-owner')
        self.staff = get_user_model().objects.create_user('recovery-staff', is_staff=True)
        self.permission = Permission.objects.get(content_type__app_label='batches', codename='resume_safety_stop')
        self.staff.user_permissions.add(self.permission)
        self.batch = Batch.objects.create(user=self.owner, original_name='recovery.csv', source_sha256='',
            status='running', valid_rows=5, processed_rows=4, good_rows=1, bad_rows=1, error_rows=2)
        UserQueue.objects.create(user=self.owner, active_batch=self.batch)
        self.rows = []
        for sequence, status in enumerate(['good','bad','error','error','pending'], 1):
            row = Number.objects.create(batch=self.batch, sequence=sequence, source_row=sequence,
                ani=f'234567890{sequence}', status=status, attempts=0 if status=='pending' else 1,
                raw_response='original response')
            self.rows.append(row)
            if status == 'error':
                Failure.objects.create(number=row, attempt=1, kind='error', http_code=503,
                    raw_response='original error response', message='Upstream HTTP 503')
        self.manual = Batch.objects.create(user=self.owner, original_name='manual.csv', source_sha256='',
            status='paused', pause_reason='manual', valid_rows=1, processed_rows=1, error_rows=1)
        self.manual_error = Number.objects.create(batch=self.manual, sequence=1, source_row=1, ani='3456789012', status='error', attempts=1)
        pause_all('10 consecutive upstream lookup failures.')
        self.client.force_login(self.staff)

    def test_explicit_retry_only_requeues_errors_in_safety_paused_files(self):
        response = self.client.post('/api/batches/safety/resume/', {'retry_failed':True}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok':True,'resumed_files':1,'retried_rows':2})
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.status,self.batch.processed_rows,self.batch.error_rows), ('queued',2,0))
        self.assertEqual(list(Number.objects.filter(batch=self.batch).order_by('sequence').values_list('status',flat=True)),
            ['good','bad','pending','pending','pending'])
        self.assertEqual(Failure.objects.filter(number__batch=self.batch).count(), 2)
        self.assertEqual(Number.objects.get(pk=self.rows[0].pk).raw_response, 'original response')
        self.manual.refresh_from_db();self.manual_error.refresh_from_db()
        self.assertEqual((self.manual.status,self.manual.pause_reason,self.manual.error_rows), ('paused','manual',1))
        self.assertEqual(self.manual_error.status,'error')
        claim = claim_number()
        self.assertEqual(claim['id'],self.rows[2].pk)
        self.assertEqual(Number.objects.get(pk=claim['id']).attempts,2)
        finish_number(claim, Result('good','new result',200,1,1))
        self.assertTrue(Event.objects.filter(batch=self.batch,action='safety_retry_queued',actor=self.staff).exists())
        self.assertEqual(clear_safety(self.staff, retry_failed=True), {'resumed_files':0,'retried_rows':0})

    def test_default_resume_preserves_failed_rows_for_explicit_review(self):
        result = clear_safety(self.staff)
        self.assertEqual(result,{'resumed_files':1,'retried_rows':0})
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.error_rows,2)
        self.assertEqual(claim_number()['id'],self.rows[4].pk)

    def test_permission_is_required_and_dashboard_updates_after_assignment(self):
        operator = get_user_model().objects.create_user('unprivileged-staff',is_staff=True)
        self.client.force_login(operator)
        self.assertFalse(self.client.get('/api/batches/').json()['safety']['can_resume'])
        self.assertEqual(self.client.post('/api/batches/safety/resume/',{},content_type='application/json').status_code,403)
        operator.user_permissions.add(self.permission)
        self.assertTrue(self.client.get('/api/batches/').json()['safety']['can_resume'])
        self.owner.user_permissions.add(self.permission)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post('/api/batches/safety/resume/',{'retry_failed':True},content_type='application/json').status_code,403)
        self.assertTrue(Engine.objects.get().paused)

    def test_retry_option_requires_boolean_and_csrf(self):
        for value in ['true',1,None]:
            response=self.client.post('/api/batches/safety/resume/',{'retry_failed':value},content_type='application/json')
            self.assertEqual(response.status_code,409)
        csrf=Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        self.assertEqual(csrf.post('/api/batches/safety/resume/',{},content_type='application/json').status_code,403)
        self.assertTrue(Engine.objects.get().paused)

    def test_active_lookup_lease_blocks_retry_and_counters_remain_intact(self):
        Number.objects.filter(pk=self.rows[4].pk).update(status='working')
        Engine.objects.update(active_number=self.rows[4],active_batch=self.batch.pk,
            lease_expires_at=timezone.now()+timedelta(seconds=60))
        with self.assertRaises(ValueError):
            clear_safety(self.staff,retry_failed=True)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.error_rows,2)
        self.assertTrue(Engine.objects.get().paused)
        self.assertEqual(Number.objects.get(pk=self.rows[2].pk).status,'error')

    def test_admin_action_available_to_recovery_staff_without_general_change_permission(self):
        response=self.client.get('/admin/batches/engine/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'resume_and_retry')
        response=self.client.post('/admin/batches/engine/',{'action':'resume_and_retry','_selected_action':'1','index':'0'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(Engine.objects.get().paused)
        self.assertEqual(Number.objects.filter(batch=self.batch,status='error').count(),0)

    def test_read_only_engine_staff_cannot_submit_recovery_action(self):
        viewer=get_user_model().objects.create_user('engine-viewer',is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(content_type__app_label='batches',codename='view_engine'))
        self.client.force_login(viewer)
        response=self.client.get('/admin/batches/engine/')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'value="resume_and_retry"')
        self.client.post('/admin/batches/engine/',{'action':'resume_and_retry','_selected_action':'1','index':'0'})
        self.assertTrue(Engine.objects.get().paused)

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/123/private-token')
    def test_discord_http_error_is_actionable_and_does_not_leak_webhook(self):
        with patch('batches.notifications.urlopen',side_effect=HTTPError('https://discord.com/api/webhooks/123/private-token',403,'Forbidden',{},io.BytesIO(b''))):
            deliver_one()
        error=Alert.objects.get().last_error
        self.assertIn('HTTP 403',error)
        self.assertNotIn('private-token',error)
        self.assertTrue(Engine.objects.get().paused)

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/123/private-token')
    def test_discord_network_error_does_not_leak_url(self):
        with patch('batches.notifications.urlopen',side_effect=URLError('private-token')):
            deliver_one()
        error=Alert.objects.get().last_error
        self.assertIn('DNS, TLS and outbound HTTPS',error)
        self.assertNotIn('private-token',error)
        self.assertTrue(Engine.objects.get().paused)
