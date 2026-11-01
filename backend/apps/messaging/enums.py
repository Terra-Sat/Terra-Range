from django.db import models


class Channel(models.TextChoices):
    SMS = "sms", "SMS"


class MessageClass(models.TextChoices):
    ADVISORY = "advisory", "Advisory (non-essential)"
    TRANSACTIONAL = "transactional", "Transactional (essential)"


class ConsentStatus(models.TextChoices):
    NONE = "none", "None"
    PENDING = "pending", "Pending confirmation"
    ACTIVE = "active", "Active"
    WITHDRAWN = "withdrawn", "Withdrawn"
    EXPIRED = "expired", "Expired"


class EventType(models.TextChoices):
    JOIN_REQUESTED = "join_requested", "Join requested"
    GRANTED = "granted", "Consent granted"
    WITHDRAWN = "withdrawn", "Consent withdrawn"
    EXPIRED = "expired", "Pending consent expired"
    HELP_REQUESTED = "help_requested", "Help requested"


class EventOutcome(models.TextChoices):
    APPLIED = "applied", "Applied"
    IGNORED_STALE = "ignored_stale", "Ignored (older than current state)"
    IGNORED_INVALID_STATE = "ignored_invalid_state", "Ignored (not valid in current state)"


class Source(models.TextChoices):
    SMS_KEYWORD = "sms_keyword", "SMS keyword"
    ASSISTED = "assisted", "Assisted registration"
    SYSTEM = "system", "System"


class Purpose(models.TextChoices):
    CONSENT_PROMPT = "consent_prompt", "Consent prompt"
    WELCOME = "welcome", "Welcome"
    STOP_CONFIRMATION = "stop_confirmation", "Stop confirmation"
    HELP = "help", "Help"
    ADVISORY = "advisory", "Advisory"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SUBMITTED = "submitted", "Submitted"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


_RANK = {"queued": 0, "submitted": 1, "sent": 2, "delivered": 3, "failed": 3}
_TERMINAL = {"delivered", "failed"}


def should_advance(current, new):
    """Delivery statuses only move forward; delivered and failed are final."""
    current, new = str(current), str(new)
    if current in _TERMINAL:
        return False
    return _RANK[new] > _RANK[current]
