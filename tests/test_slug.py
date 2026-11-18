"""Tests for shapes.slug."""

import pytest

from shapes import slug


def test_slugify_basic() -> None:
    assert slug.slugify("Hello, World!") == "hello-world"


def test_slugify_rejects_bad_length() -> None:
    with pytest.raises(ValueError):
        slug.slugify("x", max_length=0)


def test_is_slug() -> None:
    assert slug.is_slug("hello-world")
    assert not slug.is_slug("Hello")


def test_unique_slug_adds_suffix() -> None:
    assert slug.unique_slug("Hello", {"hello"}) == "hello-2"
