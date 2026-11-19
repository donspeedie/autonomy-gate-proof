import pytest

from shapes import lru
from shapes.lru import LRUCache


def test_module_exposes_class():
    assert lru.LRUCache is LRUCache


@pytest.mark.parametrize("capacity", [0, -1, -100])
def test_invalid_capacity_raises(capacity):
    with pytest.raises(ValueError):
        LRUCache(capacity)


def test_capacity_one_is_valid():
    cache = LRUCache(1)
    assert cache.capacity == 1
    assert len(cache) == 0


def test_put_and_get():
    cache = LRUCache(2)
    cache.put("a", 1)
    assert cache.get("a") == 1
    assert len(cache) == 1


def test_get_missing_returns_none():
    assert LRUCache(2).get("missing") is None


def test_get_missing_returns_default():
    assert LRUCache(2).get("missing", 42) == 42


def test_get_does_not_store_default():
    cache = LRUCache(2)
    cache.get("x", 5)
    assert "x" not in cache
    assert len(cache) == 0


def test_stored_none_value_is_returned_not_default():
    cache = LRUCache(2)
    cache.put("a", None)
    assert cache.get("a", "default") is None
    assert "a" in cache


def test_eviction_drops_least_recently_used():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert "a" not in cache
    assert cache.keys() == ["b", "c"]
    assert len(cache) == 2


def test_get_refreshes_recency():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert "b" not in cache
    assert cache.keys() == ["a", "c"]


def test_get_missing_does_not_change_order():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("zzz")
    assert cache.keys() == ["a", "b"]


def test_put_existing_updates_value_and_recency():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    assert cache.get("a") == 10
    assert len(cache) == 2
    cache.put("c", 3)
    assert "b" not in cache
    assert "a" in cache


def test_put_existing_does_not_evict():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("b", 3)
    assert cache.keys() == ["a", "b"]


def test_keys_order_least_to_most_recent():
    cache = LRUCache(3)
    for k in "abc":
        cache.put(k, k.upper())
    assert cache.keys() == ["a", "b", "c"]
    cache.get("a")
    assert cache.keys() == ["b", "c", "a"]


def test_keys_returns_copy():
    cache = LRUCache(2)
    cache.put("a", 1)
    keys = cache.keys()
    keys.append("b")
    assert cache.keys() == ["a"]


def test_capacity_one_evicts_on_every_new_key():
    cache = LRUCache(1)
    cache.put("a", 1)
    cache.put("b", 2)
    assert "a" not in cache
    assert cache.get("b") == 2
    assert len(cache) == 1


def test_contains_does_not_refresh_recency():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert "a" in cache
    cache.put("c", 3)
    assert "a" not in cache


def test_len_never_exceeds_capacity():
    cache = LRUCache(3)
    for i in range(20):
        cache.put(i, i * i)
        assert len(cache) <= 3
    assert cache.keys() == [17, 18, 19]


def test_various_hashable_keys():
    cache = LRUCache(3)
    cache.put((1, 2), "tuple")
    cache.put(7, "int")
    cache.put("s", "str")
    assert cache.get((1, 2)) == "tuple"
    assert cache.get(7) == "int"
    assert cache.get("s") == "str"
