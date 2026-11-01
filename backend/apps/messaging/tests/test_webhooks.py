from urllib.parse import urlencode

import pytest
from django.urls import reverse

from apps.messaging.models import Consent, ConsentEvent, InboundEvent, OutboundLog, Recipient
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.sending import send_advisory

pytestmark = pytest.mark.django_db

TOKEN = "test-webhook-token"
PHONE = "+254712345678"


def inbound_url(token=TOKEN):
    return reverse("messaging:at-inbound", kwargs={"token": token})


def delivery_url(token=TOKEN):
    return reverse("messaging:at-delivery", kwargs={"token": token})


def make_sent_log(recipient=None):
    """What a real Africa's Talking send leaves behind: a submitted message with a provider id."""
    return OutboundLog.objects.create(
        provider="africastalking", recipient=recipient, message_class="transactional", purpose="help",
        provider_message_id="ATXid_test_0001", status="submitted",
    )


def post(client, url, data):
    return client.post(url, urlencode(data), content_type="application/x-www-form-urlencoded")


@pytest.fixture
def sms(client, load_fixture):
    def _send(text, event_id, sender=PHONE):
        data = {**load_fixture("at_inbound_sms.json"), "text": text, "id": event_id, "from": sender}
        return post(client, inbound_url(), data)

    return _send


def test_invalid_token_is_rejected_and_nothing_is_processed(client, load_fixture):
    response = post(client, inbound_url("wrong-token"), load_fixture("at_inbound_sms.json"))
    assert response.status_code == 403
    assert post(client, delivery_url("wrong-token"), load_fixture("at_delivery_report.json")).status_code == 403
    assert Recipient.objects.count() == 0 and InboundEvent.objects.count() == 0


def test_unconfigured_token_rejects_everything(client, settings, load_fixture):
    settings.AT_WEBHOOK_TOKEN = ""
    assert post(client, inbound_url("anything"), load_fixture("at_inbound_sms.json")).status_code == 403


def test_only_post_is_allowed(client):
    assert client.get(inbound_url()).status_code == 405


def test_malformed_payload_returns_400(client):
    assert post(client, inbound_url(), {"text": "JOIN"}).status_code == 400
    assert post(client, delivery_url(), {"id": "x"}).status_code == 400


def test_full_sms_flow_join_yes_stop(sms):
    assert sms("JOIN", "m1").status_code == 200
    recipient = Recipient.objects.get(phone=PHONE)
    assert Consent.objects.get(recipient=recipient).status == "pending"
    assert sms("yes", "m2").status_code == 200
    assert Consent.objects.get(recipient=recipient).status == "active"
    assert send_advisory(recipient, "Pasture alert").sent
    assert sms("STOP", "m3").status_code == 200
    assert Consent.objects.get(recipient=recipient).status == "withdrawn"
    assert not send_advisory(recipient, "Pasture alert").sent
    bodies = [m.body for m in FakeSmsProvider.outbox]
    assert len(bodies) == 4  # prompt, welcome, the one advisory, stop confirmation
    assert "Pasture alert" not in bodies[-1]


def test_duplicate_inbound_event_is_processed_once(sms):
    assert sms("JOIN", "dup-1").status_code == 200
    assert sms("JOIN", "dup-1").status_code == 200
    assert InboundEvent.objects.count() == 1
    assert ConsentEvent.objects.count() == 1
    assert len(FakeSmsProvider.outbox) == 1


def test_stop_from_unknown_number_is_ignored_but_help_is_answered(sms):
    sms("STOP", "u1", sender="+254799000111")
    assert Recipient.objects.count() == 0 and FakeSmsProvider.outbox == []
    sms("HELP", "u2", sender="+254799000111")
    assert len(FakeSmsProvider.outbox) == 1 and Recipient.objects.count() == 0


def test_invalid_sender_number_is_ignored(sms):
    assert sms("JOIN", "bad-1", sender="12345").status_code == 200
    assert Recipient.objects.count() == 0 and FakeSmsProvider.outbox == []


def test_delivery_reports_advance_and_never_go_backwards(client, load_fixture):
    make_sent_log(Recipient.objects.create(phone=PHONE))
    report = load_fixture("at_delivery_report.json")
    assert post(client, delivery_url(), report).status_code == 200
    assert OutboundLog.objects.get().status == "delivered"
    # Out of order: an earlier "Sent" arrives after "Success".
    post(client, delivery_url(), {**report, "status": "Sent"})
    assert OutboundLog.objects.get().status == "delivered"
    assert InboundEvent.objects.get(dedupe_key="ATXid_test_0001:sent").outcome == "ignored_out_of_order"
    # Duplicate "Success" is ignored.
    post(client, delivery_url(), report)
    assert InboundEvent.objects.count() == 2


def test_failed_report_records_error_code(client, load_fixture):
    make_sent_log()
    post(client, delivery_url(), {**load_fixture("at_delivery_report.json"), "status": "Failed", "failureReason": "InsufficientBalance"})
    log = OutboundLog.objects.get()
    assert (log.status, log.error_code) == ("failed", "InsufficientBalance")


def test_report_for_unknown_message_is_not_remembered(client, load_fixture):
    response = post(client, delivery_url(), {**load_fixture("at_delivery_report.json"), "id": "not-ours"})
    assert response.status_code == 200
    assert InboundEvent.objects.count() == 0  # a later redelivery can still be applied
