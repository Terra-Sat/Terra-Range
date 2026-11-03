import pytest

from apps.messaging.core.keywords import Intent, parse_intent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("JOIN", Intent.JOIN), ("  join  ", Intent.JOIN), ("Jiunge", Intent.JOIN),
        ("YES", Intent.CONFIRM), ("yes.", Intent.CONFIRM), ("Ndiyo!", Intent.CONFIRM),
        ("STOP", Intent.STOP), ("stop", Intent.STOP), ("Stop!!", Intent.STOP),
        ("  S T O P", Intent.UNKNOWN), ("STOP ALL", Intent.STOP), ("stop please", Intent.STOP),
        ("ACHA", Intent.STOP), ("acha.", Intent.STOP),
        ("HELP", Intent.HELP), ("Msaada", Intent.HELP),
        ("", Intent.UNKNOWN), ("hello", Intent.UNKNOWN), ("yes please join", Intent.UNKNOWN),
        (None, Intent.UNKNOWN), (123, Intent.UNKNOWN),
    ],
)
def test_parse_intent(text, expected):
    assert parse_intent(text) == expected


def test_nbsp_and_tabs_are_whitespace():
    assert parse_intent("\u00a0STOP\t") == Intent.STOP
