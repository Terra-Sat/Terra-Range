"""Idempotent handling of provider webhook events."""
import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.messaging import location_flow
from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent, parse_intent
from apps.messaging.core.phone import normalise_ke
from apps.messaging.enums import Channel, ConsentStatus, Purpose, should_advance
from apps.messaging.models import Consent, InboundEvent, OutboundLog, Recipient
from apps.messaging.sending import send_advisory, send_transactional, send_transactional_text, send_whatsapp
from apps.messaging.signals import delivery_status_changed

logger = logging.getLogger(__name__)
PROVIDER = "africastalking"


def build_status_report(recipient):
    """Build a short text status summary for a ward-level recipient."""
    ward_code = (recipient.ward_code or "").strip()
    if not ward_code:
        return "Terra Range status: we do not have your location yet. Reply LOCATION to set it."

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


def _consent_active(recipient, channel):
    return Consent.objects.filter(recipient=recipient, channel=channel, status=ConsentStatus.ACTIVE).exists()


def handle_inbound_sms(msg, *, channel=Channel.SMS, provider=PROVIDER) -> HandleResult:
    """Process one inbound message exactly once, however many times the provider delivers it.

    ``channel`` decides which consent record the message can change and which channel the replies
    use, so a WhatsApp "YES" can never grant SMS consent (and the reverse).
    """
    phone = normalise_ke(msg.sender)
    intent = parse_intent(msg.text)
    recipient = None
    reply = None
    status_reply = None
    flow_replies = []
    with transaction.atomic():
        try:
            with transaction.atomic():  # savepoint, so a duplicate does not poison the outer transaction
                event = InboundEvent.objects.create(
                    provider=provider, kind=InboundEvent.KIND_INBOUND_SMS, dedupe_key=msg.provider_event_id
                )
        except IntegrityError:
            return HandleResult("duplicate", duplicate=True)

        if phone is None:
            outcome = "invalid_sender"
        else:
            recipient = Recipient.objects.filter(phone=phone).first()
            if recipient is None and intent == Intent.JOIN:
                recipient, _ = Recipient.objects.get_or_create(phone=phone)
            consented = recipient is not None and _consent_active(recipient, channel)

            if intent == Intent.STATUS:
                if recipient is not None:
                    outcome = "status_requested"
                    status_reply = build_status_report(recipient)
                else:
                    outcome = "status_unknown_sender"
            elif intent == Intent.LOCATION:
                if recipient is None:
                    outcome = "unknown_sender"
                elif not consented:
                    outcome, reply = "location_needs_consent", Purpose.HELP
                else:
                    first_question = location_flow.start(recipient)
                    if first_question is None:
                        outcome, reply = "location_unavailable", Purpose.HELP
                    else:
                        outcome, flow_replies = "location_started", [first_question]
            elif recipient is not None and consented and recipient.awaiting_location and intent == Intent.CONFIRM:
                # They typed YES again while a question is open: repeat the question, do not log a consent event.
                question = location_flow.reask(recipient)
                outcome = "location_reask"
                flow_replies = [question] if question else []
            elif recipient is not None and consented and recipient.awaiting_location and intent == Intent.UNKNOWN:
                step = location_flow.answer(recipient, msg.text)
                outcome = step.outcome
                flow_replies = [step.reply] if step.reply else []
            elif recipient is not None:
                result = apply_intent(
                    recipient, intent,
                    occurred_at=msg.occurred_at or timezone.now(),
                    provider_event_id=msg.provider_event_id,
                    channel=channel,
                )
                outcome, reply = result.outcome, result.reply
                if intent == Intent.STOP:
                    location_flow.cancel(recipient)
                elif result.outcome == "active" and recipient.location_id is None:
                    first_question = location_flow.start(recipient)
                    if first_question is not None:
                        flow_replies = [first_question]
            elif intent == Intent.HELP:
                outcome, reply = "help_unknown_sender", Purpose.HELP
            else:
                outcome = "unknown_sender"
        event.intent = intent.value
        event.outcome = outcome
        event.processed_at = timezone.now()
        event.save()

    # Replies go out after the commit, so a duplicate delivery can never trigger a second reply.
    if phone and recipient:
        if status_reply:
            if channel == Channel.WHATSAPP:
                send_whatsapp(recipient, status_reply, reference="status")
            else:
                send_advisory(recipient, status_reply, reference="status")
    if phone and reply:
        send_transactional(
            purpose=reply, phone=phone, recipient=recipient,
            language=recipient.language if recipient else "en", channel=channel,
        )
    for flow_reply in flow_replies:
        send_transactional_text(
            purpose=flow_reply.purpose, body=flow_reply.body, phone=phone, recipient=recipient, channel=channel,
        )
    return HandleResult(outcome)


def handle_delivery_report(status_event, *, provider=PROVIDER) -> HandleResult:
    """Apply a delivery report. Duplicates are ignored and statuses never move backwards."""
    changed = None
    with transaction.atomic():
        try:
            with transaction.atomic():
                event = InboundEvent.objects.create(
                    provider=provider, kind=InboundEvent.KIND_DELIVERY_REPORT, dedupe_key=status_event.dedupe_key
                )
        except IntegrityError:
            return HandleResult("duplicate", duplicate=True)

        if not status_event.status:
            outcome = "unknown_status"
        else:
            log = OutboundLog.objects.select_for_update().filter(
                provider=provider, provider_message_id=status_event.provider_message_id
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
                changed = {
                    "reference": log.reference,
                    "provider_message_id": log.provider_message_id,
                    "status": str(log.status),
                    "error_code": log.error_code,
                }
            else:
                outcome = "ignored_out_of_order"
        event.outcome = outcome
        event.processed_at = timezone.now()
        event.save()

    if changed:
        # After the commit. A failing subscriber must never make the webhook fail.
        for receiver, response in delivery_status_changed.send_robust(sender=OutboundLog, **changed):
            if isinstance(response, Exception):
                logger.error("delivery_status_changed receiver failed: %s", type(response).__name__)
    return HandleResult(outcome)
