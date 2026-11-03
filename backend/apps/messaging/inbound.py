"""Idempotent handling of provider webhook events."""
import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent, parse_intent
from apps.messaging.core.phone import normalise_ke
from apps.messaging.enums import Purpose, should_advance
from apps.messaging.models import InboundEvent, OutboundLog, Recipient
from apps.messaging.sending import send_transactional

logger = logging.getLogger(__name__)
PROVIDER = "africastalking"


@dataclass(frozen=True)
class HandleResult:
    outcome: str
    duplicate: bool = False


def handle_inbound_sms(msg) -> HandleResult:
    """Process one inbound SMS exactly once, however many times the provider delivers it."""
    phone = normalise_ke(msg.sender)
    intent = parse_intent(msg.text)
    recipient = None
    reply = None
    with transaction.atomic():
        try:
            with transaction.atomic():  # savepoint, so a duplicate does not poison the outer transaction
                event = InboundEvent.objects.create(
                    provider=PROVIDER, kind=InboundEvent.KIND_INBOUND_SMS, dedupe_key=msg.provider_event_id
                )
        except IntegrityError:
            return HandleResult("duplicate", duplicate=True)

        if phone is None:
            outcome = "invalid_sender"
        else:
            recipient = Recipient.objects.filter(phone=phone).first()
            if recipient is None and intent == Intent.JOIN:
                recipient, _ = Recipient.objects.get_or_create(phone=phone)
            if recipient is not None:
                result = apply_intent(
                    recipient, intent,
                    occurred_at=msg.occurred_at or timezone.now(),
                    provider_event_id=msg.provider_event_id,
                )
                outcome, reply = result.outcome, result.reply
            elif intent == Intent.HELP:
                outcome, reply = "help_unknown_sender", Purpose.HELP
            else:
                outcome = "unknown_sender"
        event.intent = intent.value
        event.outcome = outcome
        event.processed_at = timezone.now()
        event.save()

    # Replies go out after the commit, so a duplicate delivery can never trigger a second reply.
    if reply and phone:
        send_transactional(
            purpose=reply, phone=phone, recipient=recipient,
            language=recipient.language if recipient else "en",
        )
    return HandleResult(outcome)


def handle_delivery_report(status_event) -> HandleResult:
    """Apply a delivery report. Duplicates are ignored and statuses never move backwards."""
    with transaction.atomic():
        try:
            with transaction.atomic():
                event = InboundEvent.objects.create(
                    provider=PROVIDER, kind=InboundEvent.KIND_DELIVERY_REPORT, dedupe_key=status_event.dedupe_key
                )
        except IntegrityError:
            return HandleResult("duplicate", duplicate=True)

        if not status_event.status:
            outcome = "unknown_status"
        else:
            log = OutboundLog.objects.select_for_update().filter(
                provider=PROVIDER, provider_message_id=status_event.provider_message_id
            ).first()
            if log is None:
                # The report may have arrived before we saved the message id: do not keep the dedupe row.
                event.delete()
                return HandleResult("unknown_message")
            if should_advance(log.status, status_event.status):
                log.status = status_event.status
                log.error_code = status_event.error_code
                log.save()
                outcome = "applied"
            else:
                outcome = "ignored_out_of_order"
        event.outcome = outcome
        event.processed_at = timezone.now()
        event.save()
    return HandleResult(outcome)
