"""Convert lengths between a fixed set of units."""

from __future__ import annotations

_METERS_PER_UNIT: dict[str, float] = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "km": 1000.0,
    "in": 0.0254,
    "ft": 0.3048,
    "yd": 0.9144,
    "mi": 1609.344,
}


def supported_units() -> list[str]:
    """Return the unit codes in sorted order."""
    return sorted(_METERS_PER_UNIT)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a length. An unknown unit is an error."""
    for unit in (from_unit, to_unit):
        if unit not in _METERS_PER_UNIT:
            raise KeyError(f"unknown unit: {unit}")
    meters = value * _METERS_PER_UNIT[from_unit]
    return meters / _METERS_PER_UNIT[to_unit]


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit."""
    return celsius * 9.0 / 5.0 + 32.0


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """Convert a temperature from Fahrenheit to Celsius."""
    return (fahrenheit - 32.0) * 5.0 / 9.0
