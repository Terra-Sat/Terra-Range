from datetime import timedelta

import pytest
from django.utils import timezone

from apps.messaging.consent import apply_intent, can_send, expire_pending
from apps.messaging.core.keywords import Intent
from apps.messaging.enums import Channel, ConsentStatus, EventOutcome, MessageClass, Purpose, Source
from apps.messaging.models import AppendOnlyError, Consent, ConsentEvent, Recipient

pytestmark = pytest.mark.django_db


@pytest.fixture
def recipient():
    return Recipient.objects.create(phone="+254712345678", name="Test")


def at(minutes):
    return timezone.now() + timedelta(minutes=minutes)


def status_of(recipient):
    return Consent.objects.get(recipient=recipient, channel=Channel.SMS).status


def test_join_then_yes_activates_and_records_policy_version(recipient):
    assert apply_intent(recipient, Intent.JOIN, occurred_at=at(0)).reply == Purpose.CONSENT_PROMPT
    assert status_of(recipient) == ConsentStatus.PENDING
    assert apply_intent(recipient, Intent.CONFIRM, occurred_at=at(1)).reply == Purpose.WELCOME
    consent = Consent.objects.get(recipient=recipient)
    assert consent.status == ConsentStatus.ACTIVE
    assert consent.granted_at is not None
    assert consent.policy_version == "v1"
    events = list(ConsentEvent.objects.all())
    assert [e.event_type for e in events] == ["join_requested", "granted"]
    assert {e.channel for e in events} == {"sms"}
    assert {e.policy_version for e in events} == {"v1"}


def test_stop_suppresses_advisory_immediately(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=at(0))
    apply_intent(recipient, Intent.CONFIRM, occurred_at=at(1))
    assert can_send(recipient, Channel.SMS, MessageClass.ADVISORY).allowed
    result = apply_intent(recipient, Intent.STOP, occurred_at=at(2))
    assert result.reply == Purpose.STOP_CONFIRMATION
    decision = can_send(recipient, Channel.SMS, MessageClass.ADVISORY)
    assert (decision.allowed, decision.reason) == (False, "consent_withdrawn")
    assert apply_intent(recipient, Intent.STOP, occurred_at=at(3)).reply is None  # one confirmation only


def test_stop_works_from_pending_and_from_no_consent(recipient):
    apply_intent(recipient, Intent.STOP, occurred_at=at(0))
    assert status_of(recipient) == ConsentStatus.WITHDRAWN
    other = Recipient.objects.create(phone="+254722000111")
    apply_intent(other, Intent.JOIN, occurred_at=at(0))
    apply_intent(other, Intent.STOP, occurred_at=at(1))
    assert status_of(other) == ConsentStatus.WITHDRAWN


def test_yes_without_join_is_ignored(recipient):
    result = apply_intent(recipient, Intent.CONFIRM, occurred_at=at(0))
    assert result.outcome == "ignored_invalid_state"
    assert status_of(recipient) == ConsentStatus.NONE
    assert ConsentEvent.objects.get().outcome == EventOutcome.IGNORED_INVALID_STATE


def test_stale_yes_after_stop_does_not_reactivate(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=at(0))
    apply_intent(recipient, Intent.STOP, occurred_at=at(5))
    assert apply_intent(recipient, Intent.CONFIRM, occurred_at=at(3)).outcome == "ignored_stale"
    assert apply_intent(recipient, Intent.CONFIRM, occurred_at=at(6)).outcome == "ignored_invalid_state"
    assert status_of(recipient) == ConsentStatus.WITHDRAWN
    assert ConsentEvent.objects.filter(outcome=EventOutcome.IGNORED_STALE).count() == 1


def test_stop_is_never_ignored_as_stale(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=at(10))
    apply_intent(recipient, Intent.CONFIRM, occurred_at=at(11))
    apply_intent(recipient, Intent.STOP, occurred_at=at(2))  # skewed provider timestamp
    assert status_of(recipient) == ConsentStatus.WITHDRAWN


def test_rejoin_after_stop_needs_new_confirmation(recipient):
    apply_intent(recipient, Intent.STOP, occurred_at=at(0))
    assert apply_intent(recipient, Intent.JOIN, occurred_at=at(1)).reply == Purpose.CONSENT_PROMPT
    assert status_of(recipient) == ConsentStatus.PENDING
    assert not can_send(recipient, Channel.SMS, MessageClass.ADVISORY).allowed


def test_help_changes_nothing(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=at(0))
    assert apply_intent(recipient, Intent.HELP, occurred_at=at(1)).reply == Purpose.HELP
    assert status_of(recipient) == ConsentStatus.PENDING


def test_unrecognised_text_gets_no_reply(recipient):
    assert apply_intent(recipient, Intent.UNKNOWN, occurred_at=at(0)).reply is None


@pytest.mark.parametrize("status", list(ConsentStatus))
def test_only_active_consent_allows_advisory(recipient, status):
    Consent.objects.create(recipient=recipient, channel=Channel.SMS, status=status, policy_version="v1")
    assert can_send(recipient, Channel.SMS, MessageClass.ADVISORY).allowed is (status == ConsentStatus.ACTIVE)
    assert can_send(recipient, Channel.SMS, MessageClass.TRANSACTIONAL).allowed


def test_gate_fails_closed_for_missing_consent_and_other_channels(recipient):
    assert can_send(recipient, Channel.SMS, MessageClass.ADVISORY).reason == "consent_none"
    Consent.objects.create(recipient=recipient, channel=Channel.SMS, status=ConsentStatus.ACTIVE, policy_version="v1")
    assert can_send(recipient, "whatsapp", MessageClass.ADVISORY).reason == "unsupported_channel"


def test_ledger_is_append_only(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=at(0))
    event = ConsentEvent.objects.get()
    event.outcome = "tampered"
    with pytest.raises(AppendOnlyError):
        event.save()
    with pytest.raises(AppendOnlyError):
        event.delete()
    with pytest.raises(AppendOnlyError):
        ConsentEvent.objects.update(outcome="tampered")
    with pytest.raises(AppendOnlyError):
        ConsentEvent.objects.all().delete()


def test_pending_consent_expires(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now() - timedelta(hours=100))
    assert expire_pending() == 1
    assert status_of(recipient) == ConsentStatus.EXPIRED
    assert ConsentEvent.objects.filter(source=Source.SYSTEM, event_type="expired").count() == 1


def test_late_yes_after_ttl_expires_instead_of_activating(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now() - timedelta(hours=100))
    assert apply_intent(recipient, Intent.CONFIRM, occurred_at=timezone.now()).outcome == "expired"
    assert status_of(recipient) == ConsentStatus.EXPIRED
