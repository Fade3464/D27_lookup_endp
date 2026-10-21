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
from batches.models import Batch, Number
from batches.queue import claim_number, finish_number
from batches.upstream import Result


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock test")
@override_settings(BATCH_LOOKUP_INTERVAL=0)
class ConcurrencyTests(TransactionTestCase):
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
            Number.objects.filter(batch=batch).update(status="good", accepted_1=1, accepted_2=1, raw_response='{"synthetic":true}')
            Batch.objects.filter(pk=batch.pk).update(status="export_pending", good_rows=500000, processed_rows=500000)
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
