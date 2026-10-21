import uuid
from django.conf import settings
from django.db import models


class IndividualLookup(models.Model):
    """Public checks have private, staff-only audit records; no public history API."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    ani = models.CharField(max_length=10, db_index=True)
    status = models.CharField(max_length=16, default="pending", choices=[
        ("pending", "Pending"), ("allowed", "Allowed"), ("not_allowed", "Not allowed"), ("error", "Unverified")])
    accepted_1 = models.PositiveSmallIntegerField(null=True, blank=True)
    accepted_2 = models.PositiveSmallIntegerField(null=True, blank=True)
    http_code = models.PositiveSmallIntegerField(null=True, blank=True)
    raw_response = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Campaign(models.Model):
    campaign_id = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campaign_id"]

    def __str__(self):
        return f"{self.label} ({self.campaign_id})"


class Lookup(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Good to go"
        SUPPRESSED = "suppressed", "Not allowed"
        ERROR = "error", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT)
    # Keep historical labels even if an administrator renames a campaign.
    campaign_id_sent = models.CharField(max_length=64)
    campaign_label = models.CharField(max_length=120)
    ani = models.CharField(max_length=10, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    request_payload = models.JSONField(default=dict)
    response_payload = models.JSONField(null=True, blank=True)
    raw_response = models.TextField(blank=True)
    selected_result = models.JSONField(null=True, blank=True)
    http_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"]), models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.ani} / {self.campaign_label} / {self.status}"
