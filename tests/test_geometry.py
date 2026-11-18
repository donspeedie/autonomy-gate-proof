import math

import pytest

from shapes import geometry


def test_distance_is_pythagorean() -> None:
    assert geometry.distance((0, 0), (3, 4)) == 5


def test_circle_area() -> None:
    assert geometry.circle_area(2) == pytest.approx(4 * math.pi)


def test_circle_area_rejects_negative_radius() -> None:
    with pytest.raises(ValueError):
        geometry.circle_area(-1)


def test_rectangle_area() -> None:
    assert geometry.rectangle_area(3, 5) == 15


def test_polygon_area_of_unit_square() -> None:
    assert geometry.polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]) == 1


def test_polygon_area_needs_three_points() -> None:
    assert geometry.polygon_area([(0, 0), (1, 1)]) == 0


def test_centroid() -> None:
    assert geometry.centroid([(0, 0), (2, 0), (2, 2), (0, 2)]) == (1, 1)


def test_centroid_rejects_empty() -> None:
    with pytest.raises(ValueError):
        geometry.centroid([])
