import logging

import pytest
import requests

from apps.messaging.providers.africastalking import AfricasTalkingSms
from apps.messaging.providers.base import SendRequest, WebhookParseError

API_KEY = "test-api-key-123"
PHONE = "+254712345678"


class FakeResponse:
    def __init__(self, status_code=201, payload=None, bad_json=False):
        self.status_code, self._payload, self._bad = status_code, payload, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc
        return self.response


def provider(session, **overrides):
    config = dict(username="sandbox", api_key=API_KEY, base_url="https://api.sandbox.africastalking.com",
                  sender_id="TERRA", webhook_token="tok", session=session)
    config.update(overrides)
    return AfricasTalkingSms(**config)


REQUEST = SendRequest(to=PHONE, body="hello")


def test_send_success_builds_the_documented_request(load_fixture):
    session = FakeSession(FakeResponse(201, load_fixture("at_send_success.json")))
    result = provider(session).send(REQUEST)
    assert (result.accepted, result.provider_message_id) == (True, "ATXid_test_0001")
    url, kwargs = session.calls[0]
    assert url == "https://api.sandbox.africastalking.com/version1/messaging"
    assert kwargs["headers"]["apiKey"] == API_KEY
    assert kwargs["data"] == {"username": "sandbox", "to": PHONE, "message": "hello", "from": "TERRA"}
    assert kwargs["timeout"] == 10


def test_provider_rejection_is_not_retryable(load_fixture):
    result = provider(FakeSession(FakeResponse(201, load_fixture("at_send_rejected.json")))).send(REQUEST)
    assert (result.accepted, result.error_code, result.retryable) == (False, "at_403", False)


@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (429, True), (400, False), (401, False)])
def test_http_errors_are_classified(status, retryable):
    result = provider(FakeSession(FakeResponse(status, {}))).send(REQUEST)
    assert (result.accepted, result.error_code, result.retryable) == (False, f"http_{status}", retryable)


def test_network_failure_does_not_leak_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    exc = requests.ConnectionError(f"failed for {PHONE} with key {API_KEY}")
    result = provider(FakeSession(exc=exc)).send(REQUEST)
    assert (result.accepted, result.error_code, result.retryable) == (False, "network_error", True)
    assert API_KEY not in caplog.text and PHONE not in caplog.text
    assert API_KEY not in repr(result)


def test_garbled_response_and_missing_key():
    assert provider(FakeSession(FakeResponse(201, bad_json=True))).send(REQUEST).error_code == "bad_response"
    assert provider(FakeSession(FakeResponse(201, {"unexpected": 1}))).send(REQUEST).error_code == "bad_response"
    no_key = provider(FakeSession(), api_key="")
    assert no_key.send(REQUEST).error_code == "not_configured"


def test_repr_hides_the_api_key():
    assert API_KEY not in repr(provider(FakeSession()))


def test_webhook_token_check():
    p = provider(FakeSession())
    assert p.verify_webhook(url_token="tok")
    assert not p.verify_webhook(url_token="tok2")
    assert not p.verify_webhook(url_token="")
    assert not provider(FakeSession(), webhook_token="").verify_webhook(url_token="")


def test_parse_inbound(load_fixture):
    message = provider(FakeSession()).parse_inbound(load_fixture("at_inbound_sms.json"))
    assert (message.provider_event_id, message.sender, message.text) == ("ATMid_in_0001", PHONE, "JOIN")
    assert message.occurred_at.utcoffset().total_seconds() == 3 * 3600  # Nairobi time


def test_parse_inbound_tolerates_bad_date_and_rejects_missing_fields(load_fixture):
    data = {**load_fixture("at_inbound_sms.json"), "date": "garbage"}
    assert provider(FakeSession()).parse_inbound(data).occurred_at is None
    with pytest.raises(WebhookParseError) as exc:
        provider(FakeSession()).parse_inbound({"text": "JOIN"})
    assert "JOIN" not in str(exc.value)


@pytest.mark.parametrize("raw,expected", [
    ("Success", "delivered"), ("Sent", "sent"), ("Submitted", "submitted"), ("Buffered", "submitted"),
    ("Failed", "failed"), ("Rejected", "failed"), ("SomethingNew", ""),
])
def test_parse_status(load_fixture, raw, expected):
    event = provider(FakeSession()).parse_status({**load_fixture("at_delivery_report.json"), "status": raw})
    assert event.status == expected


def test_failed_send_carries_the_providers_error_text_for_diagnostics():
    class Resp:
        status_code = 401
        text = '{"errorMessage":"The supplied authentication is invalid"}'

    class Session:
        def post(self, *a, **k):
            return Resp()

    from apps.messaging.providers.africastalking import AfricasTalkingSms
    from apps.messaging.providers.base import SendRequest

    provider = AfricasTalkingSms(username="u", api_key="k", base_url="https://x", session=Session())
    result = provider.send(SendRequest(to="+254712345678", body="hi"))
    assert result.error_code == "http_401" and "authentication is invalid" in result.detail


def test_diagnostic_text_never_contains_a_full_phone_number():
    class Resp:
        status_code = 200
        text = '{"SMSMessageData":{"Recipients":[{"number":"+254712345678","weird":true}]}}'

        def json(self):
            import json

            return json.loads(self.text)

    class Session:
        def post(self, *a, **k):
            return Resp()

    from apps.messaging.providers.africastalking import AfricasTalkingSms
    from apps.messaging.providers.base import SendRequest

    provider = AfricasTalkingSms(username="u", api_key="k", base_url="https://x", session=Session())
    result = provider.send(SendRequest(to="+254712345678", body="hi"))
    assert result.accepted is False and "254712345678" not in result.detail and result.detail.count("*") >= 9
