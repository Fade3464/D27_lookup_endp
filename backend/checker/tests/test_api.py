import copy
import io
import json
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from .upstream import EXAMPLE_RESPONSE
from checker.models import Campaign, Lookup


class UpstreamResponse(io.BytesIO):
    code = 200


class LookupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator", password="Example-Passphrase-123")
        self.other = get_user_model().objects.create_user("other", password="Example-Passphrase-456")
        self.client.force_login(self.user)
        self.campaign = Campaign.objects.get(campaign_id="21625")

    def call(self, payload=None, code=200):
        response = UpstreamResponse(json.dumps(payload if payload is not None else EXAMPLE_RESPONSE).encode())
        response.code = code
        with patch("checker.services.urlopen", return_value=response) as opener:
            result = self.client.post("/api/lookups/", {"ani": "+1 (234) 567-8901", "campaign_id": "21625"}, content_type="application/json")
        return result, opener

    def test_success_stores_complete_response_and_sends_only_ani(self):
        response, opener = self.call()
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "accepted")
        record = Lookup.objects.get(pk=data["id"])
        self.assertEqual(record.response_payload, EXAMPLE_RESPONSE)
        self.assertEqual(json.loads(record.raw_response), EXAMPLE_RESPONSE)
        self.assertEqual(record.selected_result, EXAMPLE_RESPONSE["results"][0])
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.campaign_id_sent, "21625")
        self.assertIsNotNone(record.completed_at)
        request = opener.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"ani": "2345678901"})
        self.assertEqual(request.full_url, "https://one.marstools.co/d27supressioncheck/ping.php")

    def test_second_campaign(self):
        with patch("checker.services.urlopen", return_value=UpstreamResponse(json.dumps(EXAMPLE_RESPONSE).encode())):
            response = self.client.post("/api/lookups/", {"ani": "2345678901", "campaign_id": "21627"}, content_type="application/json")
        self.assertEqual(response.json()["selected_result"]["campaignId"], "21627")

    def test_suppressed_string_value(self):
        payload = copy.deepcopy(EXAMPLE_RESPONSE)
        payload["results"][0]["result"]["api_response"]["accepted"] = "0"
        response, _ = self.call(payload)
        self.assertEqual(response.json()["status"], "suppressed")

    def test_failed_wrapper_is_never_accepted(self):
        payload = copy.deepcopy(EXAMPLE_RESPONSE)
        payload["results"][0]["result"]["ok"] = False
        response, _ = self.call(payload)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(Lookup.objects.count(), 1)

    def test_missing_campaign_or_wrong_ani_is_error_and_retained(self):
        for mutate in [lambda p: p.update(results=[]), lambda p: p.update(ani="9999999999")]:
            payload = copy.deepcopy(EXAMPLE_RESPONSE)
            mutate(payload)
            response, _ = self.call(payload)
            self.assertEqual(response.json()["status"], "error")
            self.assertEqual(response.json()["response_payload"], payload)

    def test_transport_failure_stays_in_history(self):
        with patch("checker.services.urlopen", side_effect=URLError("unreachable")):
            response = self.client.post("/api/lookups/", {"ani": "2345678901", "campaign_id": "21625"}, content_type="application/json")
        self.assertEqual(response.json()["status"], "error")
        self.assertIsNotNone(Lookup.objects.get().completed_at)

    def test_invalid_json_and_http_error_are_recorded(self):
        for upstream in [UpstreamResponse(b"<html>error</html>"), HTTPError("https://example.org", 503, "unavailable", {}, io.BytesIO(b"service down"))]:
            kwargs = {"side_effect": upstream} if isinstance(upstream, HTTPError) else {"return_value": upstream}
            with patch("checker.services.urlopen", **kwargs):
                response = self.client.post("/api/lookups/", {"ani": "2345678901", "campaign_id": "21625"}, content_type="application/json")
            self.assertEqual(response.json()["status"], "error")
            self.assertTrue(response.json()["raw_response"])

    def test_bad_input_and_inactive_campaign_never_call_upstream(self):
        self.campaign.active = False
        self.campaign.save()
        with patch("checker.services.urlopen") as opener:
            for payload in [{"ani": "123", "campaign_id": "21627"}, {"ani": "2345678901", "campaign_id": "21625"}, {"ani": 2345678901, "campaign_id": "21627"}]:
                self.assertEqual(self.client.post("/api/lookups/", payload, content_type="application/json").status_code, 400)
            opener.assert_not_called()
        self.assertEqual(Lookup.objects.count(), 0)

    def test_history_and_details_are_user_scoped(self):
        response, _ = self.call()
        record_id = response.json()["id"]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/history/").json()["count"], 0)
        self.assertEqual(self.client.get(f"/api/history/{record_id}/").status_code, 404)
        self.other.is_staff = True
        self.other.save()
        self.assertEqual(self.client.get("/api/history/").json()["count"], 0)
        self.other.user_permissions.add(Permission.objects.get(codename="view_lookup", content_type__app_label="checker"))
        self.assertEqual(self.client.get("/api/history/").json()["count"], 1)

    def test_history_filters(self):
        self.call()
        self.assertEqual(self.client.get("/api/history/?ani=999").json()["count"], 0)
        self.assertEqual(self.client.get("/api/history/?ani=234&status=accepted").json()["count"], 1)

    def test_anonymous_access_is_denied(self):
        self.client.logout()
        for url in ["/api/history/", "/api/campaigns/"]:
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post("/api/lookups/", {}, content_type="application/json").status_code, 401)

    def test_admin_records_are_read_only(self):
        self.call()
        admin = get_user_model().objects.create_superuser("admin", "admin@example.test", "Admin-Passphrase-123")
        self.client.force_login(admin)
        record = Lookup.objects.get()
        self.assertEqual(self.client.get(f"/admin/checker/lookup/{record.pk}/change/").status_code, 200)
        self.assertEqual(self.client.post(f"/admin/checker/lookup/{record.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/checker/lookup/{record.pk}/change/", {"ani": "9999999999"}).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.ani, "2345678901")


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator", password="Example-Passphrase-123")
        self.client = Client(enforce_csrf_checks=True)

    def token(self):
        return self.client.get("/api/auth/session/").json()["csrf_token"]

    def test_csrf_required_for_login(self):
        self.assertEqual(self.client.post("/api/auth/login/", {"username": "operator", "password": "Example-Passphrase-123"}, content_type="application/json").status_code, 403)

    def test_session_rotation_logout_and_password_change(self):
        before = self.token()
        response = self.client.post("/api/auth/login/", {"username": "operator", "password": "Example-Passphrase-123"}, content_type="application/json", HTTP_X_CSRFTOKEN=before)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.cookies["sessionid"]["httponly"])
        token = response.json()["csrf_token"]
        self.assertNotEqual(before, token)
        response = self.client.post("/api/auth/password/", {"old_password": "Example-Passphrase-123", "new_password": "Updated-Passphrase-987"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/session/").json()["user"]["username"], "operator")
        response = self.client.post("/api/auth/logout/", {}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.client.get("/api/auth/session/").json()["user"])

    def test_login_lockout(self):
        token = self.token()
        for _ in range(5):
            response = self.client.post("/api/auth/login/", {"username": "operator", "password": "wrong"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 429)
        response = self.client.post("/api/auth/login/", {"username": "operator", "password": "Example-Passphrase-123"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 429)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post("/api/auth/login/", {"username": "operator", "password": "Example-Passphrase-123"}, content_type="application/json", HTTP_X_CSRFTOKEN=self.token())
        self.assertEqual(response.status_code, 401)

    def test_weak_password_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/auth/password/", {"old_password": "Example-Passphrase-123", "new_password": "short"}, content_type="application/json", HTTP_X_CSRFTOKEN=self.token())
        self.assertEqual(response.status_code, 400)
