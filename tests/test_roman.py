import pytest

from shapes import roman


@pytest.mark.parametrize(
    "number, expected",
    [
        (1, "I"),
        (4, "IV"),
        (9, "IX"),
        (14, "XIV"),
        (40, "XL"),
        (90, "XC"),
        (400, "CD"),
        (1994, "MCMXCIV"),
        (2024, "MMXXIV"),
        (3888, "MMMDCCCLXXXVIII"),
        (3999, "MMMCMXCIX"),
    ],
)
def test_to_roman_known_values(number, expected):
    assert roman.to_roman(number) == expected


@pytest.mark.parametrize("bad", [0, -1, 4000, 10000])
def test_to_roman_out_of_range(bad):
    with pytest.raises(ValueError):
        roman.to_roman(bad)


@pytest.mark.parametrize(
    "text, expected",
    [("I", 1), ("IV", 4), ("XLII", 42), ("MCMXCIV", 1994), ("MMMCMXCIX", 3999)],
)
def test_from_roman_known_values(text, expected):
    assert roman.from_roman(text) == expected


def test_from_roman_ignores_case_and_whitespace():
    assert roman.from_roman("  mcmxciv \n") == 1994


def test_round_trip_full_range():
    for n in range(1, 4000):
        assert roman.from_roman(roman.to_roman(n)) == n


@pytest.mark.parametrize(
    "bad", ["", "   ", "IIII", "VX", "IC", "IIV", "MMMM", "ABC", "X I", "VV"]
)
def test_from_roman_rejects_invalid(bad):
    with pytest.raises(ValueError):
        roman.from_roman(bad)


def test_from_roman_error_mentions_input():
    with pytest.raises(ValueError, match="IIII"):
        roman.from_roman("IIII")
