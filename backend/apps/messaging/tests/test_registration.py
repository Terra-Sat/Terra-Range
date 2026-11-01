from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.messaging.enums import ConsentStatus
from apps.messaging.models import Consent, Recipient
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.registration import InvalidPhone, register_recipient

pytestmark = pytest.mark.django_db


def test_register_validates_normalises_and_sends_prompt():
    recipient, created = register_recipient(name="Asha", phone="0712 345 678", ward_code="MARSABIT-01")
    assert created and recipient.phone == "+254712345678"
    assert Consent.objects.get(recipient=recipient).status == ConsentStatus.PENDING
    assert len(FakeSmsProvider.outbox) == 1
    assert FakeSmsProvider.outbox[0].to == "+254712345678"
    assert "YES" in FakeSmsProvider.outbox[0].body


def test_registration_alone_never_grants_consent():
    recipient, _ = register_recipient(phone="0712345678")
    assert Consent.objects.get(recipient=recipient).status != ConsentStatus.ACTIVE


def test_invalid_phone_is_rejected_without_echoing_it():
    with pytest.raises(InvalidPhone) as exc:
        register_recipient(phone="0212345678")
    assert "0212345678" not in str(exc.value)
    assert Recipient.objects.count() == 0


def test_registering_twice_sends_one_prompt():
    register_recipient(phone="0712345678")
    _, created = register_recipient(phone="+254712345678")
    assert not created
    assert len(FakeSmsProvider.outbox) == 1


def test_assisted_registration_never_overrides_stop():
    from apps.messaging.consent import apply_intent
    from apps.messaging.core.keywords import Intent
    from django.utils import timezone

    recipient, _ = register_recipient(phone="0712345678")
    apply_intent(recipient, Intent.STOP, occurred_at=timezone.now())
    FakeSmsProvider.reset()
    register_recipient(phone="0712345678")
    assert Consent.objects.get(recipient=recipient).status == ConsentStatus.WITHDRAWN
    assert FakeSmsProvider.outbox == []


def test_management_command_registers_and_masks_phone():
    out = StringIO()
    call_command("register_recipient", "--phone", "0712345678", "--name", "Asha", stdout=out)
    assert "pending" in out.getvalue()
    assert "712345" not in out.getvalue()
    with pytest.raises(CommandError):
        call_command("register_recipient", "--phone", "123")
