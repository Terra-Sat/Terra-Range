"""Africa's Talking SMS adapter (plain HTTP, no SDK).

Verify field names and status codes against the current Africa's Talking docs
and sandbox before going live.
"""
import hmac
import logging
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings

from apps.messaging.enums import DeliveryStatus
from apps.messaging.providers.base import (
    InboundMessage,
    SendRequest,
    SendResult,
    SmsProvider,
    StatusEvent,
    WebhookParseError,
)

logger = logging.getLogger(__name__)

EAT = timezone(timedelta(hours=3))  # Nairobi has no daylight saving

_STATUS_MAP = {
    "sent": DeliveryStatus.SENT.value,
    "submitted": DeliveryStatus.SUBMITTED.value,
    "buffered": DeliveryStatus.SUBMITTED.value,
    "success": DeliveryStatus.DELIVERED.value,
    "failed": DeliveryStatus.FAILED.value,
    "rejected": DeliveryStatus.FAILED.value,
}


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EAT)
    except (TypeError, ValueError):
        return None


class AfricasTalkingSms(SmsProvider):
    name = "africastalking"
    SUCCESS_CODES = {100, 101, 102}  # Processed, Sent, Queued
    RETRYABLE_CODES = {500, 501, 502}

    def __init__(self, *, username, api_key, base_url, sender_id="", webhook_token="", session=None, timeout=10):
        self.username = username
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.sender_id = sender_id
        self.webhook_token = webhook_token
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_settings(cls):
        return cls(
            username=settings.AT_USERNAME,
            api_key=settings.AT_API_KEY,
            base_url=settings.AT_BASE_URL,
            sender_id=settings.AT_SENDER_ID,
            webhook_token=settings.AT_WEBHOOK_TOKEN,
        )

    def __repr__(self):
        return f"AfricasTalkingSms(username={self.username!r}, api_key='***')"

    def send(self, request: SendRequest) -> SendResult:
        if not self.api_key:
            return SendResult(False, error_code="not_configured")
        payload = {"username": self.username, "to": request.to, "message": request.body}
        if self.sender_id:
            payload["from"] = self.sender_id
        try:
            response = self.session.post(
                f"{self.base_url}/version1/messaging",
                headers={"apiKey": self.api_key, "Accept": "application/json"},
                data=payload,
                timeout=self.timeout,
            )
        except Exception as exc:
            # Log only the exception class: messages can contain URLs, keys or numbers.
            logger.warning("africastalking send failed: %s", type(exc).__name__)
            return SendResult(False, error_code="network_error", retryable=True)

        status = response.status_code
        if status == 429 or status >= 500:
            return SendResult(False, error_code=f"http_{status}", retryable=True)
        if status not in (200, 201):
            return SendResult(False, error_code=f"http_{status}")
        try:
            first = response.json()["SMSMessageData"]["Recipients"][0]
            code = int(first["statusCode"])
        except (ValueError, KeyError, IndexError, TypeError):
            return SendResult(False, error_code="bad_response")
        if code in self.SUCCESS_CODES:
            return SendResult(True, provider_message_id=str(first.get("messageId", "")))
        return SendResult(False, error_code=f"at_{code}", retryable=code in self.RETRYABLE_CODES)

    def verify_webhook(self, *, url_token: str) -> bool:
        """Africa's Talking SMS callbacks are not signed, so we use a secret token in the callback URL."""
        if not self.webhook_token:
            return False
        return hmac.compare_digest(url_token.encode(), self.webhook_token.encode())

    def parse_inbound(self, data) -> InboundMessage:
        event_id = (data.get("id") or "").strip()
        sender = (data.get("from") or "").strip()
        if not event_id or not sender or "text" not in data:
            raise WebhookParseError("missing_fields")
        return InboundMessage(
            provider_event_id=event_id,
            sender=sender,
            text=data.get("text") or "",
            occurred_at=_parse_date(data.get("date")),
        )

    def parse_status(self, data) -> StatusEvent:
        message_id = (data.get("id") or "").strip()
        raw_status = (data.get("status") or "").strip().lower()
        if not message_id or not raw_status:
            raise WebhookParseError("missing_fields")
        return StatusEvent(
            provider_message_id=message_id,
            status=_STATUS_MAP.get(raw_status, ""),
            error_code=(data.get("failureReason") or "")[:64],
        )
