"""Validate relay configuration before any requests can leave the app."""
import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

DIRECT_LOOKUP_URL = "https://marstools.co/d27suppressioncheck/ping.php"


def lookup_config(environ):
    url = environ.get("LOOKUP_RELAY_URL", "")
    token = environ.get("LOOKUP_RELAY_TOKEN", "")
    if not url and not token:
        return DIRECT_LOOKUP_URL, ""
    if not url or not token:
        raise ImproperlyConfigured("Set both LOOKUP_RELAY_URL and LOOKUP_RELAY_TOKEN, or leave both blank.")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and parsed.port in (None, 443)
                 and parsed.path == "/lookup" and not parsed.query and not parsed.fragment
                 and not re.search(r"\s", url))
    except ValueError:
        valid = False
    if not valid:
        raise ImproperlyConfigured("LOOKUP_RELAY_URL must be an HTTPS URL ending in /lookup without credentials or query parameters.")
    if not re.fullmatch(r"[\x21-\x7e]{32,256}", token):
        raise ImproperlyConfigured("LOOKUP_RELAY_TOKEN must contain 32–256 printable ASCII characters without spaces.")
    return url, token
