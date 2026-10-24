import csv
import os
import tempfile
import threading
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from batches.files import build_export, claim_file_task, export_path, import_csv, save_upload
from batches.models import Batch, Number, Engine
from batches.queue import claim_number, finish_number, control_batch
from batches.upstream import Result
from batches.partial_exports import build_partial_export, claim_partial_export, partial_path, request_partial
from django.utils import timezone


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock test")
@override_settings(BATCH_LOOKUP_INTERVAL=0)
class ConcurrencyTests(TransactionTestCase):
    def test_partial_request_during_publication_does_not_deadlock(self):
        user = get_user_model().objects.create_user("partial-publish-race")
        batch = Batch.objects.create(user=user, original_name="test.csv", source_sha256="", status="paused", valid_rows=1, good_rows=1)
        Number.objects.create(batch=batch, sequence=1, source_row=1, ani="2345678901", status="good", completed_at=timezone.now())
        snapshot = request_partial(batch.pk, user)
        task = claim_partial_export()
        barrier = threading.Barrier(2)
        errors = []
        def run(action):
            try:
                close_old_connections()
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=10)
                if action == "build":
                    build_partial_export(task)
                else:
                    request_partial(batch.pk, user)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        threads = [threading.Thread(target=run, args=(action,)) for action in ("build", "request")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, "ready")

    def test_only_one_file_worker_claims_a_partial_export(self):
        user = get_user_model().objects.create_user("partial-race")
        batch = Batch.objects.create(user=user, original_name="test.csv", source_sha256="", status="paused", valid_rows=1, good_rows=1)
        Number.objects.create(batch=batch, sequence=1, source_row=1, ani="2345678901", status="good", completed_at=timezone.now())
        request_partial(batch.pk, user)
        barrier = threading.Barrier(2)
        claims, errors = [], []
        def claim():
            try:
                close_old_connections()
                barrier.wait(timeout=10)
                claims.append(claim_partial_export())
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(value is not None for value in claims), 1)
        build_partial_export(next(value for value in claims if value))

    def test_delete_and_worker_claim_are_serialized(self):
        from batches.models import UserQueue
        user = get_user_model().objects.create_user("delete-race")
        batch = Batch.objects.create(user=user, original_name="test.csv", source_sha256="", status="queued", valid_rows=1)
        Number.objects.create(batch=batch, sequence=1, source_row=1, ani="2345678901")
        UserQueue.objects.create(user=user)
        Engine.objects.create(pk=1)
        barrier = threading.Barrier(2)
        results, errors = {}, []
        def run(action):
            try:
                close_old_connections()
                barrier.wait(timeout=10)
                if action == "claim":
                    results[action] = claim_number()
                else:
                    try:
                        results[action] = control_batch(batch.pk, user, "delete")
                    except ValueError:
                        results[action] = "busy"
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        threads = [threading.Thread(target=run, args=(action,)) for action in ("claim", "delete")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        batch.refresh_from_db()
        if results["claim"] is None:
            self.assertEqual(results["delete"], {"deleted": True})
            self.assertEqual(batch.status, "deleted")
        else:
            self.assertEqual(results["delete"], "busy")
            self.assertEqual(batch.status, "running")

    def test_two_workers_cannot_claim_two_numbers_simultaneously(self):
        from batches.models import UserQueue
        user = get_user_model().objects.create_user("concurrent")
        batch = Batch.objects.create(user=user, original_name="test.csv", source_sha256="", status="queued", valid_rows=2)
        UserQueue.objects.create(user=user)
        Number.objects.bulk_create([Number(batch=batch, sequence=i, source_row=i, ani="2345678901") for i in (1, 2)])
        barrier = threading.Barrier(2)
        claims, errors = [], []
        def claim():
            try:
                close_old_connections()
                barrier.wait(timeout=10)
                claims.append(claim_number())
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(value is not None for value in claims), 1)
        result = next(value for value in claims if value)
        self.assertTrue(finish_number(result, Result("good", "saved", 200, 1, 1)))
        self.assertIsNotNone(claim_number())


@skipUnless(os.getenv("RUN_LARGE_BATCH_TESTS") == "1", "Opt-in 500,000-row import/export test")
class LargeFileTests(TransactionTestCase):
    def test_half_million_numbers_import_and_export_without_api_calls(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(BATCH_DATA_ROOT=temp, BATCH_CHUNK_SIZE=5000):
            user = get_user_model().objects.create_user("large-file-test")
            with tempfile.TemporaryFile() as file:
                for _ in range(500):
                    file.write(b"2345678901\n" * 1000)
                file.seek(0)
                upload = File(file, name="500000.csv")
                batch = save_upload(user, upload)
            import_csv(claim_file_task())
            batch.refresh_from_db()
            self.assertEqual(batch.valid_rows, 500000)
            self.assertEqual(Number.objects.filter(batch=batch).count(), 500000)
            # Simulate resolved rows; the load test never hits the real API.
            Number.objects.filter(batch=batch, sequence__lte=300000).update(status="good", accepted_1=1, accepted_2=1,
                completed_at=timezone.now()-timedelta(seconds=1), raw_response='{"synthetic":true}')
            Batch.objects.filter(pk=batch.pk).update(status="paused", good_rows=300000, processed_rows=300000)
            snapshot = request_partial(batch.pk, user)
            # Simulate resumed processing before the snapshot is built: later
            # successful results must stay out of the already requested CSV.
            Number.objects.filter(batch=batch, sequence__gt=300000).update(status="good", accepted_1=1, accepted_2=1,
                completed_at=timezone.now(), raw_response='{"synthetic":true}')
            Batch.objects.filter(pk=batch.pk).update(status="running", good_rows=500000, processed_rows=500000)
            build_partial_export(claim_partial_export())
            snapshot.refresh_from_db()
            self.assertEqual(snapshot.status, "ready")
            with partial_path(snapshot).open() as file:
                rows = csv.reader(file)
                self.assertEqual(next(rows), ["sqGOOD", "sqBAD"])
                self.assertEqual(sum(1 for row in rows if row == ["2345678901", ""]), 300000)
            Batch.objects.filter(pk=batch.pk).update(status="export_pending")
            build_export(claim_file_task())
            batch.refresh_from_db()
            self.assertEqual(batch.status, "completed")
            with export_path(batch.pk).open() as file:
                rows = csv.reader(file)
                self.assertEqual(next(rows), ["sqGOOD", "sqBAD"])
                count = 0
                for row in rows:
                    self.assertEqual(row, ["2345678901", ""])
                    count += 1
                self.assertEqual(count, 500000)
