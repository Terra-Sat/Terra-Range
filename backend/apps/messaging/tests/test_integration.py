from urllib.parse import urlencode

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent
from apps.messaging.models import OutboundLog, Recipient
from apps.messaging.providers.base import SendResult
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.recipients import active_recipients
from apps.messaging.sending import send_advisory
from apps.messaging.signals import delivery_status_changed

pytestmark = pytest.mark.django_db


def make_recipient(phone, ward, *, active=True, stopped=False):
    recipient = Recipient.objects.create(phone=phone, ward_code=ward)
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now())
    if active:
        apply_intent(recipient, Intent.CONFIRM, occurred_at=timezone.now())
    if stopped:
        apply_intent(recipient, Intent.STOP, occurred_at=timezone.now())
    return recipient


def test_active_recipients_only_returns_consented_people_in_the_ward():
    good = make_recipient("+254711000001", "NORTH-HORR")
    make_recipient("+254711000002", "NORTH-HORR", active=False)  # pending
    make_recipient("+254711000003", "NORTH-HORR", stopped=True)  # opted out
    make_recipient("+254711000004", "LOIYANGALANI")  # other ward
    assert list(active_recipients(ward_code="NORTH-HORR")) == [good]
    assert active_recipients().count() == 2


def test_same_reference_never_sends_twice():
    recipient = make_recipient("+254711000001", "NORTH-HORR")
    first = send_advisory(recipient, "Pasture alert", reference="delivery-42")
    second = send_advisory(recipient, "Pasture alert", reference="delivery-42")
    assert first.sent and second.sent and second.reason == "duplicate_reference"
    assert len(FakeSmsProvider.outbox) == 1
    assert OutboundLog.objects.filter(reference="delivery-42").count() == 1


def test_failed_send_can_be_retried_with_the_same_reference():
    recipient = make_recipient("+254711000001", "NORTH-HORR")
    FakeSmsProvider.next_result = SendResult(False, error_code="network_error", retryable=True)
    first = send_advisory(recipient, "Pasture alert", reference="delivery-43")
    assert (first.sent, first.retryable) == (False, True)
    assert send_advisory(recipient, "Pasture alert", reference="delivery-43").sent
    assert len(FakeSmsProvider.outbox) == 1


def test_stop_beats_a_queued_retry():
    recipient = make_recipient("+254711000001", "NORTH-HORR")
    apply_intent(recipient, Intent.STOP, occurred_at=timezone.now())
    outcome = send_advisory(recipient, "Pasture alert", reference="delivery-44")
    assert (outcome.sent, outcome.reason) == (False, "consent_withdrawn")
    assert FakeSmsProvider.outbox == []


def test_delivery_reports_notify_subscribers_with_the_callers_reference(client, load_fixture):
    recipient = make_recipient("+254711000001", "NORTH-HORR")
    send_advisory(recipient, "Pasture alert", reference="delivery-45")
    OutboundLog.objects.filter(reference="delivery-45").update(provider="africastalking", provider_message_id="ATXid_test_0001")
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    delivery_status_changed.connect(receiver)
    try:
        url = reverse("messaging:at-delivery", kwargs={"token": "test-webhook-token"})
        report = load_fixture("at_delivery_report.json")
        client.post(url, urlencode(report), content_type="application/x-www-form-urlencoded")
        client.post(url, urlencode(report), content_type="application/x-www-form-urlencoded")  # duplicate
    finally:
        delivery_status_changed.disconnect(receiver)
    assert len(received) == 1
    assert received[0]["reference"] == "delivery-45"
    assert received[0]["status"] == "delivered"


def test_a_failing_subscriber_does_not_break_the_webhook(client, load_fixture):
    recipient = make_recipient("+254711000001", "NORTH-HORR")
    send_advisory(recipient, "Pasture alert", reference="delivery-46")
    OutboundLog.objects.filter(reference="delivery-46").update(provider="africastalking", provider_message_id="ATXid_test_0001")

    def broken(sender, **kwargs):
        raise RuntimeError("subscriber bug")

    delivery_status_changed.connect(broken)
    try:
        url = reverse("messaging:at-delivery", kwargs={"token": "test-webhook-token"})
        response = client.post(url, urlencode(load_fixture("at_delivery_report.json")), content_type="application/x-www-form-urlencoded")
    finally:
        delivery_status_changed.disconnect(broken)
    assert response.status_code == 200
    assert OutboundLog.objects.get(reference="delivery-46").status == "delivered"
