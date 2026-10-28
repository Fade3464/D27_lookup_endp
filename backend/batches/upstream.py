"""One request checks both campaigns. No joins or per-campaign HTTP calls."""
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings
from checker.transport import lookup_request, open_lookup


@dataclass
class Result:
    kind: str
    raw: str = ""
    http_code: int | None = None
    accepted_1: int | None = None
    accepted_2: int | None = None
    message: str = ""


def _invalid_constant(value):
    raise ValueError("Non-JSON numeric constant")


def lookup_number(ani):
    request = lookup_request(ani)
    raw, code = "", None
    try:
        try:
            response = open_lookup(request, urlopen)
        except HTTPError as exc:
            response = exc
        with response:
            code = response.code
            body = response.read(settings.BATCH_MAX_RESPONSE_BYTES + 1)
        raw = body.decode("utf-8", errors="replace")
        if code == 429:
            return Result("rate_limited", raw, code, message="Lookup service returned HTTP 429.")
        if 500 <= code <= 599:
            return Result("error", raw, code, message=f"Lookup service returned HTTP {code}.")
        if not 200 <= code <= 299:
            return Result("abnormal", raw, code, message=f"Unexpected upstream HTTP {code}.")
        if len(body) > settings.BATCH_MAX_RESPONSE_BYTES:
            raise ValueError("Upstream response exceeded the size limit.")
        try:
            data = json.loads(raw, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError):
            raise ValueError("Lookup service returned invalid JSON.") from None
        if not isinstance(data, dict) or data.get("ani") != ani or not isinstance(data.get("results"), list):
            raise ValueError("Lookup service returned an unexpected ANI or response structure.")
        accepted = []
        for campaign_id in ("21625", "21627"):
            rows = [r for r in data["results"] if isinstance(r, dict)
                    and str(r.get("campaignId", r.get("line", ""))) == campaign_id]
            if len(rows) != 1 or not isinstance(rows[0].get("result"), dict):
                raise ValueError(f"Expected exactly one result for campaign {campaign_id}.")
            result = rows[0]["result"]
            inner_code = result.get("http_code")
            if inner_code == 429:
                return Result("rate_limited", raw, code, message=f"Campaign {campaign_id} returned HTTP 429.")
            if isinstance(inner_code, int) and 500 <= inner_code <= 599:
                return Result("error", raw, code, message=f"Campaign {campaign_id} returned HTTP {inner_code}.")
            if result.get("ok") is not True or type(inner_code) is not int or not 200 <= inner_code <= 299:
                raise ValueError(f"Campaign {campaign_id} returned an unsuccessful or invalid result.")
            api = result.get("api_response")
            value = api.get("accepted") if isinstance(api, dict) else None
            if type(value) not in (int, str) or value not in (0, 1, "0", "1"):
                raise ValueError(f"Campaign {campaign_id} returned an unknown acceptance value.")
            accepted.append(int(value))
        # Mixed results are BAD, as requested. Unresolved errors are never BAD.
        return Result("good" if accepted == [1, 1] else "bad", raw, code, *accepted)
    except (URLError, TimeoutError, OSError):
        return Result("error", raw, code, message="Could not reach the lookup service.")
    except ValueError as exc:
        return Result("abnormal", raw, code, message=str(exc))
