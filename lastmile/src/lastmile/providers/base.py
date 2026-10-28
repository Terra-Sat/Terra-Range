from __future__ import annotations

from typing import Protocol

from lastmile.core.models import InboundMessage, OutboundMessage, SendResult, StatusEvent


class Provider(Protocol):
    name: str

    def send(self, msg: OutboundMessage) -> SendResult: ...
    def parse_status(self, payload: dict) -> list[StatusEvent]: ...
    def parse_inbound(self, payload: dict) -> list[InboundMessage]: ...
