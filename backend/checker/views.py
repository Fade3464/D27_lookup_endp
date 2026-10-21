import json
from functools import wraps

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import Campaign, Lookup
from .services import normalize_ani, perform_lookup


def client_ip(request):
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")


def locked_out(request, credentials=None, *args, **kwargs):
    return JsonResponse({"error": "Too many login attempts. Try again in 15 minutes."}, status=429)


def csrf_failure(request, reason=""):
    return JsonResponse({"error": "Security token expired. Refresh the page and try again."}, status=403)


def authenticated(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Please sign in."}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def read_json(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid JSON body.") from None
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def user_data(user):
    return {"username": user.get_username(), "is_staff": user.is_staff,
            "display_name": user.get_full_name() or user.get_username(),
            "can_resume_safety": user.is_staff and user.has_perm("batches.resume_safety_stop")}


@require_GET
@never_cache
def session(request):
    return JsonResponse({"csrf_token": get_token(request),
                         "user": user_data(request.user) if request.user.is_authenticated else None})


@require_POST
@never_cache
def sign_in(request):
    try:
        data = read_json(request)
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
            raise ValueError("Enter your username and password.")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"error": "Invalid username or password."}, status=401)
    login(request, user)
    return JsonResponse({"user": user_data(user), "csrf_token": get_token(request)})


@require_POST
@authenticated
def sign_out(request):
    logout(request)
    return JsonResponse({"ok": True, "csrf_token": get_token(request)})


@require_POST
@authenticated
def change_password(request):
    try:
        data = read_json(request)
        old, new = data.get("old_password"), data.get("new_password")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("Enter your current and new passwords.")
        if not request.user.check_password(old):
            raise ValueError("Current password is incorrect.")
        validate_password(new, request.user)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    request.user.set_password(new)
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)
    return JsonResponse({"ok": True})


@require_GET
@authenticated
def campaigns(request):
    return JsonResponse({"campaigns": list(Campaign.objects.filter(active=True).values("campaign_id", "label"))})


def serialize(record, detail=False):
    data = {
        "id": str(record.pk), "ani": record.ani,
        "campaign_id": record.campaign_id_sent, "campaign_label": record.campaign_label,
        "username": record.user.get_username(), "status": record.status,
        "created_at": record.created_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "error": record.error,
    }
    if detail:
        data.update(request_payload=record.request_payload, response_payload=record.response_payload,
                    raw_response=record.raw_response, selected_result=record.selected_result,
                    http_code=record.http_code)
    return data


def visible_records(user):
    records = Lookup.objects.select_related("user")
    if user.is_staff and user.has_perm("checker.view_lookup"):
        return records
    return records.filter(user=user)


@require_POST
@authenticated
def lookup(request):
    try:
        data = read_json(request)
        ani = normalize_ani(data.get("ani"))
        campaign_id = data.get("campaign_id")
        if not isinstance(campaign_id, str):
            raise ValueError("Select a campaign.")
        campaign = Campaign.objects.get(campaign_id=campaign_id, active=True)
    except (ValueError, Campaign.DoesNotExist) as exc:
        return JsonResponse({"error": str(exc) if isinstance(exc, ValueError) else "Campaign is unavailable."}, status=400)
    record = perform_lookup(user=request.user, campaign=campaign, ani=ani)
    # Failed upstream attempts still have a persisted record and a detail URL.
    return JsonResponse(serialize(record, detail=True), status=201)


@require_GET
@authenticated
def history(request):
    records = visible_records(request.user)
    if ani := request.GET.get("ani", "").strip():
        records = records.filter(ani__icontains=ani[:20])
    if campaign := request.GET.get("campaign", ""):
        records = records.filter(campaign_id_sent=campaign)
    if status := request.GET.get("status", ""):
        records = records.filter(status=status)
    page = Paginator(records, 20).get_page(request.GET.get("page", "1"))
    return JsonResponse({"results": [serialize(r) for r in page], "count": page.paginator.count,
                         "page": page.number, "pages": page.paginator.num_pages})


@require_GET
@authenticated
def history_detail(request, record_id):
    record = visible_records(request.user).filter(pk=record_id).first()
    if record is None:
        return JsonResponse({"error": "Record not found."}, status=404)
    return JsonResponse(serialize(record, detail=True))
