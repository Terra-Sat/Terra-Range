import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Mapping

import requests
from django.conf import settings

from apps.messaging.enums import DeliveryStatus
from apps.messaging.providers.base import (
    InboundMessage,
    SendRequest,
    SendResult,
    StatusEvent,
    WebhookParseError,
)

logger = logging.getLogger(__name__)


class MetaWhatsAppProvider:
    name = "meta_whatsapp"

    def __init__(self, *, phone_number_id, access_token, app_secret, version="v20.0", verify_token="", session=None, timeout=10):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.app_secret = app_secret
        self.version = version
        self.verify_token = verify_token
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_settings(cls):
        return cls(
            phone_number_id=settings.META_WHATSAPP_PHONE_NUMBER_ID,
            access_token=settings.META_WHATSAPP_ACCESS_TOKEN,
            app_secret=settings.META_WHATSAPP_APP_SECRET,
            verify_token=settings.META_WHATSAPP_VERIFY_TOKEN,
            version=settings.META_WHATSAPP_API_VERSION,
        )

    def send(self, request: SendRequest) -> SendResult:
        if not self.access_token or not self.phone_number_id:
            return SendResult(False, error_code="not_configured")

        payload = {
            "messaging_product": "whatsapp",
            "to": request.to,
            "type": "text",
            "text": {"body": request.body},
        }
        try:
            response = self.session.post(
                f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.warning("meta whatsapp send failed: %s", type(exc).__name__)
            return SendResult(False, error_code="network_error", retryable=True)

        if response.status_code >= 500 or response.status_code == 429:
            return SendResult(False, error_code=f"http_{response.status_code}", retryable=True)
        if response.status_code not in range(200, 300):
            return SendResult(False, error_code=f"http_{response.status_code}")

        try:
            message_id = response.json()["messages"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError):
            return SendResult(False, error_code="bad_response")
        return SendResult(True, provider_message_id=str(message_id))

    def verify_webhook(self, *, mode: str, token: str, challenge: str) -> bool:
        if not self.verify_token:
            return False
        if mode != "subscribe":
            return False
        return hmac.compare_digest(token, self.verify_token) and bool(challenge)

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        if not self.app_secret:
            return False
        if not signature:
            return False
        expected = "sha256=" + hmac.new(self.app_secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def parse_inbound(self, data: Mapping) -> InboundMessage:
        messages = []
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    if message.get("type") == "text" or "text" in message:
                        messages.append(message)
        if not messages:
            raise WebhookParseError("missing_message")

        first = messages[0]
        sender = str(first.get("from") or "").strip()
        if sender.startswith("254") and not sender.startswith("+"):
            sender = "+" + sender
        text = (first.get("text") or {}).get("body") or ""
        provider_event_id = str(first.get("id") or "").strip()
        timestamp = first.get("timestamp")
        occurred_at = None
        if timestamp is not None:
            try:
                occurred_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (TypeError, ValueError):
                occurred_at = None

        if not sender or not provider_event_id:
            raise WebhookParseError("missing_fields")

        return InboundMessage(
            provider_event_id=provider_event_id,
            sender=sender,
            text=text,
            occurred_at=occurred_at,
        )

    def parse_status(self, data: Mapping) -> StatusEvent:
        states = []
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for status in value.get("statuses", []):
                    states.append(status)
        if not states:
            raise WebhookParseError("missing_status")

        first = states[0]
        provider_message_id = str(first.get("id") or "").strip()
        status_name = str(first.get("status") or "").strip().lower()
        status = {
            "queued": DeliveryStatus.QUEUED.value,
            "sent": DeliveryStatus.SENT.value,
            "delivered": DeliveryStatus.DELIVERED.value,
            "read": DeliveryStatus.DELIVERED.value,
            "failed": DeliveryStatus.FAILED.value,
            "rejected": DeliveryStatus.FAILED.value,
        }.get(status_name, "")
        if not provider_message_id or not status:
            raise WebhookParseError("missing_status_fields")
        return StatusEvent(provider_message_id=provider_message_id, status=status)
