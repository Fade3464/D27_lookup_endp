import copy
import io
import json
from urllib.error import URLError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from batches.models import Engine
from checker.models import IndividualLookup
from checker.tests.upstream import EXAMPLE_RESPONSE


class IndividualTests(TestCase):
    def post(self, ani="2345678901"):
        return self.client.post("/api/individual/check/", {"ani": ani}, content_type="application/json")

    def upstream(self, data=None, code=200):
        response = io.BytesIO(json.dumps(data if data is not None else EXAMPLE_RESPONSE).encode())
        response.code = code
        return response

    def test_anonymous_checks_all_verdict_combinations_and_private_audit(self):
        for a, b in [(1, 1), (1, 0), (0, 1), (0, 0), ("1", "1")]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            for row, value in zip(data["results"], [a, b]):
                row["result"]["api_response"]["accepted"] = value
            with self.subTest(a=a, b=b), patch("batches.upstream.urlopen", return_value=self.upstream(data)) as send:
                response = self.post()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "allowed" if int(a) == int(b) == 1 else "not_allowed")
                self.assertEqual(json.loads(send.call_args.args[0].data), {"ani": "2345678901"})
                record = IndividualLookup.objects.get(pk=response.json()["id"])
                self.assertIsNone(record.user)
                self.assertEqual(json.loads(record.raw_response), data)
                self.assertIsNotNone(record.completed_at)
                self.assertNotIn("raw_response", response.json())
        for route in ["/api/history/", "/api/lookups/", "/api/batches/"]:
            response = self.client.post(route, {}, content_type="application/json") if "lookups" in route else self.client.get(route)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/individual/check/").status_code, 405)
        self.assertEqual(self.client.get("/api/individual/history/").status_code, 404)

    def test_normalizes_plus_one_and_formats_preserves_leading_zero(self):
        for value, ani in [(" +1 (234) 567-8901 ", "2345678901"), ("234.567.8901", "2345678901"), ("0123456789", "0123456789")]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            data["ani"] = ani
            with self.subTest(value=value), patch("batches.upstream.urlopen", return_value=self.upstream(data)) as send:
                self.assertEqual(self.post(value).json()["ani"], ani)
                self.assertEqual(json.loads(send.call_args.args[0].data)["ani"], ani)

    @patch("checker.individual.lookup_number")
    def test_invalid_input_does_not_call_upstream_or_create_records(self, send):
        for value in [None, 2345678901, [], {}, "", "123", "23456789012", "garbage2345678901", "+44 2345678901", "+2345678901", "2345678901e0", "12345.67890", "１２３４５６７８９０", "1" * 41, "++1 2345678901"]:
            with self.subTest(value=value):
                self.assertEqual(self.post(value).status_code, 400)
        for body in ["{", "[]", "null"]:
            self.assertEqual(self.client.post("/api/individual/check/", body, content_type="application/json").status_code, 400)
        send.assert_not_called()
        self.assertFalse(IndividualLookup.objects.exists())

    def test_csrf_required_even_without_login(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/individual/check/", {"ani": "2345678901"}, content_type="application/json").status_code, 403)
        response = client.get("/api/individual/session/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("user", response.json())
        self.assertIn("no-store", response["Cache-Control"])
        with patch("batches.upstream.urlopen", return_value=self.upstream()):
            response = client.post("/api/individual/check/", {"ani": "2345678901"}, content_type="application/json", HTTP_X_CSRFTOKEN=response.json()["csrf_token"])
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])

    def test_invalid_upstream_never_grants_allowance(self):
        payloads = []
        for value in [True, None, 1.0, 2, "success"]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            data["results"][1]["result"]["api_response"]["accepted"] = value
            payloads.append(data)
        for mutate in [lambda d: d.update(ani="3456789012"), lambda d: d["results"].pop(), lambda d: d["results"].append(d["results"][0]), lambda d: d["results"][0]["result"].update(ok=False)]:
            data = copy.deepcopy(EXAMPLE_RESPONSE)
            mutate(data)
            payloads.append(data)
        for data in payloads:
            with self.subTest(data=data), patch("batches.upstream.urlopen", return_value=self.upstream(data)):
                response = self.post()
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("status", response.json())
                self.assertEqual(IndividualLookup.objects.first().status, "error")
        response = io.BytesIO(b"not json")
        response.code = 200
        with patch("batches.upstream.urlopen", return_value=response):
            self.assertEqual(self.post().status_code, 502)

    def test_transport_errors_and_rate_limits_are_unverified(self):
        for error in [TimeoutError(), URLError("offline")]:
            with self.subTest(error=error), patch("batches.upstream.urlopen", side_effect=error):
                self.assertEqual(self.post().status_code, 502)
                self.assertEqual(IndividualLookup.objects.first().status, "error")
        for code in [429, 503, 403]:
            with self.subTest(code=code), patch("batches.upstream.urlopen", return_value=self.upstream(code=code)):
                response = self.post()
                self.assertEqual(response.status_code, 429 if code == 429 else 502)
                self.assertEqual(IndividualLookup.objects.first().http_code, code)
                if code == 429:
                    self.assertEqual(response["Retry-After"], "60")
        data = copy.deepcopy(EXAMPLE_RESPONSE)
        data["results"][0]["result"]["http_code"] = 429
        with patch("batches.upstream.urlopen", return_value=self.upstream(data)):
            self.assertEqual(self.post().status_code, 429)

    @patch("checker.individual.lookup_number")
    def test_safety_pause_prevents_public_checks(self, send):
        Engine.objects.update_or_create(pk=1, defaults={"paused": True})
        self.assertEqual(self.post().status_code, 503)
        send.assert_not_called()
        self.assertFalse(IndividualLookup.objects.exists())

    def test_signed_in_check_is_attributed(self):
        user = get_user_model().objects.create_user("individual-operator")
        self.client.force_login(user)
        with patch("batches.upstream.urlopen", return_value=self.upstream()):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(IndividualLookup.objects.get().user, user)
