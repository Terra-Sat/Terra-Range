import logging

import pytest

from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent
from apps.messaging.models import OutboundLog, Recipient
from apps.messaging.providers.base import SendResult
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.sending import send_advisory
from django.utils import timezone

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"


@pytest.fixture
def recipient():
    return Recipient.objects.create(phone=PHONE)


def subscribe(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now())
    apply_intent(recipient, Intent.CONFIRM, occurred_at=timezone.now())


def test_advisory_goes_out_only_with_active_consent(recipient):
    assert send_advisory(recipient, "Pasture alert").reason == "consent_none"
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now())
    assert send_advisory(recipient, "Pasture alert").reason == "consent_pending"
    assert FakeSmsProvider.outbox == []
    subscribe(recipient)
    outcome = send_advisory(recipient, "Pasture alert", reference="adv-1")
    assert outcome.sent
    assert [m.body for m in FakeSmsProvider.outbox] == ["Pasture alert"]
    assert OutboundLog.objects.get(id=outcome.log_id).status == "submitted"


def test_stop_blocks_the_next_advisory(recipient):
    subscribe(recipient)
    apply_intent(recipient, Intent.STOP, occurred_at=timezone.now())
    assert not send_advisory(recipient, "Pasture alert").sent
    assert FakeSmsProvider.outbox == []


def test_provider_failure_is_logged_without_personal_data(recipient, caplog):
    caplog.set_level(logging.DEBUG)
    subscribe(recipient)
    FakeSmsProvider.next_result = SendResult(False, error_code="at_405", retryable=False)
    outcome = send_advisory(recipient, "Pasture alert")
    assert (outcome.sent, outcome.reason) == (False, "at_405")
    assert OutboundLog.objects.get(id=outcome.log_id).status == "failed"
    assert "712345678" not in caplog.text
    assert PHONE not in caplog.text


def test_provider_exception_does_not_leak_message(recipient, caplog):
    caplog.set_level(logging.DEBUG)
    subscribe(recipient)
    FakeSmsProvider.raise_next = RuntimeError(f"boom {PHONE} secret-api-key")
    outcome = send_advisory(recipient, "Pasture alert")
    assert (outcome.sent, outcome.retryable) == (False, True)
    assert "secret-api-key" not in caplog.text
    assert PHONE not in caplog.text
