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
from apps.messaging.sending import send_advisory, send_transactional

logger = logging.getLogger(__name__)
PROVIDER = "africastalking"


def build_status_report(recipient):
    """Build a short text status summary for a ward-level recipient."""
    ward_code = (recipient.ward_code or "").strip()
    if not ward_code:
        return "Terra Range status: no ward is assigned for this number. Please contact the admin to link your ward."

    try:
        from apps.geography.models import WardIndicator
    except Exception:
        return f"Terra Range status: ward {ward_code} is available, but the indicator data is temporarily unavailable."

    latest = WardIndicator.objects.filter(ward_code=ward_code, value__isnull=False).order_by("-period_start").first()
    if latest is None:
        return f"Terra Range status: no recent data is available for ward {ward_code}. Please check again later."

    previous = WardIndicator.objects.filter(
        ward_code=ward_code,
        indicator=latest.indicator,
        value__isnull=False,
        period_start__lt=latest.period_start,
    ).order_by("-period_start").first()

    current_value = float(latest.value)
    previous_value = float(previous.value) if previous and previous.value is not None else current_value
    delta = current_value - previous_value
    direction = "up" if delta >= 0 else "down"
    if current_value >= 60:
        status_label = "Healthy"
    elif current_value >= 45:
        status_label = "Stressed"
    else:
        status_label = "Critical"

    return (
        f"Terra Range status: Ward {ward_code} is {status_label}. "
        f"Latest {latest.indicator.upper()} is {current_value:.1f}, {direction} {abs(delta):.1f} from the previous period. "
        f"Please review grazing and water conditions in the field this week."
    )


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
    status_reply = None
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
            if intent == Intent.STATUS:
                if recipient is not None:
                    outcome = "status_requested"
                    status_reply = build_status_report(recipient)
                else:
                    outcome = "status_unknown_sender"
                    status_reply = None
            elif recipient is not None:
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
    if intent == Intent.STATUS and status_reply and phone and recipient and recipient.language:
        send_advisory(recipient, status_reply, reference="status")
    elif reply and phone:
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
