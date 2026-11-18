"""Small 2D geometry helpers."""

from __future__ import annotations

import math

Point = tuple[float, float]


def distance(a: Point, b: Point) -> float:
    """Return the Euclidean distance between two points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def circle_area(radius: float) -> float:
    """Return the area of a circle. A negative radius is an error."""
    if radius < 0:
        raise ValueError("radius must be zero or more")
    return math.pi * radius * radius


def rectangle_area(width: float, height: float) -> float:
    """Return the area of a rectangle. Negative sides are an error."""
    if width < 0 or height < 0:
        raise ValueError("sides must be zero or more")
    return width * height


def polygon_area(points: list[Point]) -> float:
    """Return the area of a simple polygon with the shoelace formula."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def centroid(points: list[Point]) -> Point:
    """Return the mean point of a non-empty list of points."""
    if not points:
        raise ValueError("points must not be empty")
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
