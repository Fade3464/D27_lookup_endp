import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import Alert, Batch, Engine, Event, Failure, Number, UserQueue


def engine_lock():
    Engine.objects.get_or_create(pk=1)
    return Engine.objects.select_for_update().get(pk=1)


def queue_lock(user_id):
    UserQueue.objects.get_or_create(user_id=user_id)
    return UserQueue.objects.select_for_update().get(user_id=user_id)


def stop_engine(engine, reason, batch_id=None):
    """Caller holds the engine lock. Preserve existing manual pauses."""
    if engine.paused:
        return
    engine.paused, engine.reason, engine.paused_at = True, reason, timezone.now()
    engine.save(update_fields=["paused", "reason", "paused_at", "consecutive_errors"])
    Batch.objects.filter(status__in=["queued", "running", "pausing"]).update(status="paused", pause_reason="safety")
    UserQueue.objects.update(active_batch=None)
    Alert.objects.create(reason=reason, batch_id_at_stop=batch_id)
    if batch_id:
        Event.objects.create(batch_id=batch_id, action="safety_stop", message=reason)


def pause_all(reason):
    with transaction.atomic():
        engine = engine_lock()
        stop_engine(engine, reason, engine.active_batch)


def clear_safety(actor):
    if not actor.is_staff or not actor.has_perm("batches.resume_safety_stop"):
        raise PermissionError("Staff safety-resume permission is required.")
    with transaction.atomic():
        engine = engine_lock()
        if not engine.paused:
            return
        if engine.active_number_id and engine.lease_expires_at and engine.lease_expires_at > timezone.now():
            raise ValueError("Wait for the current lookup lease to expire before resuming.")
        if engine.active_number_id:
            interrupted = Number.objects.select_for_update().get(pk=engine.active_number_id)
            if interrupted.status == "working":
                Failure.objects.create(number=interrupted, attempt=interrupted.attempts, kind="interrupted",
                                       message="Staff resumed after a worker failure; previous external outcome is unknown.")
                interrupted.status = "pending"
                interrupted.save(update_fields=["status"])
        engine.paused = False
        engine.reason = ""
        engine.paused_at = None
        engine.consecutive_errors = 0
        engine.active_number = None
        engine.active_batch = None
        engine.lease_token = None
        engine.lease_expires_at = None
        engine.save()
        batch_ids = list(Batch.objects.filter(status="paused", pause_reason="safety").values_list("pk", flat=True))
        Batch.objects.filter(pk__in=batch_ids).update(status="queued", pause_reason="")
        Event.objects.bulk_create([Event(batch_id=pk, actor=actor, action="safety_resumed") for pk in batch_ids])


def control_batch(batch_id, user, action):
    with transaction.atomic():
        engine = engine_lock()
        queue = queue_lock(user.pk)
        batch = Batch.objects.select_for_update().exclude(status="deleted").get(pk=batch_id, user=user)
        if action == "delete":
            # Serialize against worker claims; retain large row sets and audit history.
            # A tombstone avoids loading/deleting up to a million related records.
            if (batch.status not in ("uploaded", "queued", "paused", "review") or
                    (engine.active_batch == batch.pk and engine.active_number_id is not None)):
                raise ValueError("Pause this job and wait for processing to stop before deleting it.")
            if queue.active_batch_id == batch.pk:
                queue.active_batch = None
                queue.save(update_fields=["active_batch"])
            batch.status = "deleted"
            batch.save(update_fields=["status"])
            Event.objects.create(batch=batch, actor=user, action="deleted")
            return {"deleted": True}
        if action == "pause":
            if batch.status not in ("queued", "running", "pausing", "paused", "review"):
                raise ValueError("This file cannot be paused in its current state.")
            if batch.pause_reason == "safety":
                raise ValueError("This file is stopped by the global safety circuit.")
            active = queue.active_batch_id == batch.pk
            if not queue.active_batch_id and queue.enabled:
                head = Batch.objects.filter(user=user, status="queued").first()
                active = bool(head and head.pk == batch.pk)
            in_flight = engine.active_batch == batch.pk and engine.active_number_id is not None
            batch.status = "pausing" if in_flight else "paused"
            batch.pause_reason = "manual"
            if active:
                queue.enabled = False
                queue.awaiting_decision = True
                if not in_flight:
                    queue.active_batch = None
            batch.save(update_fields=["status", "pause_reason"])
            queue.save()
            Event.objects.create(batch=batch, actor=user, action="pause_requested" if in_flight else "paused")
            next_batch = Batch.objects.filter(user=user, status="queued").exclude(pk=batch.pk).first()
            return {"needs_next_decision": active, "next_batch": str(next_batch.pk) if next_batch else None,
                    "next_name": next_batch.original_name if next_batch else None}
        if action not in ("start", "resume", "retry", "retry_export"):
            raise ValueError("Unknown queue action.")
        if action == "retry_export":
            if batch.status != "export_failed":
                raise ValueError("This export does not need a retry.")
            batch.status, batch.error_message = "export_pending", ""
            batch.save(update_fields=["status", "error_message"])
            Event.objects.create(batch=batch, actor=user, action="export_retried")
            return {}
        if engine.paused:
            raise ValueError("All queues are safety-paused. A staff member must review and resume them.")
        if batch.status not in ("queued", "paused", "pausing", "review"):
            raise ValueError("This file cannot be started in its current state.")
        if action == "retry":
            if batch.status == "pausing":
                raise ValueError("Wait for the current lookup to finish before retrying errors.")
            count = Number.objects.filter(batch=batch, status="error").update(status="pending")
            batch.processed_rows -= count
            batch.error_rows -= count
        elif batch.status == "review":
            raise ValueError("Use Retry Errors to retry unresolved numbers.")
        batch.status = "running" if engine.active_batch == batch.pk and engine.active_number_id else "queued"
        batch.pause_reason = ""
        batch.error_message = ""
        batch.save(update_fields=["status", "pause_reason", "error_message", "processed_rows", "error_rows"])
        queue.enabled, queue.awaiting_decision = True, False
        queue.save()
        Event.objects.create(batch=batch, actor=user, action=action)
        return {}


def continue_queue(user, start_next):
    with transaction.atomic():
        engine = engine_lock()
        queue = queue_lock(user.pk)
        if engine.paused and start_next:
            raise ValueError("All queues are safety-paused.")
        queue.enabled, queue.awaiting_decision = start_next, False
        queue.save(update_fields=["enabled", "awaiting_decision"])


def claim_number():
    now = timezone.now()
    with transaction.atomic():
        engine = engine_lock()
        if engine.paused:
            return None
        if engine.active_number_id:
            if engine.lease_expires_at and engine.lease_expires_at > now:
                return None
            # An expired lease is an uncertain external request, never a silent retry.
            number = Number.objects.select_for_update().get(pk=engine.active_number_id)
            Failure.objects.create(number=number, attempt=number.attempts, kind="interrupted",
                                   message="Worker lease expired; outcome of external request is unknown.")
            number.status = "pending"
            number.save(update_fields=["status"])
            stop_engine(engine, "Lookup worker lost its lease. Review before resuming.", engine.active_batch)
            engine.active_number = None
            engine.active_batch = None
            engine.lease_token = None
            engine.lease_expires_at = None
            engine.save()
            return None
        if engine.next_lookup_at and engine.next_lookup_at > now:
            return None
        ready = Batch.objects.filter(user_id=OuterRef("user_id"), status__in=["queued", "running"])
        queue = (UserQueue.objects.select_for_update().filter(enabled=True, awaiting_decision=False)
                 .filter(Exists(ready)).order_by("last_served_at", "user_id").first())
        if not queue:
            return None
        batch = Batch.objects.select_for_update().filter(pk=queue.active_batch_id, user_id=queue.user_id, status="running").first()
        if not batch:
            batch = Batch.objects.select_for_update().filter(user_id=queue.user_id, status="queued").first()
        if not batch:
            return None
        queue.active_batch, queue.last_served_at = batch, now
        queue.save(update_fields=["active_batch", "last_served_at"])
        if batch.status != "running":
            batch.status = "running"
            batch.started_at = batch.started_at or now
            batch.save(update_fields=["status", "started_at"])
            Event.objects.create(batch=batch, action="started")
        number = Number.objects.select_for_update().filter(batch=batch, status="pending").order_by("sequence").first()
        if not number:
            batch.status = "review" if batch.error_rows else "export_pending"
            batch.error_message = f"{batch.error_rows} numbers need retry." if batch.error_rows else ""
            batch.save(update_fields=["status", "error_message"])
            queue.active_batch = None
            queue.save(update_fields=["active_batch"])
            Event.objects.create(batch=batch, action=batch.status)
            return None
        token = uuid.uuid4()
        number.status, number.attempts = "working", number.attempts + 1
        number.save(update_fields=["status", "attempts"])
        engine.active_number, engine.active_batch, engine.lease_token = number, batch.pk, token
        engine.lease_expires_at = now + timedelta(seconds=settings.LOOKUP_TIMEOUT + 60)
        engine.next_lookup_at = now + timedelta(seconds=settings.BATCH_LOOKUP_INTERVAL)
        engine.save()
        return {"id": number.pk, "ani": number.ani, "batch_id": batch.pk, "token": token}


def finish_number(claim, result):
    with transaction.atomic():
        engine = engine_lock()
        if engine.lease_token != claim["token"] or engine.active_number_id != claim["id"]:
            return False  # Fencing prevents a late worker overwriting recovered work.
        number = Number.objects.select_for_update().get(pk=claim["id"])
        batch = Batch.objects.select_for_update().get(pk=number.batch_id)
        queue = queue_lock(batch.user_id)
        number.raw_response, number.http_code = result.raw, result.http_code
        number.accepted_1, number.accepted_2 = result.accepted_1, result.accepted_2
        number.completed_at, number.error = timezone.now(), result.message
        batch.processed_rows += 1
        if result.kind in ("good", "bad"):
            number.status = result.kind
            if result.kind == "good":
                batch.good_rows += 1
            else:
                batch.bad_rows += 1
                batch.mixed_rows += int(result.accepted_1 != result.accepted_2)
            engine.consecutive_errors = 0
        else:
            number.status = "error"
            batch.error_rows += 1
            engine.consecutive_errors += 1
            Failure.objects.create(number=number, attempt=number.attempts, kind=result.kind,
                                   raw_response=result.raw, http_code=result.http_code, message=result.message)
        number.save()
        if batch.status == "pausing":
            batch.status = "paused"
            queue.active_batch = None
            queue.save(update_fields=["active_batch"])
            Event.objects.create(batch=batch, action="paused", message="Current lookup finished; checkpoint saved.")
        batch.save()
        engine.active_number = None
        engine.active_batch = None
        engine.lease_token = None
        engine.lease_expires_at = None
        engine.save()
        if result.kind in ("abnormal", "rate_limited") or engine.consecutive_errors >= settings.BATCH_ERROR_LIMIT:
            reason = result.message if result.kind != "error" else f"{engine.consecutive_errors} consecutive upstream lookup failures."
            stop_engine(engine, reason, batch.pk)
        return True
