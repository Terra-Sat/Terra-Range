import pytest

from apps.messaging import location_flow as flow
from apps.messaging.enums import Purpose
from apps.messaging.models import Location, Recipient

pytestmark = pytest.mark.django_db


@pytest.fixture
def person(db):
    return Recipient.objects.create(phone="+254712345678")


def walk(person, *replies):
    last = None
    for text in replies:
        last = flow.answer(person, text)
        person.refresh_from_db()
    return last


def test_nothing_to_ask_when_no_locations_are_loaded(person):
    assert flow.start(person) is None
    assert person.awaiting_location is False


def test_first_question_lists_the_top_level(person, tree):
    reply = flow.start(person)
    assert reply.purpose == Purpose.LOCATION_PROMPT
    assert "which county" in reply.body and "1. Marsabit" in reply.body and "2. Turkana" in reply.body
    assert "BACK" not in reply.body  # nothing above the top level
    assert person.awaiting_location is True


def test_choosing_by_number_reaches_a_ward_through_every_level(person, tree):
    flow.start(person)
    step = walk(person, "1")
    assert "which sub county" in step.reply.body and "1. Moyale" in step.reply.body and "2. North Horr" in step.reply.body
    step = walk(person, "2")
    assert "which ward" in step.reply.body and "1. Dukana" in step.reply.body
    done = walk(person, "1")
    assert done.outcome == "location_saved"
    assert done.reply.purpose == Purpose.LOCATION_CONFIRMATION
    assert "Dukana, North Horr, Marsabit" in done.reply.body
    assert person.location == tree["dukana"] and person.ward_code == "W-DUKANA"
    assert person.awaiting_location is False and person.location_cursor is None


def test_choosing_by_name_exact_prefix_and_fragment(person, tree):
    flow.start(person)
    walk(person, "marsabit")
    assert person.location_cursor == tree["marsabit"]
    walk(person, "Moy")  # unique prefix
    assert person.location_cursor == tree["moyale"]
    done = walk(person, "olo")  # unique fragment of Sololo
    assert done.outcome == "location_saved" and person.location == tree["sololo"]


def test_a_name_that_matches_a_node_and_its_child_resolves_per_level(person, tree):
    flow.start(person)
    walk(person, "1", "north horr")  # the sub-county
    assert person.location_cursor == tree["northhorr"]
    walk(person, "north horr")  # the ward of the same name
    assert person.location == tree["nh_ward"]


def test_an_ambiguous_name_is_not_guessed(person, tree):
    Location.objects.create(code="KE030", name="Samburu", level="county")
    Location.objects.create(code="KE031", name="Samburu East", level="county")
    flow.start(person)
    step = walk(person, "sam")
    assert step.outcome == "location_invalid" and step.reply.body.startswith("Sorry")
    assert person.location_cursor is None and person.location is None


def test_an_exact_name_wins_over_longer_names_that_start_with_it(person, tree):
    Location.objects.create(code="KE030", name="Samburu", level="county")
    Location.objects.create(code="KE031", name="Samburu East", level="county")
    flow.start(person)
    done = walk(person, "samburu")
    assert done.outcome == "location_saved"
    assert person.location.code == "KE030" and person.awaiting_location is False


@pytest.mark.parametrize("text", ["9", "0", "banana", "", "   ", "!!!"])
def test_unusable_answers_repeat_the_question_and_keep_state(person, tree, text):
    flow.start(person)
    step = walk(person, text)
    assert step.outcome == "location_invalid"
    assert step.reply.body.startswith("Sorry, we did not understand that.")
    assert "which county" in step.reply.body
    assert person.awaiting_location is True and person.location is None


def test_back_goes_up_one_level_and_is_unavailable_at_the_top(person, tree):
    flow.start(person)
    walk(person, "1", "1")
    assert person.location_cursor == tree["moyale"]
    step = walk(person, "back")
    assert step.outcome == "location_back" and person.location_cursor == tree["marsabit"]
    walk(person, "back")
    assert person.location_cursor is None
    assert walk(person, "back").outcome == "location_invalid"


def test_zero_keeps_the_area_chosen_so_far(person, tree):
    flow.start(person)
    walk(person, "1")
    assert "0 if Marsabit is as close" in flow.reask(person).body
    done = walk(person, "0")
    assert done.outcome == "location_saved"
    assert person.location == tree["marsabit"] and person.ward_code == "KE010"


def test_zero_is_not_offered_or_accepted_at_the_top_level(person, tree):
    reply = flow.start(person)
    assert "0 if" not in reply.body
    assert walk(person, "0").outcome == "location_invalid"


def test_long_lists_are_paged_within_the_sms_budget(person, db, settings):
    county = Location.objects.create(code="C", name="County", level="county")
    for i in range(25):
        Location.objects.create(code=f"W{i:02d}", name=f"Ward number {i:02d} of the long list", level="ward", parent=county, sort_order=i)
    flow.start(person)
    first = walk(person, "1").reply
    assert len(first.body) <= settings.MESSAGING_LOCATION_SMS_MAX_CHARS
    assert first.body.count("\n") >= 3 and "MORE" in first.body and "1. Ward number 00" in first.body
    options_per_page = sum(1 for line in first.body.splitlines() if line[:1].isdigit() and ". " in line)
    assert 1 <= options_per_page <= settings.MESSAGING_LOCATION_MAX_OPTIONS
    second = walk(person, "more").reply
    assert f"1. Ward number {options_per_page:02d}" in second.body
    done = walk(person, "2")  # number 2 on page two
    assert done.outcome == "location_saved" and person.location.code == f"W{options_per_page + 1:02d}"


def test_more_wraps_around_after_the_last_page(person, db):
    county = Location.objects.create(code="C", name="County", level="county")
    for i in range(12):
        Location.objects.create(code=f"W{i:02d}", name=f"Ward number {i:02d} of the long list", level="ward", parent=county, sort_order=i)
    flow.start(person)
    first = walk(person, "1").reply.body
    bodies = [walk(person, "more").reply.body for _ in range(10)]
    assert any(body != first for body in bodies)  # there really are several pages
    assert first in bodies  # and MORE wraps back round to the first one


def test_names_are_matched_across_all_pages_not_just_the_visible_one(person, db):
    county = Location.objects.create(code="C", name="County", level="county")
    for i in range(25):
        Location.objects.create(code=f"W{i:02d}", name=f"Ward number {i:02d} of the long list", level="ward", parent=county, sort_order=i)
    flow.start(person)
    walk(person, "1")
    done = walk(person, "ward number 24 of the long list")
    assert done.outcome == "location_saved" and person.location.code == "W24"


def test_swahili_questions_and_confirmation(tree):
    person = Recipient.objects.create(phone="+254712345679", language="sw")
    assert "Jibu kwa nambari" in flow.start(person).body
    done = walk(person, "1", "0")
    assert done.reply.body.startswith("Terra Range: asante")


def test_if_the_tree_changes_mid_conversation_the_current_area_is_saved(person, tree):
    flow.start(person)
    walk(person, "1", "2")  # now asking wards under North Horr
    Location.objects.filter(parent=tree["northhorr"]).update(is_active=False)
    done = walk(person, "1")
    assert done.outcome == "location_saved" and person.location == tree["northhorr"]


def test_if_every_location_disappears_the_conversation_is_cancelled(person, tree):
    flow.start(person)
    Location.objects.update(is_active=False)
    assert walk(person, "1").outcome == "location_unavailable"
    assert person.awaiting_location is False


def test_cancel_stops_asking_but_keeps_a_saved_location(person, tree):
    flow.start(person)
    assert walk(person, "1", "0").outcome == "location_saved"
    flow.start(person)
    flow.cancel(person)
    person.refresh_from_db()
    assert person.awaiting_location is False and person.location_cursor is None
    assert person.location == tree["marsabit"]


def test_restarting_keeps_the_old_location_until_a_new_one_is_chosen(person, tree):
    flow.start(person)
    walk(person, "1", "0")
    flow.start(person)
    person.refresh_from_db()
    assert person.location == tree["marsabit"] and person.awaiting_location is True
