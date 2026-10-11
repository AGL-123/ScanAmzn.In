"""Plain data types shared by the parser, the monitor and the notifiers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    # Buyable, but priced above the configured limit (e.g. a scalper listing).
    OVERPRICED = "overpriced"
    # Amazon served a captcha / robot-check page instead of the real page.
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    # The page loaded but no availability signal was recognised.
    UNKNOWN = "unknown"
    # Network or HTTP failure.
    ERROR = "error"


# Statuses that say something definite about stock. Anything else leaves the
# previously known state of an item untouched.
DEFINITIVE = frozenset({Status.AVAILABLE, Status.UNAVAILABLE, Status.OVERPRICED})


@dataclass
class Product:
    asin: str
    status: Status
    title: str = ""
    price: float | None = None
    seller: str = ""
    # Which page signal decided the status, for logs and debugging.
    reason: str = ""
    # "product" (product page) or "search" (search results page).
    source: str = "product"


@dataclass
class SearchPage:
    blocked: bool = False
    products: list[Product] = field(default_factory=list)
    # False when the page looked fine but no result tiles were recognised.
    layout_ok: bool = True
