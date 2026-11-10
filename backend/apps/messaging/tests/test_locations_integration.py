import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from apps.messaging.checks import messaging_configuration
from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent
from apps.messaging.models import Location, Recipient
from apps.messaging.recipients import active_recipients
from apps.messaging.registration import register_recipient

pytestmark = pytest.mark.django_db


def activate(recipient):
    apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now())
    apply_intent(recipient, Intent.CONFIRM, occurred_at=timezone.now())


def registered(phone, code):
    recipient, _ = register_recipient(phone=phone, ward_code=code, send_prompt=False)
    activate(recipient)
    return recipient


# --- registration + delivery-engine queries ---

def test_registering_with_a_known_code_links_the_location_at_any_level(tree):
    ward, _ = register_recipient(phone="0712000001", ward_code="W-DUKANA", send_prompt=False)
    county, _ = register_recipient(phone="0712000002", ward_code="ke010", send_prompt=False)  # case-insensitive
    assert ward.location == tree["dukana"] and ward.ward_code == "W-DUKANA"
    assert county.location == tree["marsabit"] and county.ward_code == "KE010"


def test_an_unknown_code_is_kept_as_plain_text_for_older_data(tree):
    recipient, _ = register_recipient(phone="0712000003", ward_code="NORTH-HORR", send_prompt=False)
    assert recipient.location is None and recipient.ward_code == "NORTH-HORR"


def test_registering_again_with_a_location_fills_a_missing_one(tree):
    first, _ = register_recipient(phone="0712000004", send_prompt=False)
    again, created = register_recipient(phone="0712000004", ward_code="W-SOLOLO", send_prompt=False)
    assert not created and again.pk == first.pk and again.location == tree["sololo"]


def test_a_county_query_returns_everyone_below_it_and_only_consented_people(tree):
    a = registered("0712000011", "W-DUKANA")
    b = registered("0712000012", "W-SOLOLO")
    c = registered("0712000013", "KE010")  # registered at county level
    registered("0712000014", "W-KANAM")  # a different county
    register_recipient(phone="0712000015", ward_code="W-DUKANA", send_prompt=False)  # never confirmed
    assert set(active_recipients(location_code="KE010")) == {a, b, c}


def test_include_descendants_false_matches_that_exact_level_only(tree):
    registered("0712000021", "W-DUKANA")
    county = registered("0712000022", "KE010")
    assert list(active_recipients(location_code="KE010", include_descendants=False)) == [county]


def test_a_sub_county_query_and_an_unknown_code(tree):
    a = registered("0712000031", "W-BUTIYE")
    registered("0712000032", "W-DUKANA")
    assert list(active_recipients(location_code="SC-MOYALE")) == [a]
    assert active_recipients(location_code="NOPE").count() == 0


def test_ward_code_filter_still_works_for_existing_callers(tree):
    a = registered("0712000041", "W-DUKANA")
    assert list(active_recipients(ward_code="W-DUKANA")) == [a]


# --- startup configuration checks ---

def ids(problems):
    return {p.id for p in problems}


@pytest.fixture
def at(settings):
    settings.DEBUG = False
    settings.MESSAGING_SMS_PROVIDER = "africastalking"
    settings.AT_USERNAME = "myapp"
    settings.AT_API_KEY = "k" * 40
    settings.AT_BASE_URL = "https://api.africastalking.com"
    settings.AT_WEBHOOK_TOKEN = "t" * 40
    return settings


def test_a_correct_live_configuration_has_no_problems(at):
    assert messaging_configuration(None) == []


def test_unknown_provider_is_an_error(settings):
    settings.MESSAGING_SMS_PROVIDER = "carrier-pigeon"
    assert ids(messaging_configuration(None)) == {"messaging.E001"}


def test_missing_credentials_are_an_error(at):
    at.AT_API_KEY = ""
    assert "messaging.E002" in ids(messaging_configuration(None))


def test_missing_webhook_token_is_an_error_in_production_and_a_warning_in_debug(at):
    at.AT_WEBHOOK_TOKEN = ""
    assert "messaging.E003" in ids(messaging_configuration(None))
    at.DEBUG = True
    assert ids(messaging_configuration(None)) == {"messaging.W002"}


def test_short_webhook_token_is_warned_about(at):
    at.AT_WEBHOOK_TOKEN = "short"
    assert "messaging.W003" in ids(messaging_configuration(None))


def test_mixing_sandbox_and_live_settings_is_warned_about(at):
    at.AT_BASE_URL = "https://api.sandbox.africastalking.com"
    assert "messaging.W004" in ids(messaging_configuration(None))
    at.AT_USERNAME = "sandbox"
    assert messaging_configuration(None) == []


def test_fake_provider_in_production_is_warned_about(settings):
    settings.MESSAGING_SMS_PROVIDER, settings.DEBUG = "fake", False
    assert ids(messaging_configuration(None)) == {"messaging.W001"}
    settings.DEBUG = True
    assert messaging_configuration(None) == []


# --- commands ---

def csv_file(tmp_path, body):
    path = tmp_path / "l.csv"
    path.write_text("code,name,level,parent_code,sort_order\n" + body)
    return str(path)


def test_load_locations_command_loads_and_reports(tmp_path):
    out = io.StringIO()
    call_command("load_locations", csv=csv_file(tmp_path, "C1,County,county,,0\nS1,Sub,sub_county,C1,0\n"), stdout=out)
    assert "2 created" in out.getvalue() and Location.objects.count() == 2


def test_load_locations_command_fails_loudly_on_a_bad_file_and_writes_nothing(tmp_path):
    with pytest.raises(CommandError, match="Nothing was loaded"):
        call_command("load_locations", csv=csv_file(tmp_path, "A,One,county,MISSING,0\n"), stderr=io.StringIO())
    assert Location.objects.count() == 0


def test_load_locations_command_reports_a_missing_file():
    with pytest.raises(CommandError, match="File not found"):
        call_command("load_locations", csv="/no/such/file.csv")


def test_list_locations_prints_the_tree_with_counts(tree):
    registered("0712000051", "W-DUKANA")
    out = io.StringIO()
    call_command("list_locations", max_depth=2, stdout=out)
    text = out.getvalue()
    assert "county=2" in text and "Dukana [ward] W-DUKANA  (1 registered)" in text
    assert "Marsabit [county] KE010  (1 registered)" in text


def test_simulate_inbound_refuses_to_run_in_production(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("simulate_inbound", phone="0712345678", text="JOIN")


def test_simulate_inbound_runs_the_real_handler_in_debug(settings):
    settings.DEBUG = True
    out = io.StringIO()
    call_command("simulate_inbound", phone="0712345678", text="JOIN", stdout=out)
    assert "outcome: pending" in out.getvalue()
    assert Recipient.objects.filter(phone="+254712345678").exists()


# --- health ---

def test_health_endpoint_reports_ok(client):
    response = client.get(reverse("messaging:health"))
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_health_endpoint_does_not_leak_details_when_the_database_is_down(client, monkeypatch):
    from django.db import connection

    def broken(*args, **kwargs):
        raise RuntimeError("password=secret host=db.internal")

    monkeypatch.setattr(connection, "cursor", broken)
    response = client.get(reverse("messaging:health"))
    assert response.status_code == 503 and "secret" not in response.content.decode()


def test_health_endpoint_is_read_only(client):
    assert client.post(reverse("messaging:health")).status_code == 405
