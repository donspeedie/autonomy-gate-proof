"""Tests for shapes.lru."""

import pytest

from shapes.lru import LRUCache


def test_evicts_least_recently_used() -> None:
    cache: LRUCache[str, int] = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.keys() == ["a", "c"]
    assert "b" not in cache
    assert len(cache) == 2


def test_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError):
        LRUCache(0)
