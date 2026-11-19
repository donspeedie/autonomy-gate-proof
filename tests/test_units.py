"""Tests for shapes.units."""

import pytest

from shapes import units


def test_supported_units_sorted():
    codes = units.supported_units()
    assert codes == sorted(codes)
    assert "m" in codes and "mi" in codes


def test_convert_round_trip():
    assert units.convert(1.0, "km", "m") == pytest.approx(1000.0)
    assert units.convert(12.0, "in", "ft") == pytest.approx(1.0)
    assert units.convert(units.convert(3.5, "mi", "cm"), "cm", "mi") == pytest.approx(3.5)


def test_convert_unknown_unit():
    with pytest.raises(KeyError):
        units.convert(1.0, "m", "furlong")


def test_temperature():
    assert units.celsius_to_fahrenheit(100.0) == pytest.approx(212.0)
    assert units.fahrenheit_to_celsius(32.0) == pytest.approx(0.0)
