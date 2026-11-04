"""The whole SMS journey through the same handler the webhook uses: JOIN, YES, then the location questions."""
import itertools

import pytest

from apps.messaging import sending
from apps.messaging.enums import Channel, ConsentStatus, DeliveryStatus, MessageClass, Purpose
from apps.messaging.inbound import handle_delivery_report, handle_inbound_sms
from apps.messaging.models import Consent, ConsentEvent, OutboundLog, Recipient
from apps.messaging.providers.base import InboundMessage, StatusEvent
from apps.messaging.providers.fake import FakeSmsProvider
from apps.messaging.registration import register_recipient

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
_ids = itertools.count(1)


def say(text, phone=PHONE, **kwargs):
    message = InboundMessage(provider_event_id=f"evt-{next(_ids)}", sender=phone, text=text, occurred_at=None)
    return handle_inbound_sms(message, **kwargs)


def last_body():
    return FakeSmsProvider.outbox[-1].body


def person(phone=PHONE):
    return Recipient.objects.get(phone=phone)


def join_and_confirm():
    say("JOIN")
    say("YES")


def test_join_then_yes_sends_welcome_then_the_first_location_question(tree):
    say("JOIN")
    assert len(FakeSmsProvider.outbox) == 1  # the consent prompt only: no question before consent
    say("YES")
    assert len(FakeSmsProvider.outbox) == 3  # + welcome + first question
    assert "which county" in last_body() and "1. Marsabit" in last_body()
    assert person().awaiting_location is True
    assert Consent.objects.get(recipient=person(), channel=Channel.SMS).status == ConsentStatus.ACTIVE


def test_answering_the_questions_registers_the_ward(tree):
    join_and_confirm()
    say("1")
    say("1")
    say("sololo")
    assert "registered for Sololo, Moyale, Marsabit" in last_body()
    recipient = person()
    assert recipient.ward_code == "W-SOLOLO" and recipient.location == tree["sololo"]
    assert recipient.awaiting_location is False


def test_location_messages_are_logged_as_transactional_with_their_own_purposes(tree):
    join_and_confirm()
    say("2")
    purposes = set(OutboundLog.objects.values_list("purpose", flat=True))
    assert Purpose.LOCATION_PROMPT in purposes
    assert set(OutboundLog.objects.filter(purpose=Purpose.LOCATION_PROMPT).values_list("message_class", flat=True)) == {MessageClass.TRANSACTIONAL}


def test_replies_before_consent_are_ignored(tree):
    say("JOIN")
    sent = len(FakeSmsProvider.outbox)
    say("1")
    recipient = person()
    assert recipient.location is None and recipient.awaiting_location is False
    assert len(FakeSmsProvider.outbox) == sent  # no reply to a stray number


def test_a_duplicate_answer_is_processed_once(tree):
    join_and_confirm()
    message = InboundMessage(provider_event_id="same-id", sender=PHONE, text="1", occurred_at=None)
    first = handle_inbound_sms(message)
    sent = len(FakeSmsProvider.outbox)
    second = handle_inbound_sms(message)
    assert second.duplicate is True and first.duplicate is False
    assert len(FakeSmsProvider.outbox) == sent
    assert person().location_cursor == tree["marsabit"]  # moved one level, not two


def test_stop_mid_conversation_ends_it_and_numbers_afterwards_do_nothing(tree):
    join_and_confirm()
    say("1")
    say("STOP")
    recipient = person()
    assert recipient.awaiting_location is False and recipient.location_cursor is None
    assert Consent.objects.get(recipient=recipient, channel=Channel.SMS).status == ConsentStatus.WITHDRAWN
    sent = len(FakeSmsProvider.outbox)
    say("1")
    assert person().location is None and len(FakeSmsProvider.outbox) == sent


def test_saying_yes_again_mid_conversation_repeats_the_question_without_a_new_consent_event(tree):
    join_and_confirm()
    events = ConsentEvent.objects.count()
    say("YES")
    assert "which county" in last_body()
    assert ConsentEvent.objects.count() == events


def test_location_keyword_restarts_the_questions_and_keeps_the_old_answer_until_replaced(tree):
    join_and_confirm()
    say("1")
    say("0")
    assert person().location == tree["marsabit"]
    say("LOCATION")
    assert "which county" in last_body() and person().awaiting_location is True
    assert person().location == tree["marsabit"]
    say("2")
    say("1")
    say("1")
    assert person().location == tree["kanam"]


def test_location_keyword_without_consent_gets_help_not_questions(tree):
    say("JOIN")
    say("LOCATION")
    assert person().awaiting_location is False
    assert "which county" not in last_body()


def test_location_keyword_from_an_unknown_number_gets_no_reply(tree):
    say("LOCATION", phone="+254799000111")
    assert FakeSmsProvider.outbox == []


def test_swahili_keyword_works(tree):
    join_and_confirm()
    say("1")
    say("0")
    say("ENEO")
    assert person().awaiting_location is True


def test_a_recipient_registered_with_a_ward_is_not_asked_again(tree):
    register_recipient(phone="0712345678", ward_code="W-DUKANA")
    say("YES")
    recipient = person()
    assert recipient.location == tree["dukana"] and recipient.awaiting_location is False
    assert all("which" not in m.body for m in FakeSmsProvider.outbox)


def test_no_questions_when_no_locations_are_loaded(db):
    join_and_confirm()
    assert person().awaiting_location is False
    assert len(FakeSmsProvider.outbox) == 2  # consent prompt and welcome


def test_status_before_a_location_is_set_points_to_the_location_keyword(tree):
    join_and_confirm()
    say("STATUS")
    assert "Reply LOCATION" in last_body()


def test_withdrawn_person_who_joins_again_keeps_their_location_and_is_not_asked(tree):
    join_and_confirm()
    say("1")
    say("0")
    say("STOP")
    say("JOIN")
    say("YES")
    assert person().location == tree["marsabit"]
    assert person().awaiting_location is False


# --- channel isolation (consent must never cross channels) ---

@pytest.fixture
def whatsapp(monkeypatch):
    monkeypatch.setattr(sending, "get_whatsapp_provider", lambda: FakeSmsProvider())


def test_a_whatsapp_yes_grants_whatsapp_consent_only(whatsapp, tree):
    say("JOIN", channel=Channel.WHATSAPP, provider="meta_whatsapp")
    say("YES", channel=Channel.WHATSAPP, provider="meta_whatsapp")
    recipient = person()
    statuses = {c.channel: c.status for c in Consent.objects.filter(recipient=recipient)}
    assert statuses == {Channel.WHATSAPP: ConsentStatus.ACTIVE}  # no SMS consent was created or granted
    assert set(OutboundLog.objects.values_list("provider", flat=True)) == {"fake"}
    assert "which county" in last_body()


def test_whatsapp_stop_does_not_withdraw_sms_consent(whatsapp, tree):
    join_and_confirm()  # SMS consent active
    say("STOP", channel=Channel.WHATSAPP, provider="meta_whatsapp")
    sms = Consent.objects.get(recipient=person(), channel=Channel.SMS)
    assert sms.status == ConsentStatus.ACTIVE


def test_same_event_id_on_two_providers_is_not_treated_as_a_duplicate(whatsapp, tree):
    msg = InboundMessage(provider_event_id="shared-id", sender=PHONE, text="JOIN", occurred_at=None)
    assert handle_inbound_sms(msg).duplicate is False
    assert handle_inbound_sms(msg, channel=Channel.WHATSAPP, provider="meta_whatsapp").duplicate is False


def test_whatsapp_delivery_reports_match_whatsapp_messages():
    OutboundLog.objects.create(
        provider="meta_whatsapp", message_class=MessageClass.ADVISORY, purpose=Purpose.ADVISORY,
        provider_message_id="wamid.1", status=DeliveryStatus.SENT,
    )
    result = handle_delivery_report(StatusEvent("wamid.1", DeliveryStatus.DELIVERED.value), provider="meta_whatsapp")
    assert result.outcome == "applied"
    assert OutboundLog.objects.get(provider_message_id="wamid.1").status == DeliveryStatus.DELIVERED
    # and an SMS report cannot touch a WhatsApp message
    other = handle_delivery_report(StatusEvent("wamid.1", DeliveryStatus.FAILED.value))
    assert other.outcome == "unknown_message"
