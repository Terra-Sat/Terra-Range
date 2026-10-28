from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutboundMessage:
    idempotency_key: str
    to_e164: str
    body: str
    channel: str
    template_name: str | None = None
    template_lang: str | None = None


@dataclass(frozen=True)
class SendResult:
    accepted: bool
    provider_message_id: str | None
    retryable: bool
    error: str | None = None


@dataclass(frozen=True)
class StatusEvent:
    provider_message_id: str
    status: str
    raw: dict


@dataclass(frozen=True)
class InboundMessage:
    provider: str
    source: str
    body: str
    raw: dict
