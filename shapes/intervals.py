"""Merge and query closed integer intervals."""

from __future__ import annotations

Interval = tuple[int, int]


def normalize(interval: Interval) -> Interval:
    """Return the interval with the smaller bound first."""
    low, high = interval
    return (low, high) if low <= high else (high, low)


def merge(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or touching intervals. Return them sorted."""
    ordered = sorted(normalize(i) for i in intervals)
    result: list[Interval] = []
    for low, high in ordered:
        if result and low <= result[-1][1] + 1:
            result[-1] = (result[-1][0], max(result[-1][1], high))
        else:
            result.append((low, high))
    return result


def total_length(intervals: list[Interval]) -> int:
    """Return the count of integers covered by the intervals."""
    return sum(high - low + 1 for low, high in merge(intervals))


def contains(intervals: list[Interval], value: int) -> bool:
    """Return True if any interval covers the value."""
    return any(low <= value <= high for low, high in merge(intervals))
