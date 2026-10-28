import copy
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from batches.upstream import lookup_number
from checker.models import Campaign, IndividualLookup, Lookup
from checker.services import perform_lookup
from checker.transport import NoRedirects, lookup_request, open_lookup
from config.lookup import DIRECT_LOOKUP_URL, lookup_config
from .upstream import EXAMPLE_RESPONSE

TOKEN = "test-only-secret-" + "a" * 48
RELAY_URL = "https://example.workers.dev/lookup"


def response(payload=EXAMPLE_RESPONSE, code=200):
    result = io.BytesIO(json.dumps(payload).encode())
    result.code = code
    return result


class ConfigurationTests(SimpleTestCase):
    def test_direct_and_relay_configuration(self):
        self.assertEqual(lookup_config({}), (DIRECT_LOOKUP_URL, ""))
        self.assertEqual(lookup_config({"LOOKUP_RELAY_URL": RELAY_URL, "LOOKUP_RELAY_TOKEN": TOKEN}), (RELAY_URL, TOKEN))

    def test_invalid_configuration_rejected_without_disclosing_token(self):
        for url, token in [(RELAY_URL, ""), ("", TOKEN), (RELAY_URL, "short"),
                           (RELAY_URL, TOKEN + "\n"), (RELAY_URL, "x" * 257),
                           ("http://example.org/lookup", TOKEN),
                           ("https://user:password@example.org/lookup", TOKEN),
                           (RELAY_URL + "?target=other", TOKEN), (RELAY_URL + "#fragment", TOKEN),
                           ("https://example.org:8000/lookup", TOKEN),
                           ("https://example.org/ping.php", TOKEN)]:
            with self.subTest(url=url):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    lookup_config({"LOOKUP_RELAY_URL": url, "LOOKUP_RELAY_TOKEN": token})
                self.assertNotIn(TOKEN, str(caught.exception))

    @override_settings(LOOKUP_URL=DIRECT_LOOKUP_URL, LOOKUP_RELAY_TOKEN="")
    def test_direct_transport_has_no_auth(self):
        request = lookup_request("0123456789")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_header("User-agent"), "D27Desk/1.0")
        direct = Mock(return_value="response")
        self.assertEqual(open_lookup(request, direct), "response")
        direct.assert_called_once_with(request, timeout=20)

    def test_real_redirect_is_not_followed(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                paths.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/must-not-receive-token")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{server.server_port}/lookup", data=b"{}")
            request.add_unredirected_header("Authorization", f"Bearer {TOKEN}")
            with self.assertRaises(HTTPError) as caught:
                build_opener(NoRedirects()).open(request, timeout=2)
            self.assertEqual(caught.exception.code, 307)
            caught.exception.close()
            self.assertEqual(paths, ["/lookup"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@override_settings(LOOKUP_URL=RELAY_URL, LOOKUP_RELAY_TOKEN=TOKEN)
class RelayTests(TestCase):
    def assert_request(self, send):
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, RELAY_URL)
        self.assertEqual(request.get_header("User-agent"), "D27Desk/1.0")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(json.loads(request.data), {"ani": "2345678901"})
        self.assertNotIn("Authorization", request.headers)

    def test_public_check_uses_relay_and_keeps_token_out_of_audit(self):
        for accepted, status in [(1, "allowed"), (0, "not_allowed")]:
            payload = copy.deepcopy(EXAMPLE_RESPONSE)
            payload["results"][1]["result"]["api_response"]["accepted"] = accepted
            with patch("checker.transport.relay_opener.open", return_value=response(payload)) as send:
                result = self.client.post("/api/individual/check/", {"ani": "+1 2345678901"}, content_type="application/json")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["status"], status)
            self.assert_request(send)
        for record in IndividualLookup.objects.all():
            self.assertNotIn(TOKEN, record.raw_response)
        self.assertNotIn(TOKEN, result.content.decode())

    def test_campaign_lookup_uses_same_relay(self):
        user = get_user_model().objects.create_user("relay-operator")
        campaign = Campaign.objects.get(campaign_id="21625")
        with patch("checker.transport.relay_opener.open", return_value=response()) as send:
            record = perform_lookup(user=user, campaign=campaign, ani="2345678901")
        self.assertEqual(record.status, "accepted")
        self.assert_request(send)
        self.assertNotIn(TOKEN, json.dumps(Lookup.objects.values().get(), default=str))

    def test_batch_transport_errors_never_fall_back(self):
        for code, kind in [(401, "abnormal"), (403, "abnormal"), (307, "abnormal"),
                           (429, "rate_limited"), (502, "error"), (504, "error")]:
            with self.subTest(code=code), patch("checker.transport.relay_opener.open", return_value=response({}, code)) as relay, patch("batches.upstream.urlopen") as direct:
                result = lookup_number("2345678901")
                self.assertEqual(result.kind, kind)
                relay.assert_called_once()
                direct.assert_not_called()
        with patch("checker.transport.relay_opener.open", side_effect=URLError("unavailable")), patch("batches.upstream.urlopen") as direct:
            self.assertEqual(lookup_number("2345678901").kind, "error")
            direct.assert_not_called()

    def test_invalid_relay_response_never_allows_number(self):
        for payload in [{}, {"ani": "2345678901", "results": []}, "Hello World!"]:
            with self.subTest(payload=payload), patch("checker.transport.relay_opener.open", return_value=response(payload)):
                result = self.client.post("/api/individual/check/", {"ani": "2345678901"}, content_type="application/json")
                self.assertEqual(result.status_code, 502)
