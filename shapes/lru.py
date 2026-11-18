"""A small least-recently-used cache."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    """Keep at most `capacity` items. Drop the least recently used item first."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be 1 or more")
        self.capacity = capacity
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K, default: V | None = None) -> V | None:
        """Return the value and mark the key as recently used."""
        if key not in self._items:
            return default
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: K, value: V) -> None:
        """Store the value. Drop the oldest item if the cache is full."""
        self._items[key] = value
        self._items.move_to_end(key)
        if len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def keys(self) -> list[K]:
        """Return keys from least to most recently used."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        return key in self._items
