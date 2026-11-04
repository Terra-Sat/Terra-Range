import json

import pytest

from apps.messaging import locations_loader as loader
from apps.messaging.models import Location

pytestmark = pytest.mark.django_db

HEADER = "code,name,level,parent_code,sort_order\n"


def write(tmp_path, text, name="locations.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_csv_load_builds_the_tree_at_any_depth(tmp_path):
    path = write(tmp_path, HEADER + "C1,County One,county,,0\nS1,Sub One,sub_county,C1,0\nW1,Ward One,ward,S1,0\nV1,Village One,village,W1,0\n")
    summary = loader.load(loader.rows_from_csv(path))
    assert (summary.created, summary.updated, summary.unchanged) == (4, 0, 0)
    assert Location.objects.get(code="V1").path == "|C1|S1|W1|V1|"


def test_loading_twice_is_idempotent(tmp_path):
    rows = loader.rows_from_csv(write(tmp_path, HEADER + "C1,County One,county,,0\nS1,Sub One,sub_county,C1,0\n"))
    loader.load(rows)
    second = loader.load(rows)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)
    assert Location.objects.count() == 2


def test_rows_can_be_listed_children_before_parents(tmp_path):
    rows = loader.rows_from_csv(write(tmp_path, HEADER + "W1,Ward,ward,S1,0\nS1,Sub,sub_county,C1,0\nC1,County,county,,0\n"))
    loader.load(rows)
    assert Location.objects.get(code="W1").path == "|C1|S1|W1|"


def test_moving_a_node_moves_its_whole_subtree(tmp_path, tree):
    rows = [loader.Row("SC-NH", "North Horr", "sub_county", "KE023")]  # North Horr moves under Turkana
    summary = loader.load(rows)
    assert summary.updated == 1
    assert Location.objects.get(code="W-DUKANA").path == "|KE023|SC-NH|W-DUKANA|"


def test_renaming_updates_the_search_name(tmp_path, tree):
    loader.load([loader.Row("W-BUTIYE", "Butiye East", "ward", "SC-MOYALE")])
    assert Location.objects.get(code="W-BUTIYE").search_name == "BUTIYE EAST"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("A,One,county,,0\nA,Two,county,,0\n", "more than once"),
        ("A,One,county,MISSING,0\n", "does not exist"),
        ("A,One,county,A,0\n", "own parent"),
        ("A,One,county,B,0\nB,Two,county,A,0\n", "loop"),
        ("A,One,county,,0\nB,One,county,,0\n", "same name"),
        ("bad code!,One,county,,0\n", "code must be"),
        (",One,county,,0\n", "code must be"),
        ("A,,county,,0\n", "name is required"),
        ("A,One,,,0\n", "level is required"),
    ],
)
def test_invalid_input_is_rejected_and_nothing_is_written(tmp_path, body, fragment):
    with pytest.raises(loader.LoadError) as caught:
        loader.load(loader.rows_from_csv(write(tmp_path, HEADER + body)))
    assert fragment in " ".join(caught.value.errors)
    assert Location.objects.count() == 0


def test_a_name_clash_with_an_existing_sibling_is_rejected(tree):
    with pytest.raises(loader.LoadError):
        loader.load([loader.Row("W-NEW", "dukana", "ward", "SC-NH")])  # Dukana already exists under North Horr


def test_csv_with_missing_columns_is_rejected(tmp_path):
    with pytest.raises(loader.LoadError):
        loader.rows_from_csv(write(tmp_path, "code,name\nA,One\n"))


def test_dry_run_changes_nothing(tmp_path):
    rows = loader.rows_from_csv(write(tmp_path, HEADER + "C1,County,county,,0\n"))
    summary = loader.load(rows, dry_run=True)
    assert summary.created == 1 and Location.objects.count() == 0


def test_deactivate_missing_hides_locations_that_left_the_file(tree):
    summary = loader.load([loader.Row("KE010", "Marsabit", "county")], deactivate_missing=True)
    assert summary.deactivated == len(tree) - 1
    assert Location.objects.get(code="KE023").is_active is False
    assert Location.objects.get(code="KE010").is_active is True


def test_wards_geojson_builds_county_subcounty_and_ward(tmp_path):
    def feature(uid, ward, sc_id, sub):
        return {"type": "Feature", "geometry": None, "properties": {
            "county": "MARSABIT", "county_code_linked": "KE010", "county_name_linked": "Marsabit",
            "scuid": sc_id, "subcounty": sub, "uid": uid, "ward": ward}}

    data = {"type": "FeatureCollection", "features": [
        feature("u1", "HEILLU/MANYATTA", "s1", "MOYALE"), feature("u2", "GOLBO", "s1", "MOYALE"),
        feature("u3", "DUKANA", "s2", "NORTH HORR"),
    ]}
    path = write(tmp_path, json.dumps(data), "wards.geojson")
    loader.load(loader.rows_from_wards_geojson(path))
    assert Location.objects.filter(level="county").count() == 1
    assert Location.objects.filter(level="sub_county").count() == 2
    ward = Location.objects.get(code="u1")
    assert ward.name == "Heillu/Manyatta" and ward.label() == "Heillu/Manyatta, Moyale, Marsabit"


def test_wards_geojson_with_a_missing_field_is_rejected(tmp_path):
    data = {"features": [{"properties": {"county": "MARSABIT", "ward": "X"}}]}
    with pytest.raises(loader.LoadError):
        loader.rows_from_wards_geojson(write(tmp_path, json.dumps(data), "w.geojson"))


def test_the_projects_own_ward_file_loads(tmp_path):
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "data" / "wards.geojson"
    if not path.exists():
        pytest.skip("data/wards.geojson not present")
    loader.load(loader.rows_from_wards_geojson(str(path)))
    assert Location.objects.filter(level="ward").count() >= 1
    assert not Location.objects.filter(level="ward", parent__level="county").exists()
