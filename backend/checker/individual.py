"""Anonymous single-number checks. CSRF and reverse-proxy rate limits still apply."""
import logging

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from batches.files import clean_phone
from batches.models import Engine
from batches.upstream import Result, lookup_number
from .models import IndividualLookup
from .views import read_json

logger = logging.getLogger(__name__)


@require_GET
@never_cache
def token(request):
    return JsonResponse({"csrf_token": get_token(request)})


@require_POST
@never_cache
def check(request):
    try:
        value = read_json(request).get("ani")
        ani = clean_phone(value) if isinstance(value, str) and len(value) <= 40 else None
        if ani is None:
            raise ValueError("Enter a 10-digit phone number. A leading +1 and phone formatting are welcome.")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if Engine.objects.filter(pk=1, paused=True).exists():
        return JsonResponse({"error": "D27 checks are temporarily paused. Please try again later."}, status=503)
    record = IndividualLookup.objects.create(ani=ani, user=request.user if request.user.is_authenticated else None)
    try:
        result = lookup_number(ani)
    except Exception:
        # Preserve the attempt even for an unexpected transport failure. Never return an allowance.
        logger.exception("Individual lookup transport failure; record=%s", record.pk)
        result = Result("error", message="Unexpected lookup transport failure.")
    record.status = "allowed" if result.kind == "good" else "not_allowed" if result.kind == "bad" else "error"
    record.accepted_1, record.accepted_2 = result.accepted_1, result.accepted_2
    record.http_code, record.raw_response, record.error = result.http_code, result.raw, result.message
    record.completed_at = timezone.now()
    record.save()
    if record.status == "error":
        response = JsonResponse({"error": "We couldn’t verify this number. Please try again shortly."},
                                status=429 if result.kind == "rate_limited" else 502)
        if result.kind == "rate_limited":
            response["Retry-After"] = "60"
        return response
    return JsonResponse({"id": str(record.pk), "ani": ani, "status": record.status,
                         "accepted_1": record.accepted_1, "accepted_2": record.accepted_2,
                         "completed_at": record.completed_at.isoformat()})
