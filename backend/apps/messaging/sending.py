"""Outbound SMS. The delivery engine should call send_advisory(); it enforces consent."""
import logging
from dataclasses import dataclass
from typing import Optional

from apps.messaging.consent import can_send
from apps.messaging.core.redact import mask_phone
from apps.messaging.enums import Channel, DeliveryStatus, MessageClass, Purpose
from apps.messaging.messages import render
from apps.messaging.models import OutboundLog
from apps.messaging.providers.base import SendRequest, SendResult
from apps.messaging.providers.registry import get_sms_provider, get_whatsapp_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendOutcome:
    sent: bool
    reason: str = ""
    retryable: bool = False
    log_id: Optional[int] = None


def send_advisory(recipient, body, *, reference="") -> SendOutcome:
    """Send a non-essential alert. Suppressed unless the recipient has ACTIVE SMS consent."""
    decision = can_send(recipient, Channel.SMS, MessageClass.ADVISORY)
    if not decision.allowed:
        logger.info("advisory suppressed reason=%s recipient=%s", decision.reason, recipient.id)
        return SendOutcome(False, reason=decision.reason)
    return _dispatch(
        recipient=recipient, phone=recipient.phone, body=body, message_class=MessageClass.ADVISORY,
        purpose=Purpose.ADVISORY, reference=reference,
    )


def send_transactional(*, purpose, phone, recipient=None, language="en") -> SendOutcome:
    """Send one of the fixed replies (consent prompt, welcome, stop confirmation, help)."""
    return _dispatch(
        recipient=recipient, phone=phone, body=render(purpose, language),
        message_class=MessageClass.TRANSACTIONAL, purpose=purpose, reference="",
    )


def send_whatsapp(recipient, body, *, reference="") -> SendOutcome:
    """Send an outbound WhatsApp message when the recipient has WA consent."""
    decision = can_send(recipient, Channel.WHATSAPP, MessageClass.ADVISORY)
    if not decision.allowed:
        logger.info("whatsapp advisory suppressed reason=%s recipient=%s", decision.reason, recipient.id)
        return SendOutcome(False, reason=decision.reason)
    return _dispatch(
        recipient=recipient,
        phone=recipient.phone,
        body=body,
        message_class=MessageClass.ADVISORY,
        purpose=Purpose.ADVISORY,
        reference=reference,
        channel=Channel.WHATSAPP,
    )


def _dispatch(*, recipient, phone, body, message_class, purpose, reference, channel=Channel.SMS) -> SendOutcome:
    provider = get_sms_provider() if channel == Channel.SMS else get_whatsapp_provider()
    try:
        result = provider.send(SendRequest(to=phone, body=body, reference=reference))
    except Exception as exc:
        logger.error("%s provider raised %s", channel.value, type(exc).__name__)
        result = SendResult(False, error_code="provider_exception", retryable=True)
    log = OutboundLog.objects.create(
        provider=provider.name,
        recipient=recipient,
        message_class=message_class,
        purpose=purpose,
        reference=reference,
        provider_message_id=result.provider_message_id,
        status=DeliveryStatus.SUBMITTED if result.accepted else DeliveryStatus.FAILED,
        error_code=result.error_code,
    )
    if not result.accepted:
        logger.warning("%s send failed code=%s retryable=%s to=%s", channel.value, result.error_code, result.retryable, mask_phone(phone))
    return SendOutcome(result.accepted, reason=result.error_code or "submitted", retryable=result.retryable, log_id=log.id)
