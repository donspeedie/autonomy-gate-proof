import pytest

from shapes import slug


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hello World", "hello-world"),
        ("  spaced   out  ", "spaced-out"),
        ("Already-a-slug", "already-a-slug"),
        ("a!!b??c", "a-b-c"),
        ("Café Münster", "cafe-munster"),
        ("Python 3.12", "python-3-12"),
        ("", ""),
        ("!!!", ""),
        ("日本語", ""),
    ],
)
def test_slugify(text, expected):
    assert slug.slugify(text) == expected


def test_slugify_truncates():
    assert slug.slugify("abcdefghij", max_length=5) == "abcde"


def test_slugify_truncation_strips_trailing_hyphen():
    assert slug.slugify("abcd efgh", max_length=5) == "abcd"


def test_slugify_default_max_length():
    assert len(slug.slugify("a" * 100)) == 60


@pytest.mark.parametrize("bad", [0, -1])
def test_slugify_invalid_max_length(bad):
    with pytest.raises(ValueError):
        slug.slugify("x", max_length=bad)


@pytest.mark.parametrize("text", ["hello", "hello-world", "a1-b2", "x"])
def test_is_slug_true(text):
    assert slug.is_slug(text) is True


@pytest.mark.parametrize(
    "text", ["", "Hello", "hello world", "-hello", "hello-", "a--b", "café"]
)
def test_is_slug_false(text):
    assert slug.is_slug(text) is False


def test_unique_slug_free():
    assert slug.unique_slug("Hello World", set()) == "hello-world"


def test_unique_slug_taken():
    assert slug.unique_slug("Hello", {"hello"}) == "hello-2"


def test_unique_slug_skips_taken_suffixes():
    taken = {"hello", "hello-2", "hello-3"}
    assert slug.unique_slug("Hello", taken) == "hello-4"


def test_unique_slug_empty_text_uses_item():
    assert slug.unique_slug("!!!", set()) == "item"
    assert slug.unique_slug("", {"item"}) == "item-2"


def test_unique_slug_does_not_mutate_taken():
    taken = {"a"}
    slug.unique_slug("a", taken)
    assert taken == {"a"}
