import pytest

from apps.messaging.core.phone import normalise_ke


@pytest.mark.parametrize(
    "raw",
    ["0712 345 678", "+254 712 345 678", "254712345678", "0112345678", "712345678", " +254-712-345-678 "],
)
def test_valid_numbers_normalise(raw):
    result = normalise_ke(raw)
    assert result is not None
    assert result.startswith("+254") and len(result) == 13


def test_exact_outputs():
    assert normalise_ke("0712 345 678") == "+254712345678"
    assert normalise_ke("0112345678") == "+254112345678"
    assert normalise_ke("712345678") == "+254712345678"


@pytest.mark.parametrize(
    "raw",
    ["", None, 712345678, "+14155552671", "07123456789012", "abc0712345678", "0212345678", "+25471234", "12345"],
)
def test_invalid_numbers_return_none(raw):
    assert normalise_ke(raw) is None
