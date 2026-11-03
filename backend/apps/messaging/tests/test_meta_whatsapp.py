import hashlib
import hmac

import pytest

from apps.messaging.consent import can_send
from apps.messaging.enums import Channel, MessageClass
from apps.messaging.models import Recipient
from apps.messaging.providers.base import SendRequest
from apps.messaging.providers.meta_whatsapp import MetaWhatsAppProvider


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc
        return self.response


@pytest.mark.django_db
def test_whatsapp_consent_gate_allows_active_consent():
    recipient = Recipient.objects.create(phone="+254712345678", name="Test")
    assert can_send(recipient, Channel.WHATSAPP, MessageClass.ADVISORY).reason == "consent_none"


def test_meta_provider_send_builds_whatsapp_payload():
    session = FakeSession(FakeResponse(200, {"messages": [{"id": "wamid.123"}]}))
    provider = MetaWhatsAppProvider(
        phone_number_id="12345",
        access_token="token",
        app_secret="secret",
        session=session,
    )
    result = provider.send(SendRequest(to="+254712345678", body="hello"))
    assert result.accepted is True
    assert result.provider_message_id == "wamid.123"
    assert session.calls[0][0] == "https://graph.facebook.com/v20.0/12345/messages"
    payload = session.calls[0][1]["json"]
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "+254712345678"
    assert payload["text"]["body"] == "hello"


def test_meta_provider_verifies_signature_and_parses_inbound():
    provider = MetaWhatsAppProvider(phone_number_id="12345", access_token="token", app_secret="secret")
    payload = b'{"entry":[{"changes":[{"value":{"messages":[{"from":"254712345678","text":{"body":"JOIN"},"id":"wamid.123"}]}}]}]}'
    signature = "sha256=" + hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    assert provider.verify_webhook_signature(payload, signature) is True
    inbound = provider.parse_inbound({
        "entry": [{"changes": [{"value": {"messages": [{"from": "254712345678", "text": {"body": "JOIN"}, "id": "wamid.123"}]}}]}]
    })
    assert (inbound.provider_event_id, inbound.sender, inbound.text) == ("wamid.123", "+254712345678", "JOIN")
