"""Shared provider interface. SMS only for now; a WhatsApp provider can implement the same shape later."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


class WebhookParseError(ValueError):
    """A webhook payload was missing required fields. The message never contains payload data."""


@dataclass(frozen=True)
class SendRequest:
    to: str
    body: str
    reference: str = ""


@dataclass(frozen=True)
class SendResult:
    accepted: bool
    provider_message_id: str = ""
    error_code: str = ""
    retryable: bool = False
    detail: str = ""  # provider's own error text, for diagnostics only; never contains keys


@dataclass(frozen=True)
class InboundMessage:
    provider_event_id: str
    sender: str
    text: str
    occurred_at: Optional[datetime] = None


@dataclass(frozen=True)
class StatusEvent:
    provider_message_id: str
    status: str  # a DeliveryStatus value, or "" when the provider status is not recognised
    error_code: str = ""

    @property
    def dedupe_key(self):
        return f"{self.provider_message_id}:{self.status or 'unknown'}"


class SmsProvider(ABC):
    name = ""

    @abstractmethod
    def send(self, request: SendRequest) -> SendResult:
        """Never raises for provider failures: returns SendResult(accepted=False, error_code=...)."""

    def verify_webhook(self, *, url_token: str) -> bool:
        raise NotImplementedError

    def parse_inbound(self, data: Mapping) -> InboundMessage:
        raise NotImplementedError

    def parse_status(self, data: Mapping) -> StatusEvent:
        raise NotImplementedError
