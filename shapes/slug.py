"""Make URL-safe slugs from text."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_length: int = 60) -> str:
    """Return a lower-case slug with single hyphens between words."""
    if max_length < 1:
        raise ValueError("max_length must be 1 or more")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _NON_WORD.sub("-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def is_slug(text: str) -> bool:
    """Return True if the text is already a valid slug."""
    return bool(text) and slugify(text, max_length=len(text)) == text


def unique_slug(text: str, taken: set[str]) -> str:
    """Return a slug that is not in the taken set, with a number suffix if needed."""
    base = slugify(text) or "item"
    if base not in taken:
        return base
    for n in range(2, len(taken) + 3):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("no free slug found")
