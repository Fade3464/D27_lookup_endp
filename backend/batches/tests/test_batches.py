import copy
import csv
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from batches.files import build_export, claim_file_task, clean_phone, export_path, import_csv, save_upload
from batches.models import Alert, Batch, Engine, Event, Failure, Number, UserQueue
from batches.notifications import deliver_one
from batches.queue import claim_number, clear_safety, continue_queue, control_batch, finish_number
from batches.upstream import Result, lookup_number
from checker.tests.upstream import EXAMPLE_RESPONSE


@override_settings(BATCH_LOOKUP_INTERVAL=0, BATCH_CHUNK_SIZE=2, BATCH_ACCEL_DOWNLOADS=False)
class BatchTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(BATCH_DATA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user("operator", password="Example-Passphrase-123")
        self.other = get_user_model().objects.create_user("other", password="Example-Passphrase-456")
        self.client.force_login(self.user)

    def upload(self, content=b"2345678901\n2345678902\n", user=None):
        batch = save_upload(user or self.user, SimpleUploadedFile("phones.csv", content))
        task = claim_file_task()
        self.assertEqual(task.pk, batch.pk)
        import_csv(task)
        batch.refresh_from_db()
        return batch

    def step(self, kind="good"):
        claim = claim_number()
        self.assertIsNotNone(claim)
        result = Result(kind, '{"saved":true}', 200 if kind in ("good", "bad") else 503,
                        1 if kind == "good" else 0, 1 if kind in ("good", "bad") else None,
                        "Temporary upstream failure" if kind not in ("good", "bad") else "")
        self.assertTrue(finish_number(claim, result))
        return claim

    def test_cleaning_rejects_garbage_formulas_exponents_international_and_decimals(self):
        for value in ["2345678901", " (234) 567-8901 ", "+1 234 567 8901", "1-234-567-8901", "234.567.8901"]:
            self.assertEqual(clean_phone(value), "2345678901")
        for value in ["abc2345678901", "=2345678901", "2345678901e0", "12345.67890", "+2345678901", "+44 2345678901", "１２３４５６７８９０", "", "123", "23456789012"]:
            self.assertIsNone(clean_phone(value), value)

    def test_first_column_only_bom_headers_blank_and_duplicates(self):
        batch = self.upload(b'\xef\xbb\xbfphone,other\n"(234) 567-8901",ignore\n2345678901,duplicate\ntrash,3456789012\n,3456789013\n\n')
        self.assertEqual(batch.status, "queued")
        self.assertEqual(batch.valid_rows, 2)
        self.assertEqual(batch.cleaned_rows, 1)
        self.assertEqual(batch.rejected_rows, 4)
        self.assertEqual(list(Number.objects.filter(batch=batch).values_list("source_row", "ani")), [(2, "2345678901"), (3, "2345678901")])

    def test_zero_valid_csv_never_queues(self):
        batch = self.upload(b"garbage,2345678901\n,3456789012\n")
        self.assertEqual(batch.status, "invalid")
        self.assertIn("No valid 10-digit", batch.error_message)
        self.assertIsNone(claim_number())

    def test_bad_encoding_malformed_csv_and_row_limit_remove_partial_imports(self):
        for content in [b"2345678901\n\xff\xfe\n", b'2345678901\n"unterminated\n']:
            batch = self.upload(content)
            self.assertEqual(batch.status, "invalid")
            self.assertFalse(Number.objects.filter(batch=batch).exists())
        with override_settings(BATCH_MAX_NUMBERS=1):
            batch = self.upload()
            self.assertEqual(batch.status, "invalid")
            self.assertFalse(Number.objects.filter(batch=batch).exists())

    def test_reject_empty_wrong_extension_size_and_open_file_quota(self):
        for file in [SimpleUploadedFile("a.csv", b""), SimpleUploadedFile("a.exe", b"123")]:
            with self.assertRaises(ValueError):
                save_upload(self.user, file)
        with override_settings(BATCH_MAX_UPLOAD_BYTES=2):
            with self.assertRaises(ValueError):
                save_upload(self.user, SimpleUploadedFile("a.csv", b"123"))
        self.upload()
        with override_settings(BATCH_MAX_OPEN_FILES=1):
            with self.assertRaises(ValueError):
                save_upload(self.user, SimpleUploadedFile("a.csv", b"2345678901"))

    def test_pause_finishes_current_number_and_waits_for_next_decision(self):
        first = self.upload()
        second = self.upload()
        claim = claim_number()
        self.assertEqual(claim["batch_id"], first.pk)
        result = control_batch(first.pk, self.user, "pause")
        self.assertTrue(result["needs_next_decision"])
        self.assertEqual(result["next_batch"], str(second.pk))
        first.refresh_from_db()
        self.assertEqual(first.status, "pausing")
        self.assertIsNone(claim_number())
        finish_number(claim, Result("good", "saved", 200, 1, 1))
        first.refresh_from_db()
        self.assertEqual(first.status, "paused")
        self.assertEqual(first.good_rows, 1)
        self.assertIsNone(claim_number())
        continue_queue(self.user, True)
        self.assertEqual(claim_number()["batch_id"], second.pk)

    def test_pause_queued_head_also_requires_decision(self):
        first, second = self.upload(), self.upload()
        response = control_batch(first.pk, self.user, "pause")
        self.assertTrue(response["needs_next_decision"])
        self.assertIsNone(claim_number())
        continue_queue(self.user, True)
        self.assertEqual(claim_number()["batch_id"], second.pk)

    def test_resume_never_repeats_completed_numbers(self):
        batch = self.upload()
        self.step()
        control_batch(batch.pk, self.user, "pause")
        control_batch(batch.pk, self.user, "resume")
        claim = self.step()
        self.assertEqual(Number.objects.get(pk=claim["id"]).sequence, 2)
        self.assertIsNone(claim_number())
        batch.refresh_from_db()
        self.assertEqual(batch.status, "export_pending")
        self.assertEqual(batch.good_rows, 2)

    def test_separate_user_queues_and_single_inflight_request(self):
        first, second = self.upload(), self.upload(user=self.other)
        claim = claim_number()
        self.assertEqual(claim["batch_id"], first.pk)
        self.assertIsNone(claim_number())
        control_batch(first.pk, self.user, "pause")
        finish_number(claim, Result("good", "saved", 200, 1, 1))
        self.assertEqual(claim_number()["batch_id"], second.pk)

    def test_ten_consecutive_errors_pause_every_queue_and_create_one_alert(self):
        batch = self.upload(b"2345678901\n" * 12)
        for _ in range(9):
            self.step("error")
        self.assertFalse(Engine.objects.get().paused)
        self.step("error")
        self.assertTrue(Engine.objects.get().paused)
        self.assertIsNone(claim_number())
        batch.refresh_from_db()
        self.assertEqual(batch.error_rows, 10)
        self.assertEqual(batch.good_rows + batch.bad_rows, 0)
        self.assertEqual(batch.status, "paused")
        self.assertEqual(Alert.objects.count(), 1)
        self.assertEqual(Failure.objects.count(), 10)

    def test_success_resets_consecutive_errors(self):
        self.upload(b"2345678901\n" * 12)
        for _ in range(5):
            self.step("error")
        self.step("good")
        self.assertEqual(Engine.objects.get().consecutive_errors, 0)

    def test_rate_limit_and_abnormal_reply_stop_immediately(self):
        for kind in ["rate_limited", "abnormal"]:
            with self.subTest(kind=kind):
                Engine.objects.all().delete()
                Batch.objects.all().delete()
                self.upload()
                self.step(kind)
                self.assertTrue(Engine.objects.get().paused)

    def test_expired_worker_lease_stops_and_fences_late_result(self):
        batch = self.upload()
        claim = claim_number()
        Engine.objects.update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(claim_number())
        self.assertTrue(Engine.objects.get().paused)
        self.assertFalse(finish_number(claim, Result("good", "late", 200, 1, 1)))
        batch.refresh_from_db()
        self.assertEqual(batch.processed_rows, 0)
        self.assertEqual(Failure.objects.get().kind, "interrupted")

    def test_safety_resume_permission_and_manual_pause_preservation(self):
        manual = self.upload()
        control_batch(manual.pk, self.user, "pause")
        self.upload(user=self.other)
        self.step("rate_limited")
        with self.assertRaises(PermissionError):
            clear_safety(self.user)
        admin = get_user_model().objects.create_superuser("admin", "a@example.test", "Admin-Passphrase-123")
        clear_safety(admin)
        manual.refresh_from_db()
        self.assertEqual(manual.pause_reason, "manual")
        self.assertEqual(manual.status, "paused")

    def test_resume_after_internal_worker_failure_keeps_interrupted_attempt_record(self):
        from batches.queue import pause_all
        self.upload()
        claim = claim_number()
        pause_all("Internal worker failure")
        admin = get_user_model().objects.create_superuser("admin", "a@example.test", "Admin-Passphrase-123")
        with self.assertRaises(ValueError):
            clear_safety(admin)
        Engine.objects.update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        clear_safety(admin)
        self.assertEqual(Number.objects.get(pk=claim["id"]).status, "pending")
        self.assertEqual(Failure.objects.get(number_id=claim["id"]).kind, "interrupted")

    def test_retry_only_unresolved_errors_and_export_exact_columns(self):
        batch = self.upload(b"2345678901\n2345678902\n2345678903\n")
        self.step("good")
        self.step("bad")  # Mixed = BAD.
        self.step("error")
        self.assertIsNone(claim_number())
        batch.refresh_from_db()
        self.assertEqual(batch.status, "review")
        control_batch(batch.pk, self.user, "retry")
        last = self.step("good")
        self.assertEqual(Number.objects.get(pk=last["id"]).sequence, 3)
        self.assertEqual(Number.objects.get(pk=last["id"]).attempts, 2)
        self.assertIsNone(claim_number())
        task = claim_file_task()
        build_export(task)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "completed")
        with export_path(batch.pk).open() as file:
            self.assertEqual(list(csv.reader(file)), [["sqGOOD", "sqBAD"], ["2345678901", ""], ["", "2345678902"], ["2345678903", ""]])
        self.assertEqual(Failure.objects.count(), 1)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/batches/{batch.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any('batches_number' in q["sql"] for q in queries.captured_queries))
        self.assertTrue(b"sqGOOD,sqBAD" in b"".join(response.streaming_content))

    def test_import_recovery_and_stale_file_token(self):
        batch = save_upload(self.user, SimpleUploadedFile("phones.csv", b"2345678901\n"))
        old = claim_file_task()
        Number.objects.create(batch=batch, sequence=1, source_row=1, ani="9999999999")
        Batch.objects.filter(pk=batch.pk).update(task_expires_at=timezone.now() - timedelta(seconds=1))
        recovered = claim_file_task()
        import_csv(recovered)
        self.assertEqual(Number.objects.get(batch=batch).ani, "2345678901")
        from batches.files import LostLease
        with self.assertRaises(LostLease):
            import_csv(old)

    def test_export_cannot_publish_unresolved_or_count_mismatched_file(self):
        batch = self.upload()
        Batch.objects.filter(pk=batch.pk).update(status="export_pending")
        task = claim_file_task()
        build_export(task)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "export_failed")
        self.assertFalse(export_path(batch.pk).exists())

    def test_api_file_ownership_download_readiness_and_csrf(self):
        batch = self.upload()
        self.assertEqual(self.client.get(f"/api/batches/{batch.pk}/download/").status_code, 409)
        self.client.force_login(self.other)
        for suffix in ["", "download/", "numbers/1/"]:
            self.assertEqual(self.client.get(f"/api/batches/{batch.pk}/{suffix}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/batches/{batch.pk}/control/", {"action": "pause"}, content_type="application/json").status_code, 404)
        self.assertEqual(self.client.get("/api/batches/").json()["count"], 0)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post("/api/batches/upload/", {"file": SimpleUploadedFile("a.csv", b"2345678901")}).status_code, 403)

    def test_webhook_unconfigured_and_failed_delivery_never_unpause_engine(self):
        Engine.objects.create(paused=True)
        alert = Alert.objects.create(reason="Test safety stop")
        with override_settings(DISCORD_WEBHOOK_URL=""), patch("batches.notifications.urlopen") as opener:
            self.assertTrue(deliver_one())
            opener.assert_not_called()
        alert.refresh_from_db()
        self.assertEqual(alert.status, "unconfigured")
        self.assertTrue(Engine.objects.get().paused)

    def test_webhook_payload_excludes_phone_and_mentions(self):
        Alert.objects.create(reason="10 consecutive upstream lookup failures.")
        response = io.BytesIO(b"")
        response.code = 204
        with override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/test-token"), patch("batches.notifications.urlopen", return_value=response) as opener:
            deliver_one()
        body = json.loads(opener.call_args.args[0].data)
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertNotIn("2345678901", body["content"])
        self.assertEqual(Alert.objects.get().status, "sent")


class UpstreamTests(TestCase):
    def call(self, data=None, code=200, raw=None):
        response = io.BytesIO(raw if raw is not None else json.dumps(data or EXAMPLE_RESPONSE).encode())
        response.code = code
        with patch("batches.upstream.urlopen", return_value=response):
            return lookup_number("2345678901")

    def test_all_classifications_and_string_values(self):
        for first, second, expected in [(1, 1, "good"), (0, 0, "bad"), (0, 1, "bad"), (1, 0, "bad"), ("1", "1", "good")]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            for index, value in enumerate([first, second]):
                data["results"][index]["result"]["api_response"]["accepted"] = value
            self.assertEqual(self.call(data).kind, expected)

    def test_malformed_missing_duplicate_campaign_boolean_nan_and_wrong_number(self):
        cases = []
        for mutate in [lambda d: d.update(ani="9999999999"), lambda d: d.update(results=[]),
                       lambda d: d["results"].append(d["results"][0]),
                       lambda d: d["results"][0]["result"]["api_response"].update(accepted=True)]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            mutate(data)
            cases.append(data)
        for data in cases:
            self.assertEqual(self.call(data).kind, "abnormal")
        self.assertEqual(self.call(raw=b"<html>bad</html>").kind, "abnormal")
        self.assertEqual(self.call(raw=b'{"ani":NaN}').kind, "abnormal")

    def test_outer_and_inner_rate_limit_and_server_error(self):
        for code, expected in [(429, "rate_limited"), (503, "error"), (403, "abnormal")]:
            self.assertEqual(self.call(code=code, raw=b"error").kind, expected)
        for code, expected in [(429, "rate_limited"), (500, "error")]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            data["results"][0]["result"]["http_code"] = code
            self.assertEqual(self.call(data).kind, expected)
