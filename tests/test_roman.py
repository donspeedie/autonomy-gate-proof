"""Tests for shapes.roman."""

import pytest

from shapes import roman


def test_to_roman() -> None:
    assert roman.to_roman(1994) == "MCMXCIV"


def test_from_roman() -> None:
    assert roman.from_roman("MCMXCIV") == 1994


def test_to_roman_rejects_zero() -> None:
    with pytest.raises(ValueError):
        roman.to_roman(0)


def test_from_roman_rejects_non_canonical() -> None:
    with pytest.raises(ValueError):
        roman.from_roman("IIII")
