from shapes import intervals


def test_merge_overlapping() -> None:
    assert intervals.merge([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]


def test_merge_touching() -> None:
    assert intervals.merge([(1, 2), (3, 4)]) == [(1, 4)]


def test_merge_reversed_bounds() -> None:
    assert intervals.merge([(5, 1)]) == [(1, 5)]


def test_merge_empty() -> None:
    assert intervals.merge([]) == []


def test_total_length() -> None:
    assert intervals.total_length([(1, 3), (2, 4), (10, 10)]) == 5


def test_contains() -> None:
    assert intervals.contains([(1, 3), (7, 9)], 8)
    assert not intervals.contains([(1, 3), (7, 9)], 5)
