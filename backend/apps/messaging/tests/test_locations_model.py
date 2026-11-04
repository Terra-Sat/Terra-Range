import pytest

from apps.messaging.models import MAX_LOCATION_DEPTH, Location

pytestmark = pytest.mark.django_db


def test_depth_and_materialised_path_follow_the_parents(tree):
    ward = tree["dukana"]
    assert ward.depth == 2 and ward.path == "|KE010|SC-NH|W-DUKANA|"
    assert tree["marsabit"].depth == 0 and tree["marsabit"].path == "|KE010|"


def test_everything_below_a_county_is_one_prefix_query(tree):
    below = set(Location.objects.filter(path__startswith=tree["marsabit"].path).values_list("code", flat=True))
    assert below == {"KE010", "SC-MOYALE", "SC-NH", "W-BUTIYE", "W-SOLOLO", "W-DUKANA", "W-MAIKONA", "W-NH"}


def test_a_code_that_is_a_prefix_of_another_does_not_match_it(db):
    short = Location.objects.create(code="KE1", name="One", level="county")
    Location.objects.create(code="KE10", name="Ten", level="county")
    assert list(Location.objects.filter(path__startswith=short.path).values_list("code", flat=True)) == ["KE1"]


def test_label_lists_the_chain_from_the_leaf_up(tree):
    assert tree["dukana"].label() == "Dukana, North Horr, Marsabit"


def test_code_cannot_contain_the_path_separator(db):
    with pytest.raises(ValueError):
        Location.objects.create(code="A|B", name="Bad", level="county")


def test_depth_is_capped(db):
    parent = None
    for i in range(MAX_LOCATION_DEPTH):
        parent = Location.objects.create(code=f"L{i}", name=f"Level {i}", level="x", parent=parent)
    with pytest.raises(ValueError):
        Location.objects.create(code="TOO-DEEP", name="Too deep", level="x", parent=parent)


def test_rebuild_paths_repairs_stale_paths(tree):
    Location.objects.update(path="stale", depth=0)
    Location.rebuild_paths()
    ward = Location.objects.get(code="W-DUKANA")
    assert ward.path == "|KE010|SC-NH|W-DUKANA|" and ward.depth == 2
