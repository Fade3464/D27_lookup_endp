"""Shared lookup transport. Relay credentials never enter audit payloads."""
import json
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


relay_opener = build_opener(NoRedirects())


def lookup_request(ani):
    request = Request(settings.LOOKUP_URL, data=json.dumps({"ani": ani}).encode(), method="POST",
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    if settings.LOOKUP_RELAY_TOKEN:
        request.add_unredirected_header("Authorization", f"Bearer {settings.LOOKUP_RELAY_TOKEN}")
    return request


def open_lookup(request, direct_open):
    # A configured relay must never silently fall back to direct server egress.
    opener = relay_opener.open if settings.LOOKUP_RELAY_TOKEN else direct_open
    return opener(request, timeout=settings.LOOKUP_TIMEOUT)
