"""Durable, cached partial exports. HTTP requests never scan phone records."""
import csv
import hashlib
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from .files import LostLease, export_path
from .models import Batch, Event, Number, PartialExport
from .queue import engine_lock


def partial_path(snapshot):
    return export_path(snapshot.batch_id).with_name(f"partial-{snapshot.pk}.csv")


def serialize_partial(snapshot):
    status, error = snapshot.status, snapshot.error
    if status == "ready" and not partial_path(snapshot).is_file():
        status, error = "failed", "Partial CSV is missing. Prepare it again."
    return {"id": str(snapshot.pk), "status": status,
            "resolved_rows": snapshot.resolved_rows, "good_rows": snapshot.good_rows,
            "bad_rows": snapshot.bad_rows, "cutoff_at": snapshot.cutoff_at.isoformat(),
            "error": error,
            "download_url": f"/api/batches/{snapshot.batch_id}/partial-exports/{snapshot.pk}/download/"
                            if status == "ready" else None}


def current_partials(batches):
    # One metadata query: the unique (batch, resolved_rows) index also finds the
    # latest snapshot. Keep it visible when processing resumes and advances.
    ids = {batch.pk for batch in batches}
    if not ids:
        return {}
    latest = (Batch.objects.filter(pk__in=ids).order_by().annotate(snapshot=Subquery(
        PartialExport.objects.filter(batch_id=OuterRef("pk")).order_by("-resolved_rows").values("pk")[:1]))
        .values("snapshot"))
    return {snapshot.batch_id: snapshot for snapshot in PartialExport.objects.filter(pk__in=Subquery(latest))}


def request_partial(batch_id, user):
    with transaction.atomic():
        # finish_number uses this same gate and batch lock. All earlier successful
        # checkpoints are committed before the cutoff and counts are captured.
        engine_lock()
        batch = Batch.objects.select_for_update().exclude(status="deleted").get(pk=batch_id, user=user)
        if batch.status == "pausing":
            raise ValueError("Wait for the current lookup to finish before preparing a partial CSV.")
        if batch.status not in ("paused", "review"):
            raise ValueError("Pause this file before preparing a partial CSV.")
        count = batch.good_rows + batch.bad_rows
        if not count:
            raise ValueError("There are no completed results to export yet.")
        snapshot, created = PartialExport.objects.select_for_update().get_or_create(
            batch=batch, resolved_rows=count,
            defaults={"cutoff_at": timezone.now(), "good_rows": batch.good_rows, "bad_rows": batch.bad_rows})
        if snapshot.status == "failed" or (snapshot.status == "ready" and not partial_path(snapshot).is_file()):
            snapshot.status, snapshot.error = "queued", ""
            snapshot.task_token = snapshot.task_expires_at = None
            snapshot.save(update_fields=["status", "error", "task_token", "task_expires_at"])
            Event.objects.create(batch=batch, actor=user, action="partial_export_retried")
        elif created:
            Event.objects.create(batch=batch, actor=user, action="partial_export_requested",
                                 message=f"Snapshot of {count} resolved numbers.")
        return snapshot


def claim_partial_export():
    now = timezone.now()
    with transaction.atomic():
        snapshot = (PartialExport.objects.select_for_update(of=("self",), skip_locked=True)
                    .exclude(batch__status="deleted")
                    .filter(Q(status="queued") | Q(status="building", task_expires_at__lte=now))
                    .order_by("created_at", "id").first())
        if snapshot is None:
            return None
        snapshot.status, snapshot.task_token = "building", uuid.uuid4()
        snapshot.task_expires_at = now + timedelta(seconds=settings.BATCH_FILE_LEASE_SECONDS)
        snapshot.save(update_fields=["status", "task_token", "task_expires_at"])
        return snapshot


def locked_snapshot(snapshot):
    # API requests lock Batch before PartialExport too. Keep this order for
    # publication and audit-event foreign keys to avoid a resume/request deadlock.
    batch = Batch.objects.select_for_update().only("id", "status").get(pk=snapshot.batch_id)
    current = PartialExport.objects.select_for_update().get(pk=snapshot.pk)
    if (current.status != "building" or current.task_token != snapshot.task_token or
            batch.status == "deleted"):
        raise LostLease()
    return current


def fail_partial_export(snapshot, message):
    with transaction.atomic():
        current = locked_snapshot(snapshot)
        current.status, current.error = "failed", message[:350]
        current.task_token = current.task_expires_at = None
        current.save(update_fields=["status", "error", "task_token", "task_expires_at"])
        Event.objects.create(batch_id=snapshot.batch_id, action="partial_export_failed", message=current.error)


def build_partial_export(snapshot):
    path = partial_path(snapshot)
    temp = path.with_name(f"partial-{snapshot.pk}-{snapshot.task_token}.tmp")
    cursor = count = good = bad = 0
    try:
        with transaction.atomic():
            locked_snapshot(snapshot)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with temp.open("x", encoding="utf-8", newline="") as file:
            os.chmod(temp, 0o644)
            writer = csv.writer(file)
            writer.writerow(["sqGOOD", "sqBAD"])
            while True:
                # Successful classifications are immutable. Retried errors receive
                # a new completed_at and cannot enter an earlier snapshot.
                rows = list(Number.objects.filter(batch_id=snapshot.batch_id, sequence__gt=cursor,
                            status__in=["good", "bad"], completed_at__lte=snapshot.cutoff_at)
                            .order_by("sequence").values_list("sequence", "ani", "status")[:settings.BATCH_CHUNK_SIZE])
                if not rows:
                    break
                for sequence, ani, status in rows:
                    writer.writerow([ani, ""] if status == "good" else ["", ani])
                    good += int(status == "good")
                    bad += int(status == "bad")
                    count += 1
                cursor = rows[-1][0]
                with transaction.atomic():
                    locked_snapshot(snapshot)
                    PartialExport.objects.filter(pk=snapshot.pk).update(
                        task_expires_at=timezone.now() + timedelta(seconds=settings.BATCH_FILE_LEASE_SECONDS))
            file.flush()
            os.fsync(file.fileno())
        if (count, good, bad) != (snapshot.resolved_rows, snapshot.good_rows, snapshot.bad_rows):
            raise ValueError("Snapshot counts did not match saved results. No partial CSV was published.")
        digest = hashlib.sha256()
        with temp.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        with transaction.atomic():
            current = locked_snapshot(snapshot)
            os.replace(temp, path)
            current.status, current.ready_at = "ready", timezone.now()
            current.export_bytes, current.export_sha256 = path.stat().st_size, digest.hexdigest()
            current.task_token = current.task_expires_at = None
            current.error = ""
            current.save()
            Event.objects.create(batch_id=snapshot.batch_id, action="partial_export_ready",
                                 message=f"Partial CSV ready: {count} resolved numbers.")
    except LostLease:
        raise
    except (OSError, ValueError) as exc:
        fail_partial_export(snapshot, "Partial export failed: " + str(exc)[:300])
    finally:
        temp.unlink(missing_ok=True)
