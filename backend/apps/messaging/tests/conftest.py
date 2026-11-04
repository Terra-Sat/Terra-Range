import json
from pathlib import Path

import pytest
from django.core.cache import cache

from apps.messaging.providers.fake import FakeSmsProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_fake_provider():
    FakeSmsProvider.reset()
    cache.clear()
    yield
    FakeSmsProvider.reset()


@pytest.fixture
def load_fixture():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())

    return _load


@pytest.fixture
def tree(db):
    """A small county > sub-county > ward tree. Returns the nodes by short key."""
    from apps.messaging.models import Location

    def make(code, name, level, parent=None, order=0):
        return Location.objects.create(code=code, name=name, level=level, parent=parent, sort_order=order)

    nodes = {}
    nodes["marsabit"] = make("KE010", "Marsabit", "county")
    nodes["turkana"] = make("KE023", "Turkana", "county")
    nodes["moyale"] = make("SC-MOYALE", "Moyale", "sub_county", nodes["marsabit"])
    nodes["northhorr"] = make("SC-NH", "North Horr", "sub_county", nodes["marsabit"])
    nodes["butiye"] = make("W-BUTIYE", "Butiye", "ward", nodes["moyale"])
    nodes["sololo"] = make("W-SOLOLO", "Sololo", "ward", nodes["moyale"])
    nodes["dukana"] = make("W-DUKANA", "Dukana", "ward", nodes["northhorr"])
    nodes["maikona"] = make("W-MAIKONA", "Maikona", "ward", nodes["northhorr"])
    nodes["nh_ward"] = make("W-NH", "North Horr", "ward", nodes["northhorr"])  # same name as its parent: allowed
    nodes["tcentral"] = make("SC-TC", "Turkana Central", "sub_county", nodes["turkana"])
    nodes["kanam"] = make("W-KANAM", "Kanamkemer", "ward", nodes["tcentral"])
    return nodes
