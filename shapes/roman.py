"""Convert integers to and from Roman numerals (1 to 3999)."""

from __future__ import annotations

_PAIRS: tuple[tuple[int, str], ...] = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def to_roman(number: int) -> str:
    """Return the Roman numeral for a number from 1 to 3999."""
    if not 1 <= number <= 3999:
        raise ValueError("number must be from 1 to 3999")
    parts: list[str] = []
    for value, symbol in _PAIRS:
        count, number = divmod(number, value)
        parts.append(symbol * count)
    return "".join(parts)


def from_roman(text: str) -> int:
    """Return the integer for a canonical Roman numeral."""
    upper = text.strip().upper()
    total = 0
    index = 0
    for value, symbol in _PAIRS:
        while upper.startswith(symbol, index):
            total += value
            index += len(symbol)
    if index != len(upper) or not upper or to_roman(total) != upper:
        raise ValueError(f"not a canonical Roman numeral: {text!r}")
    return total
