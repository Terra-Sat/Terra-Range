import uuid

from django.db import models

from apps.messaging.core.redact import mask_phone
from apps.messaging.enums import (
    Channel,
    ConsentStatus,
    DeliveryStatus,
    EventOutcome,
    EventType,
    MessageClass,
    Purpose,
    Source,
)


MAX_LOCATION_DEPTH = 8  # county -> sub-county -> ward leaves room for four more levels


class Location(models.Model):
    """One node in the administrative tree (county, sub-county, ward, or any level below).

    The tree has no fixed depth: each node only knows its parent and a free-text ``level`` label.
    ``path`` is a materialised path (``|KE010|SC1|W1|``) so "everything under this county" is one
    indexed prefix query instead of a recursive one. Load and change nodes with
    ``manage.py load_locations``; ``save()`` keeps ``depth`` and ``path`` consistent.
    """

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=150)
    level = models.CharField(max_length=32)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    depth = models.PositiveSmallIntegerField(default=0, editable=False)
    path = models.CharField(max_length=1024, db_index=True, editable=False)
    search_name = models.CharField(max_length=150, editable=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["depth", "sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.level})"

    def save(self, *args, **kwargs):
        from apps.messaging.core.keywords import normalise_text

        if "|" in self.code:
            raise ValueError("Location code must not contain '|'.")
        self.search_name = normalise_text(self.name)
        self.depth = self.parent.depth + 1 if self.parent_id else 0
        if self.depth >= MAX_LOCATION_DEPTH:
            raise ValueError(f"Locations can be at most {MAX_LOCATION_DEPTH} levels deep.")
        self.path = f"{self.parent.path}{self.code}|" if self.parent_id else f"|{self.code}|"
        super().save(*args, **kwargs)

    @property
    def is_leaf(self):
        return not self.children.filter(is_active=True).exists()

    def chain(self):
        """Names from this node up to the top, e.g. ["North Horr", "Marsabit"]."""
        names, node = [], self
        while node is not None:
            names.append(node.name)
            node = node.parent
        return names

    def label(self):
        return ", ".join(self.chain())

    @classmethod
    def rebuild_paths(cls):
        """Recompute depth and path for every node, top-down. Run after bulk re-parenting."""
        by_parent = {}
        for pk, parent_id in cls.objects.values_list("pk", "parent_id"):
            by_parent.setdefault(parent_id, []).append(pk)
        queue = [(pk, "|") for pk in by_parent.get(None, [])]
        updates = []
        nodes = {n.pk: n for n in cls.objects.all()}
        while queue:
            pk, prefix = queue.pop()
            node = nodes[pk]
            node.path = f"{prefix}{node.code}|"
            node.depth = prefix.count("|") - 1
            updates.append(node)
            queue.extend((child, node.path) for child in by_parent.get(pk, []))
        cls.objects.bulk_update(updates, ["path", "depth"], batch_size=500)
        return len(updates)


class Recipient(models.Model):
    """A person who can receive alerts. Phone is stored as E.164 (+254...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=64, blank=True)
    # ward_code is the key the data and delivery layers use. It always equals location.code when a
    # location is set, and may hold a code with no Location row for recipients registered by hand.
    ward_code = models.CharField(max_length=64, blank=True, db_index=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="recipients")
    # State of the SMS location conversation. cursor = the node whose children are being asked (null = top level).
    awaiting_location = models.BooleanField(default=False)
    location_cursor = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    location_page = models.PositiveSmallIntegerField(default=0)
    language = models.CharField(max_length=8, default="en", choices=[("en", "English"), ("sw", "Kiswahili")])
    phone = models.CharField(max_length=16, unique=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.SMS_KEYWORD)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Recipient {str(self.id)[:8]} ({mask_phone(self.phone)})"


class Consent(models.Model):
    """Current consent state per recipient and channel. History lives in ConsentEvent."""

    recipient = models.ForeignKey(Recipient, on_delete=models.PROTECT, related_name="consents")
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.SMS)
    status = models.CharField(max_length=16, choices=ConsentStatus.choices, default=ConsentStatus.NONE)
    policy_version = models.CharField(max_length=32)
    requested_at = models.DateTimeField(null=True, blank=True)
    granted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipient", "channel"], name="uniq_consent_recipient_channel"),
        ]

    def __str__(self):
        return f"{self.recipient} {self.channel}={self.status}"


class AppendOnlyError(RuntimeError):
    pass


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("ConsentEvent rows cannot be updated")

    def delete(self):
        raise AppendOnlyError("ConsentEvent rows cannot be deleted")


class ConsentEvent(models.Model):
    """Append-only audit ledger of every consent decision, applied or ignored."""

    recipient = models.ForeignKey(Recipient, on_delete=models.PROTECT, related_name="consent_events")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    outcome = models.CharField(max_length=32, choices=EventOutcome.choices)
    from_status = models.CharField(max_length=16, choices=ConsentStatus.choices)
    to_status = models.CharField(max_length=16, choices=ConsentStatus.choices)
    policy_version = models.CharField(max_length=32)
    source = models.CharField(max_length=16, choices=Source.choices)
    provider_event_id = models.CharField(max_length=128, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("ConsentEvent rows cannot be modified")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("ConsentEvent rows cannot be deleted")


class InboundEvent(models.Model):
    """One row per provider webhook event. The unique key makes processing idempotent.

    Message bodies and phone numbers are deliberately not stored here.
    """

    KIND_INBOUND_SMS = "inbound_sms"
    KIND_DELIVERY_REPORT = "delivery_report"

    provider = models.CharField(max_length=32)
    kind = models.CharField(max_length=24)
    dedupe_key = models.CharField(max_length=160)
    intent = models.CharField(max_length=16, blank=True)
    outcome = models.CharField(max_length=40, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "dedupe_key"], name="uniq_inbound_provider_key"),
        ]


class OutboundLog(models.Model):
    """Thin send log so delivery reports can be matched. The delivery engine may own a richer table later."""

    provider = models.CharField(max_length=32)
    recipient = models.ForeignKey(Recipient, null=True, blank=True, on_delete=models.PROTECT, related_name="outbound")
    message_class = models.CharField(max_length=16, choices=MessageClass.choices)
    purpose = models.CharField(max_length=24, choices=Purpose.choices)
    reference = models.CharField(max_length=128, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["provider", "provider_message_id"])]
