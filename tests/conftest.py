from __future__ import annotations

from pathlib import Path

import pytest

from scanamzn.config import from_dict
from scanamzn.fetcher import FetchError, Fetcher, Page
from scanamzn.notifiers import Message

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def product_html(title: str, available: bool, price: str = "₹1,000.00") -> str:
    """A minimal product page in Amazon's markup."""
    if available:
        buybox = (
            '<div id="availability"><span class="a-color-success">In stock</span></div>'
            '<input id="add-to-cart-button" type="submit" value="Add to Cart">'
        )
        core = f'<span class="a-price priceToPay"><span class="a-offscreen">{price}</span></span>'
    else:
        buybox = '<div id="availability"><span class="a-color-price">Currently unavailable.</span></div>'
        core = ""
    return (
        f'<html><body><span id="productTitle">{title}</span>'
        f'<div id="corePriceDisplay_desktop_feature_div">{core}</div>{buybox}</body></html>'
    )


class FakeFetcher(Fetcher):
    """Serves canned pages by URL; unknown URLs return a 404 page."""

    def __init__(self):
        super().__init__("www.amazon.in", backend="requests")
        self.pages: dict[str, tuple[int, str]] = {}
        self.requests: list[str] = []
        self.resets = 0
        self.fail: set[str] = set()

    def set_product(self, asin: str, html: str, status: int = 200) -> None:
        self.pages[self.product_url(asin)] = (status, html)

    def set_search(self, query: str, html: str, status: int = 200) -> None:
        self.pages[self.search_url(query)] = (status, html)

    def get(self, url: str) -> Page:
        self.requests.append(url)
        if url in self.fail:
            raise FetchError("connection reset")
        status, html = self.pages.get(url, (404, fixture("not_found.html")))
        return Page(url, status, html)

    def reset(self) -> None:
        self.resets += 1


class FakeDispatcher:
    def __init__(self):
        self.messages: list[Message] = []

    def send(self, message: Message):
        self.messages.append(message)
        return []

    def close(self):
        pass


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_settings(**overrides):
    import tempfile

    data = {
        "amazon": {"asins": [], "search_queries": [], "watch_known_listings": False},
        # The in-stock self-test has its own tests; keep it out of the others.
        "monitor": {"interval_seconds": 30, "request_delay_seconds": [0, 0], "selftest_every_hours": 0,
                    "debug_dump_dir": tempfile.mkdtemp(prefix="scanamzn-debug-")},
    }
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return from_dict(data)


@pytest.fixture
def fetcher():
    return FakeFetcher()


@pytest.fixture
def dispatcher():
    return FakeDispatcher()


@pytest.fixture
def clock():
    return FakeClock()
