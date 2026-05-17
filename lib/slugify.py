"""Canonical id generation: lowercase, ASCII, snake_case, max 80 chars."""

from __future__ import annotations

import re
import unicodedata

MAX_LEN = 80

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MULTIPLE_UNDERSCORES = re.compile(r"_+")


class SlugifyError(ValueError):
    """Raised when a label cannot be turned into a valid slug."""


def slugify(label: str) -> str:
    """Return a stable snake_case id derived from ``label``.

    Rules: NFKD decompose to strip accents, lowercase, replace any run of
    non-alphanumerics by ``_``, collapse repeated ``_``, trim, truncate to
    ``MAX_LEN`` characters at the last ``_`` boundary before the limit.
    """
    if label is None:
        raise SlugifyError("label is None")
    normalized = unicodedata.normalize("NFKD", label)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    replaced = _NON_ALNUM.sub("_", lowered)
    collapsed = _MULTIPLE_UNDERSCORES.sub("_", replaced)
    trimmed = collapsed.strip("_")
    if not trimmed:
        raise SlugifyError(f"label produced empty slug: {label!r}")
    if len(trimmed) <= MAX_LEN:
        return trimmed
    cut = trimmed[:MAX_LEN]
    boundary = cut.rfind("_")
    if boundary >= MAX_LEN // 2:
        cut = cut[:boundary]
    return cut.strip("_")
