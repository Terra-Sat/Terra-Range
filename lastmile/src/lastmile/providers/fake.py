from __future__ import annotations

from lastmile.core.models import InboundMessage, OutboundMessage, SendResult, StatusEvent


class FakeProvider:
    name = "fake"

    def __init__(self, fail_pattern: dict[str, str] | None = None):
        self.fail_pattern = fail_pattern or {}

    def send(self, msg: OutboundMessage) -> SendResult:
        if self.fail_pattern.get(msg.to_e164):
            return SendResult(
                accepted=False,
                provider_message_id=None,
                retryable=False,
                error=self.fail_pattern[msg.to_e164],
            )
        return SendResult(
            accepted=True,
            provider_message_id=f"fake_{msg.idempotency_key}",
            retryable=False,
        )

    def parse_status(self, payload: dict) -> list[StatusEvent]:
        return [StatusEvent(provider_message_id=payload.get("id", ""), status="submitted", raw=payload)]

    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        return []
