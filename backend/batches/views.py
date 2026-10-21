from django.conf import settings
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from checker.views import authenticated, read_json
from .files import export_path, save_upload
from .models import Alert, Batch, Engine, Event, Failure, Number, UserQueue
from .queue import clear_safety, continue_queue, control_batch


def serialize(batch):
    resolved = batch.good_rows + batch.bad_rows
    return {
        "id": str(batch.pk), "name": batch.original_name, "status": batch.status,
        "pause_reason": batch.pause_reason, "total_rows": batch.total_rows,
        "valid_rows": batch.valid_rows, "cleaned_rows": batch.cleaned_rows, "rejected_rows": batch.rejected_rows,
        "processed_rows": batch.processed_rows, "resolved_rows": resolved,
        "good_rows": batch.good_rows, "bad_rows": batch.bad_rows, "mixed_rows": batch.mixed_rows,
        "error_rows": batch.error_rows, "progress": round(resolved * 100 / batch.valid_rows, 2) if batch.valid_rows else 0,
        "error": batch.error_message, "created_at": batch.created_at.isoformat(),
        "export_bytes": batch.export_bytes, "export_sha256": batch.export_sha256,
        "download_url": f"/api/batches/{batch.pk}/download/" if batch.status == "completed" else None,
    }


@require_POST
@authenticated
def upload(request):
    if len(request.FILES) != 1 or "file" not in request.FILES or len(request.FILES.getlist("file")) != 1:
        return JsonResponse({"error": "Upload exactly one CSV in the file field."}, status=400)
    try:
        batch = save_upload(request.user, request.FILES["file"])
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except OSError:
        return JsonResponse({"error": "The file could not be stored. Please try again."}, status=503)
    return JsonResponse(serialize(batch), status=202)


@require_GET
@authenticated
def dashboard(request):
    jobs = Batch.objects.filter(user=request.user).order_by("-created_at", "-id")
    page = Paginator(jobs, 30).get_page(request.GET.get("page", "1"))
    queue = UserQueue.objects.filter(user=request.user).first()
    engine = Engine.objects.filter(pk=1).first()
    alert = Alert.objects.order_by("-id").first()
    return JsonResponse({"results": [serialize(b) for b in page], "count": page.paginator.count,
                         "active": [serialize(b) for b in Batch.objects.filter(user=request.user)
                                    .exclude(status__in=["completed", "invalid"])[:settings.BATCH_MAX_OPEN_FILES]],
                         "page": page.number, "pages": page.paginator.num_pages,
                         "queue": {"enabled": queue.enabled if queue else True,
                                   "awaiting_decision": queue.awaiting_decision if queue else False},
                         "safety": {"paused": engine.paused if engine else False,
                                    "reason": engine.reason if engine else "",
                                    "notification": alert.status if alert else None,
                                    "notification_error": alert.last_error if alert else "",
                                    "discord_configured": bool(settings.DISCORD_WEBHOOK_URL)}})


@require_POST
@authenticated
def control(request, batch_id):
    try:
        data = read_json(request)
        result = control_batch(batch_id, request.user, data.get("action"))
    except Batch.DoesNotExist:
        return JsonResponse({"error": "File not found."}, status=404)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse(result)


@require_POST
@authenticated
def continue_next(request):
    try:
        data = read_json(request)
        if type(data.get("start_next")) is not bool:
            raise ValueError("Specify whether the next file should start.")
        continue_queue(request.user, data["start_next"])
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"ok": True})


@require_POST
@authenticated
def resume_safety(request):
    try:
        clear_safety(request.user)
    except PermissionError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"ok": True})


@require_GET
@authenticated
def details(request, batch_id):
    batch = Batch.objects.filter(pk=batch_id, user=request.user).first()
    if not batch:
        return JsonResponse({"error": "File not found."}, status=404)
    try:
        after = max(0, int(request.GET.get("after", "0")))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid row cursor."}, status=400)
    rows = list(Number.objects.filter(batch=batch, sequence__gt=after).order_by("sequence").values(
        "sequence", "source_row", "ani", "status", "attempts", "accepted_1", "accepted_2", "http_code", "error")[:100])
    events = list(Event.objects.filter(batch=batch).values("action", "message", "created_at")[:50])
    return JsonResponse({"batch": serialize(batch), "rows": rows, "next_cursor": rows[-1]["sequence"] if len(rows) == 100 else None, "events": events})


@require_GET
@authenticated
def number_detail(request, batch_id, sequence):
    batch = Batch.objects.filter(pk=batch_id, user=request.user).first()
    if not batch:
        return JsonResponse({"error": "File not found."}, status=404)
    row = Number.objects.filter(batch=batch, sequence=sequence).values().first()
    if not row:
        return JsonResponse({"error": "Number not found."}, status=404)
    row["batch_id"] = str(batch_id)
    failures = list(Failure.objects.filter(number_id=row["id"]).order_by("-id").values(
        "attempt", "kind", "http_code", "raw_response", "message", "created_at")[:50])
    return JsonResponse({"number": row, "failures": failures})


@require_GET
@authenticated
def download(request, batch_id):
    batch = Batch.objects.filter(pk=batch_id, user=request.user).first()
    if not batch:
        return JsonResponse({"error": "File not found."}, status=404)
    if batch.status != "completed":
        return JsonResponse({"error": "The export is not ready."}, status=409)
    path = export_path(batch.pk)
    if not path.is_file():
        return JsonResponse({"error": "The export file is missing. Contact an administrator."}, status=503)
    if settings.BATCH_ACCEL_DOWNLOADS:
        response = HttpResponse(content_type="text/csv")
        response["X-Accel-Redirect"] = f"/protected-batch-exports/{batch.pk}/result.csv"
        response["Content-Disposition"] = f'attachment; filename="d27-{batch.pk}.csv"'
    else:
        response = FileResponse(path.open("rb"), as_attachment=True, filename=f"d27-{batch.pk}.csv", content_type="text/csv")
    response["ETag"] = f'"{batch.export_sha256}"'
    Event.objects.create(batch=batch, actor=request.user, action="downloaded")
    return response
