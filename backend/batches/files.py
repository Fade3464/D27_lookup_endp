import csv
import hashlib
import io
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Batch, Event, Number
from .queue import engine_lock, queue_lock


class LostLease(Exception):
    pass


def source_path(batch_id):
    return Path(settings.BATCH_DATA_ROOT) / "sources" / str(batch_id) / "input.csv"


def export_path(batch_id):
    return Path(settings.BATCH_DATA_ROOT) / "exports" / str(batch_id) / "result.csv"


def clean_phone(cell):
    cell = cell.strip()
    # Do not turn formulas, exponents, words or arbitrary embedded digits into phones.
    if not cell or not re.fullmatch(r"[+0-9() .\-\t]+", cell):
        return None
    if "+" in cell and (not cell.startswith("+") or cell.count("+") != 1):
        return None
    digits = re.sub(r"[^0-9]", "", cell)
    if cell.startswith("+") and not (len(digits) == 11 and digits.startswith("1")):
        return None
    if re.fullmatch(r"[0-9]+\.[0-9]+", cell):
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def save_upload(user, uploaded):
    if not uploaded.name.lower().endswith(".csv"):
        raise ValueError("Choose a CSV file.")
    if uploaded.size == 0 or uploaded.size > settings.BATCH_MAX_UPLOAD_BYTES:
        raise ValueError(f"CSV must be nonempty and no larger than {settings.BATCH_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    batch_id = uuid.uuid4()
    path = source_path(batch_id)
    path.parent.mkdir(parents=True, mode=0o700)
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("xb") as file:
            os.chmod(path, 0o600)
            for chunk in uploaded.chunks():
                size += len(chunk)
                if size > settings.BATCH_MAX_UPLOAD_BYTES:
                    raise ValueError("CSV exceeded the upload size limit.")
                digest.update(chunk)
                file.write(chunk)
        with transaction.atomic():
            queue_lock(user.pk)
            open_batches = Batch.objects.filter(user=user).exclude(status__in=["completed", "invalid"]).count()
            if open_batches >= settings.BATCH_MAX_OPEN_FILES:
                raise ValueError("Too many unfinished CSV files. Finish existing files before uploading more.")
            batch = Batch.objects.create(id=batch_id, user=user, original_name=Path(uploaded.name).name[:255],
                                         source_sha256=digest.hexdigest(), source_bytes=size)
            Event.objects.create(batch=batch, actor=user, action="uploaded")
        return batch
    except BaseException:
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        raise


def claim_file_task():
    now = timezone.now()
    with transaction.atomic():
        batch = (Batch.objects.select_for_update(skip_locked=True)
                 .filter(Q(status__in=["uploaded", "export_pending"]) |
                         Q(status__in=["preprocessing", "exporting"], task_expires_at__lte=now)).first())
        if not batch:
            return None
        batch.status = "preprocessing" if batch.status in ("uploaded", "preprocessing") else "exporting"
        batch.task_token = uuid.uuid4()
        batch.task_expires_at = now + timedelta(seconds=settings.BATCH_FILE_LEASE_SECONDS)
        batch.save(update_fields=["status", "task_token", "task_expires_at"])
        return batch


def locked_task(batch):
    current = Batch.objects.select_for_update().get(pk=batch.pk)
    if current.task_token != batch.task_token or current.status != batch.status:
        raise LostLease()
    return current


def import_csv(batch):
    stats = {"total_rows": 0, "valid_rows": 0, "cleaned_rows": 0, "rejected_rows": 0}
    pending = []
    csv.field_size_limit(settings.BATCH_CSV_FIELD_LIMIT)
    with transaction.atomic():
        locked_task(batch)
        # A crashed preprocessing task is restarted from the source; no API work
        # can have begun before preprocessing is published as queued.
        Number.objects.filter(batch=batch).delete()
        Batch.objects.filter(pk=batch.pk).update(**stats)

    def flush():
        with transaction.atomic():
            locked_task(batch)
            Number.objects.bulk_create(pending, batch_size=settings.BATCH_CHUNK_SIZE)
            Batch.objects.filter(pk=batch.pk).update(
                **stats, task_expires_at=timezone.now() + timedelta(seconds=settings.BATCH_FILE_LEASE_SECONDS))
        pending.clear()

    try:
        with source_path(batch.pk).open("rb") as binary:
            with io.TextIOWrapper(binary, encoding="utf-8-sig", errors="strict", newline="") as text:
                sample = text.read(8192)
                text.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                except csv.Error:
                    dialect = csv.excel
                for source_row, row in enumerate(csv.reader(text, dialect=dialect, strict=True), 1):
                    stats["total_rows"] += 1
                    if source_row > settings.BATCH_MAX_CSV_RECORDS:
                        raise ValueError("CSV contains too many records.")
                    first = row[0] if row else ""
                    ani = clean_phone(first)
                    if ani is None:
                        stats["rejected_rows"] += 1
                    else:
                        stats["valid_rows"] += 1
                        if stats["valid_rows"] > settings.BATCH_MAX_NUMBERS:
                            raise ValueError("CSV contains too many valid numbers.")
                        stats["cleaned_rows"] += int(first != ani)
                        pending.append(Number(batch_id=batch.pk, sequence=stats["valid_rows"], source_row=source_row, ani=ani))
                    # Flush/checkpoint even when a large file contains only garbage.
                    if len(pending) >= settings.BATCH_CHUNK_SIZE or source_row % settings.BATCH_CHUNK_SIZE == 0:
                        flush()
                flush()
        with transaction.atomic():
            engine = engine_lock()
            queue_lock(batch.user_id)
            current = locked_task(batch)
            current.status = "paused" if engine.paused else "queued"
            current.pause_reason = "safety" if engine.paused else ""
            if not stats["valid_rows"]:
                current.status = "invalid"
                current.error_message = "No valid 10-digit phone numbers were found in the first column. Upload a CSV with phone numbers in column one."
            current.task_token, current.task_expires_at = None, None
            current.save(update_fields=["status", "pause_reason", "error_message", "task_token", "task_expires_at"])
            Event.objects.create(batch=current, action="preprocessed", message=(f"{stats['valid_rows']} valid; "
                f"{stats['cleaned_rows']} cleaned; {stats['rejected_rows']} rejected."))
    except LostLease:
        raise
    except (UnicodeError, csv.Error, ValueError, OSError) as exc:
        with transaction.atomic():
            current = locked_task(batch)
            Number.objects.filter(batch=batch).delete()
            current.status = "invalid"
            current.valid_rows = 0
            current.error_message = "CSV preprocessing failed: " + (
                "Use a valid UTF-8 CSV file." if isinstance(exc, UnicodeError) else str(exc)[:300])
            current.task_token, current.task_expires_at = None, None
            current.save(update_fields=["status", "valid_rows", "error_message", "task_token", "task_expires_at"])
            Event.objects.create(batch=current, action="invalid", message=current.error_message)


def build_export(batch):
    path = export_path(batch.pk)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temp = path.with_name(f"{batch.task_token}.tmp")
    cursor = count = 0
    try:
        if batch.error_rows or batch.good_rows + batch.bad_rows != batch.valid_rows:
            raise ValueError("Export requires a resolved classification for every valid number.")
        with temp.open("x", encoding="utf-8", newline="") as file:
            os.chmod(temp, 0o644)
            writer = csv.writer(file)
            writer.writerow(["sqGOOD", "sqBAD"])
            while True:
                # Keyset pagination bounds memory even with server-side cursors disabled
                # for PgBouncer. Do not use iterator() or OFFSET at this scale.
                rows = list(Number.objects.filter(batch=batch, sequence__gt=cursor)
                            .order_by("sequence").values_list("sequence", "ani", "status")[:settings.BATCH_CHUNK_SIZE])
                if not rows:
                    break
                for sequence, ani, status in rows:
                    if status not in ("good", "bad"):
                        raise ValueError("An unresolved number was found while exporting.")
                    writer.writerow([ani, ""] if status == "good" else ["", ani])
                    count += 1
                cursor = rows[-1][0]
                with transaction.atomic():
                    locked_task(batch)
                    Batch.objects.filter(pk=batch.pk).update(task_expires_at=timezone.now() + timedelta(seconds=settings.BATCH_FILE_LEASE_SECONDS))
            file.flush()
            os.fsync(file.fileno())
        if count != batch.valid_rows:
            raise ValueError("Export row count did not match the validated input count.")
        digest = hashlib.sha256()
        with temp.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        with transaction.atomic():
            current = locked_task(batch)
            os.replace(temp, path)
            current.status = "completed"
            current.export_bytes = path.stat().st_size
            current.export_sha256 = digest.hexdigest()
            current.export_ready_at = current.completed_at = timezone.now()
            current.task_token = current.task_expires_at = None
            current.error_message = ""
            current.save()
            Event.objects.create(batch=current, action="completed", message=f"Export ready: {count} numbers.")
    except LostLease:
        raise
    except (OSError, ValueError) as exc:
        with transaction.atomic():
            current = locked_task(batch)
            current.status, current.error_message = "export_failed", "Export failed: " + str(exc)[:300]
            current.task_token = current.task_expires_at = None
            current.save(update_fields=["status", "error_message", "task_token", "task_expires_at"])
            Event.objects.create(batch=current, action="export_failed", message=current.error_message)
    finally:
        temp.unlink(missing_ok=True)
