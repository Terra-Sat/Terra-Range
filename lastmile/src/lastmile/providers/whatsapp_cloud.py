from __future__ import annotations

from lastmile.core.models import InboundMessage, OutboundMessage, SendResult, StatusEvent


class WhatsAppCloudProvider:
    name = "whatsapp_cloud"

    def send(self, msg: OutboundMessage) -> SendResult:
        return SendResult(
            accepted=True,
            provider_message_id=f"WA_{msg.idempotency_key}",
            retryable=False,
        )

    def parse_status(self, payload: dict) -> list[StatusEvent]:
        return [StatusEvent(provider_message_id=payload.get("id", ""), status="delivered", raw=payload)]

    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        return []
