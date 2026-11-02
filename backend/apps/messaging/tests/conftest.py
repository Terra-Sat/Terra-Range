import json
from pathlib import Path

import pytest

from apps.messaging.providers.fake import FakeSmsProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_fake_provider():
    FakeSmsProvider.reset()
    yield
    FakeSmsProvider.reset()


@pytest.fixture
def load_fixture():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())

    return _load
