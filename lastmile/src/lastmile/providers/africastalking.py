from __future__ import annotations

from lastmile.core.models import InboundMessage, OutboundMessage, SendResult, StatusEvent


class AfricaTalkingProvider:
    name = "africastalking"

    def send(self, msg: OutboundMessage) -> SendResult:
        return SendResult(
            accepted=True,
            provider_message_id=f"AT_{msg.idempotency_key}",
            retryable=False,
        )

    def parse_status(self, payload: dict) -> list[StatusEvent]:
        return [StatusEvent(provider_message_id=payload.get("message_id", ""), status="sent", raw=payload)]

    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        return []
