"""Decide which search results are Indian-rupee PlayStation gift cards."""

from __future__ import annotations

import re

_FACE_VALUE = re.compile(
    r"(?:₹|\brs\b\.?|\binr\b)\s*([0-9][0-9,]{1,8})"  # ₹1,000 / Rs. 2000 / INR 3000
    r"|\b([0-9][0-9,]{1,8})\s*(?:₹|\brs\b|\binr\b|\brupees?\b)",  # 1000 INR / 500 Rs
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    text = text.lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def _term_pattern(term: str) -> re.Pattern[str]:
    # Whole-word match (so "us" does not hit "usb"), tolerating plurals.
    escaped = re.escape(_normalise(term))
    return re.compile(rf"(?<![a-z0-9]){escaped}(?:s|es)?(?![a-z0-9])")


class TitleFilter:
    """A title matches when it contains at least one term from *every* group in
    ``require_any`` and no term from ``exclude``."""

    def __init__(self, require_any: list[list[str]], exclude: list[str]):
        self._groups = [[_term_pattern(t) for t in group] for group in require_any if group]
        self._exclude = [(t, _term_pattern(t)) for t in exclude]

    def why_not(self, title: str) -> str | None:
        """None when the title matches, otherwise a short reason."""
        norm = _normalise(title)
        for term, pattern in self._exclude:
            if pattern.search(norm):
                return f"excluded term {term!r}"
        for group in self._groups:
            if not any(p.search(norm) for p in group):
                return "missing required term"
        return None

    def matches(self, title: str) -> bool:
        return self.why_not(title) is None


def face_value(title: str) -> int | None:
    """Rupee denomination printed in a gift-card title, e.g. 2000 for 'Rs. 2,000'."""
    m = _FACE_VALUE.search(title or "")
    if not m:
        return None
    value = int((m.group(1) or m.group(2)).replace(",", ""))
    return value if value >= 100 else None
