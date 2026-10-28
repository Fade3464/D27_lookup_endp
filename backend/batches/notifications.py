import json
import re
import uuid
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Alert


def deliver_one():
    now = timezone.now()
    with transaction.atomic():
        alert = (Alert.objects.select_for_update(skip_locked=True).exclude(status="sent")
                 .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
                 .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)).order_by("id").first())
        if not alert:
            return False
        token = alert.lease_token = uuid.uuid4()
        alert.lease_expires_at = now + timedelta(seconds=60)
        alert.attempts += 1
        alert.save()
    status, error, delay = "failed", "Discord notification failed; retry scheduled.", min(3600, 2 ** min(alert.attempts, 10) * 5)
    webhook = settings.DISCORD_WEBHOOK_URL
    parts = urlsplit(webhook)
    if not webhook:
        status, error, delay = "unconfigured", "DISCORD_WEBHOOK_URL is not configured.", 60
    elif (parts.scheme != "https" or parts.hostname != "discord.com" or parts.username or parts.password
          or not re.fullmatch(r"/api/(?:v[0-9]+/)?webhooks/[0-9]+/[A-Za-z0-9._-]+", parts.path)):
        error, delay = "Invalid Discord webhook configuration.", 300
    else:
        # No ANI, CSV filename, response body, credential, or @mention is sent.
        payload = {"content": (f"D27 safety stop #{alert.pk}\nAll lookup queues are paused.\n"
                               f"Reason: {alert.reason}\nBatch: {alert.batch_id_at_stop or 'engine'}\n"
                               "Review the dashboard before resuming.")[:1800], "allowed_mentions": {"parse": []}}
        request = Request(webhook, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                if not 200 <= response.code < 300:
                    raise ValueError("Unexpected webhook status")
            status, error = "sent", ""
        except HTTPError as exc:
            error = f"Discord returned HTTP {exc.code}; retry scheduled."
            if exc.code == 429:
                try:
                    wait = json.loads(exc.read(16384)).get("retry_after", 60)
                    delay = min(3600, max(1, float(wait)))
                except (ValueError, TypeError, AttributeError):
                    delay = 60
        except (URLError, TimeoutError, OSError):
            error = "Could not connect to Discord; check DNS, TLS and outbound HTTPS. Retry scheduled."
        except ValueError:
            error = "Discord returned an unexpected response; retry scheduled."
    with transaction.atomic():
        current = Alert.objects.select_for_update().get(pk=alert.pk)
        if current.lease_token != token:
            return True
        current.status, current.last_error = status, error
        current.next_attempt_at = None if status == "sent" else timezone.now() + timedelta(seconds=delay)
        current.sent_at = timezone.now() if status == "sent" else None
        current.lease_token = current.lease_expires_at = None
        current.save()
    return True
