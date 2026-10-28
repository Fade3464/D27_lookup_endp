import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.utils import timezone
from .models import Lookup
from .transport import lookup_request, open_lookup


def normalize_ani(value):
    if not isinstance(value, str):
        raise ValueError("Enter a phone number as text.")
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Enter a 10-digit phone number, optionally with country code +1.")
    return digits


def perform_lookup(*, user, campaign, ani):
    # Commit the attempt before network I/O, preserving an audit record even if
    # a worker exits during the request. Never hold a DB transaction while waiting.
    record = Lookup.objects.create(
        user=user, campaign=campaign, campaign_id_sent=campaign.campaign_id,
        campaign_label=campaign.label, ani=ani, request_payload={"ani": ani},
    )
    request = lookup_request(ani)
    try:
        try:
            response = open_lookup(request, urlopen)
        except HTTPError as exc:
            response = exc
        with response:
            record.http_code = response.code
            raw = response.read(1024 * 1024 + 1)
        record.raw_response = raw.decode("utf-8", errors="replace")
        if len(raw) > 1024 * 1024:
            raise ValueError("Upstream response exceeded the 1 MB limit.")
        try:
            payload = json.loads(record.raw_response)
        except ValueError:
            raise ValueError("Lookup service returned invalid JSON.") from None
        record.response_payload = payload
        if not 200 <= record.http_code < 300:
            raise ValueError(f"Lookup service returned HTTP {record.http_code}.")
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("Lookup service returned an unexpected response.")
        if payload.get("ani") != ani:
            raise ValueError("Lookup service returned a different phone number.")
        selected = next((r for r in payload["results"] if isinstance(r, dict)
                         and str(r.get("campaignId", r.get("line", ""))) == campaign.campaign_id), None)
        record.selected_result = selected
        if selected is None:
            raise ValueError("No result returned for the selected campaign.")
        result = selected.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError("The selected campaign lookup failed.")
        code = result.get("http_code")
        if not isinstance(code, int) or not 200 <= code < 300:
            raise ValueError("The selected campaign returned an unsuccessful HTTP status.")
        api = result.get("api_response")
        accepted = api.get("accepted") if isinstance(api, dict) else None
        if accepted in (1, "1"):
            record.status = Lookup.Status.ACCEPTED
        elif accepted in (0, "0"):
            record.status = Lookup.Status.SUPPRESSED
        else:
            raise ValueError("Lookup service returned an unknown acceptance value.")
    except (URLError, TimeoutError, OSError):
        record.status = Lookup.Status.ERROR
        record.error = "Could not reach the lookup service. Please try again."
    except ValueError as exc:
        record.status = Lookup.Status.ERROR
        record.error = str(exc)
    finally:
        record.completed_at = timezone.now()
        record.save(update_fields=["status", "response_payload", "raw_response", "selected_result",
                                   "http_code", "error", "completed_at"])
    return record
