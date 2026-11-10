"""Consent state machine and the send gate. One consent record per recipient and channel.

States: none -> pending (JOIN) -> active (YES) -> withdrawn (STOP); pending -> expired (timeout).
Every decision, applied or ignored, is written to the append-only ConsentEvent ledger.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.messaging.core.keywords import Intent
from apps.messaging.enums import (
    Channel,
    ConsentStatus,
    EventOutcome,
    EventType,
    MessageClass,
    Purpose,
    Source,
)
from apps.messaging.models import Consent, ConsentEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class IntentResult:
    outcome: str
    reply: Optional[str] = None  # a Purpose to send back, or None


def can_send(recipient, channel, message_class) -> SendDecision:
    """The gate every outbound message must pass. Fails closed.

    Advisory messages need ACTIVE consent on that exact channel.
    Transactional messages are only the fixed replies in messages.py.
    """
    if str(channel) not in {Channel.SMS.value, Channel.WHATSAPP.value}:
        return SendDecision(False, "unsupported_channel")
    if str(message_class) == MessageClass.TRANSACTIONAL.value:
        return SendDecision(True, "transactional")
    consent = Consent.objects.filter(recipient=recipient, channel=channel).first()
    if consent is None:
        return SendDecision(False, "consent_none")
    if consent.status != ConsentStatus.ACTIVE:
        return SendDecision(False, f"consent_{consent.status}")
    return SendDecision(True, "consent_active")


def _ttl():
    return timedelta(hours=settings.MESSAGING_CONSENT_PENDING_TTL_HOURS)


@transaction.atomic
def apply_intent(
    recipient, intent, *, occurred_at, provider_event_id="", source=Source.SMS_KEYWORD, channel=Channel.SMS
) -> IntentResult:
    consent, _ = Consent.objects.select_for_update().get_or_create(
        recipient=recipient,
        channel=channel,
        defaults={"status": ConsentStatus.NONE, "policy_version": settings.MESSAGING_CONSENT_POLICY_VERSION},
    )
    current = consent.status

    def log(event_type, outcome, to_status=None):
        ConsentEvent.objects.create(
            recipient_id=consent.recipient_id,
            channel=channel,
            event_type=event_type,
            outcome=outcome,
            from_status=current,
            to_status=to_status or current,
            policy_version=consent.policy_version,
            source=source,
            provider_event_id=provider_event_id,
            occurred_at=occurred_at,
        )

    def touch():
        consent.last_event_at = max(t for t in (consent.last_event_at, occurred_at) if t is not None)

    # Older JOIN/YES events must never undo a newer state. STOP is never treated as stale:
    # an opt-out always applies, even if provider timestamps are skewed.
    stale = consent.last_event_at is not None and occurred_at < consent.last_event_at
    if stale and intent in (Intent.JOIN, Intent.CONFIRM):
        log(EventType.JOIN_REQUESTED if intent == Intent.JOIN else EventType.GRANTED, EventOutcome.IGNORED_STALE)
        return IntentResult("ignored_stale")

    if intent == Intent.STOP:
        if current == ConsentStatus.WITHDRAWN:
            log(EventType.WITHDRAWN, EventOutcome.IGNORED_INVALID_STATE)
            return IntentResult("already_withdrawn")  # no second confirmation SMS
        consent.status = ConsentStatus.WITHDRAWN
        consent.withdrawn_at = occurred_at
        touch()
        consent.save()
        log(EventType.WITHDRAWN, EventOutcome.APPLIED, ConsentStatus.WITHDRAWN)
        return IntentResult("withdrawn", Purpose.STOP_CONFIRMATION)

    if intent == Intent.JOIN:
        if current == ConsentStatus.ACTIVE:
            log(EventType.JOIN_REQUESTED, EventOutcome.IGNORED_INVALID_STATE)
            return IntentResult("already_active", Purpose.HELP)
        if current != ConsentStatus.PENDING:
            consent.status = ConsentStatus.PENDING
            consent.policy_version = settings.MESSAGING_CONSENT_POLICY_VERSION
            consent.requested_at = occurred_at
            consent.granted_at = None
            consent.withdrawn_at = None
            touch()
            consent.save()
            log(EventType.JOIN_REQUESTED, EventOutcome.APPLIED, ConsentStatus.PENDING)
        else:
            log(EventType.JOIN_REQUESTED, EventOutcome.APPLIED)  # prompt is re-sent, state unchanged
        return IntentResult("pending", Purpose.CONSENT_PROMPT)

    if intent == Intent.CONFIRM:
        if current != ConsentStatus.PENDING:
            log(EventType.GRANTED, EventOutcome.IGNORED_INVALID_STATE)
            return IntentResult("ignored_invalid_state", Purpose.HELP)
        if consent.requested_at and occurred_at - consent.requested_at > _ttl():
            consent.status = ConsentStatus.EXPIRED
            consent.save()
            log(EventType.EXPIRED, EventOutcome.APPLIED, ConsentStatus.EXPIRED)
            return IntentResult("expired", Purpose.HELP)
        consent.status = ConsentStatus.ACTIVE
        consent.granted_at = occurred_at
        touch()
        consent.save()
        log(EventType.GRANTED, EventOutcome.APPLIED, ConsentStatus.ACTIVE)
        return IntentResult("active", Purpose.WELCOME)

    if intent == Intent.HELP:
        log(EventType.HELP_REQUESTED, EventOutcome.APPLIED)
        return IntentResult("help", Purpose.HELP)

    return IntentResult("unrecognised")  # no reply: avoids loops with other automated senders


def expire_pending(now=None) -> int:
    """Mark pending consents older than the TTL as expired. Run from cron or a scheduler."""
    now = now or timezone.now()
    cutoff = now - _ttl()
    count = 0
    with transaction.atomic():
        for consent in Consent.objects.select_for_update().filter(status=ConsentStatus.PENDING, requested_at__lt=cutoff):
            consent.status = ConsentStatus.EXPIRED
            consent.save()
            ConsentEvent.objects.create(
                recipient_id=consent.recipient_id,
                channel=consent.channel,
                event_type=EventType.EXPIRED,
                outcome=EventOutcome.APPLIED,
                from_status=ConsentStatus.PENDING,
                to_status=ConsentStatus.EXPIRED,
                policy_version=consent.policy_version,
                source=Source.SYSTEM,
                occurred_at=now,
            )
            count += 1
    return count
