import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Batch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Awaiting preprocessing"
        PREPROCESSING = "preprocessing", "Preprocessing"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PAUSING = "pausing", "Finishing current lookup"
        PAUSED = "paused", "Paused"
        REVIEW = "review", "Needs retry"
        EXPORT_PENDING = "export_pending", "Awaiting export"
        EXPORTING = "exporting", "Building export"
        EXPORT_FAILED = "export_failed", "Export failed"
        COMPLETED = "completed", "Completed"
        INVALID = "invalid", "Invalid CSV"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    original_name = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    source_bytes = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UPLOADED)
    pause_reason = models.CharField(max_length=16, blank=True)
    total_rows = models.PositiveBigIntegerField(default=0)
    valid_rows = models.PositiveBigIntegerField(default=0)
    cleaned_rows = models.PositiveBigIntegerField(default=0)
    rejected_rows = models.PositiveBigIntegerField(default=0)
    processed_rows = models.PositiveBigIntegerField(default=0)
    good_rows = models.PositiveBigIntegerField(default=0)
    bad_rows = models.PositiveBigIntegerField(default=0)
    mixed_rows = models.PositiveBigIntegerField(default=0)
    error_rows = models.PositiveBigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    export_bytes = models.PositiveBigIntegerField(default=0)
    export_sha256 = models.CharField(max_length=64, blank=True)
    export_ready_at = models.DateTimeField(null=True, blank=True)
    task_token = models.UUIDField(null=True, blank=True)
    task_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["status", "task_expires_at"]),
        ]


class UserQueue(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    awaiting_decision = models.BooleanField(default=False)
    active_batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.SET_NULL)
    last_served_at = models.DateTimeField(default=timezone.now)


class Number(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        WORKING = "working", "In progress"
        GOOD = "good", "Both accepted"
        BAD = "bad", "One or both rejected"
        ERROR = "error", "Unresolved error"

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    sequence = models.PositiveBigIntegerField()
    source_row = models.PositiveBigIntegerField()
    ani = models.CharField(max_length=10)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    accepted_1 = models.PositiveSmallIntegerField(null=True, blank=True)
    accepted_2 = models.PositiveSmallIntegerField(null=True, blank=True)
    http_code = models.PositiveSmallIntegerField(null=True, blank=True)
    raw_response = models.TextField(blank=True)
    error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "sequence"], name="unique_batch_sequence")]
        indexes = [models.Index(fields=["batch", "status", "sequence"])]


class Engine(models.Model):
    """A single transaction-locked gate serializes upstream requests globally."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    paused = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    consecutive_errors = models.PositiveIntegerField(default=0)
    paused_at = models.DateTimeField(null=True, blank=True)
    next_lookup_at = models.DateTimeField(null=True, blank=True)
    active_number = models.ForeignKey(Number, null=True, blank=True, on_delete=models.SET_NULL)
    active_batch = models.UUIDField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("resume_safety_stop", "Can resume all queues after a safety stop")]


class Failure(models.Model):
    number = models.ForeignKey(Number, on_delete=models.CASCADE)
    attempt = models.PositiveIntegerField()
    kind = models.CharField(max_length=24)
    http_code = models.PositiveSmallIntegerField(null=True, blank=True)
    raw_response = models.TextField(blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Event(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=32)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class Alert(models.Model):
    """Durable webhook outbox; lookup safety never depends on Discord uptime."""
    reason = models.TextField()
    batch_id_at_stop = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
