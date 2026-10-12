"""Parse Amazon.in product and search pages into availability information.

Amazon's markup changes often and differs between product types, so the
product-page verdict combines several independent signals (buy buttons,
"see all buying options", the #availability text) and only falls back to
UNKNOWN when none of them is present.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .models import Product, SearchPage, Status

try:  # lxml is several times faster on Amazon's ~1 MB pages.
    import lxml  # noqa: F401

    _HTML_PARSER = "lxml"
except ImportError:  # pragma: no cover - depends on the environment
    _HTML_PARSER = "html.parser"

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

_BLOCK_MARKERS = (
    "/errors/validatecaptcha",
    "enter the characters you see below",
    "type the characters you see in this image",
    "make sure you're not a robot",
    "make sure you&#39;re not a robot",
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
)
_NOT_FOUND_MARKER = "the web address you entered is not a functioning page"
# Present only on real pages; a page carrying one is never a robot/404 page.
_REAL_PAGE_MARKERS = ('id="productTitle"', 'data-component-type="s-search-result"')

# Main buy-box buttons. Deliberately ids only: generic add-to-cart inputs also
# appear in "frequently bought together" widgets for *other* products.
_BUY_BUTTONS = (
    "#add-to-cart-button",
    "#buy-now-button",
    "#add-to-cart-button-ubb",
    "#gc-buy-box-atc",
)
# Shown when no seller wins the buy box but offers exist.
_OTHER_OFFERS = (
    "#buybox-see-all-buying-choices",
    "#buybox-see-all-buying-choices-announce",
)
_AVAILABILITY = ("#availability", "#availability_feature_div", "#outOfStock")
_OUT_OF_STOCK_BOXES = ("#outOfStock", "#outOfStockBuyBox_feature_div")

_NEGATIVE = re.compile(
    r"currently unavailable|out of stock|temporarily unavailable|not available|\bunavailable\b"
    r"|back in stock|don't know when|don’t know when"
)
_POSITIVE = re.compile(
    r"\bin stock\b|only \d+ left|available from these sellers|\bavailable\b"
    r"|usually (?:dispatched|ships)|dispatched within|ships within"
)
_OTHER_SELLERS = re.compile(r"available from these sellers|see all buying options")
# Shown to visitors whose (guessed) delivery location is outside the seller's
# area, e.g. when browsing Amazon.in from abroad. The item itself is in stock.
_UNDELIVERABLE = re.compile(
    r"cannot be (?:delivered|shipped|dispatched) to your (?:selected )?(?:delivery )?(?:location|address|pincode)"
    r"|(?:does not|doesn't) (?:ship|deliver) to|not (?:deliverable|available for delivery) (?:to|in) your"
)
_BUY_BOX = ("#desktop_buybox", "#buybox", "#rightCol")

# Buy-box price containers, most specific first. Product pages carry many other
# prices (carousels, "customers also bought"), so never search the whole page.
_PRICE_CONTAINERS = (
    "#corePriceDisplay_desktop_feature_div .priceToPay",
    "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price)",
    "#corePrice_feature_div .a-price:not(.a-text-price)",
    "#corePrice_desktop .a-price:not(.a-text-price)",
    "#apex_desktop .priceToPay",
    "#apex_desktop .a-price:not(.a-text-price)",
    "#tp_price_block_total_price_ww",
    "#price_inside_buybox",
    "#newBuyBoxPrice",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#buybox .a-price:not(.a-text-price)",
    "#buybox-see-all-buying-choices .a-color-price",
)
_SELLER = (
    "#sellerProfileTriggerId",
    "#merchantInfoFeature_feature_div .offer-display-feature-text-message",
    "[offer-display-feature-name='desktop-merchant-info'] .offer-display-feature-text-message",
    "#merchant-info",
)
_TITLE = ("#productTitle", "#title", "#gc-asin-title")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, _HTML_PARSER)


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return _WS.sub(" ", el.get_text(" ", strip=True)).strip()


def _first_text(root: Tag, selectors: tuple[str, ...]) -> str:
    for sel in selectors:
        txt = _text(root.select_one(sel))
        if txt:
            return txt
    return ""


def parse_price(text: str) -> float | None:
    """Return the first number in a price string such as '₹1,000.00'."""
    m = _NUMBER.search(text or "")
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def _price_of(el: Tag) -> float | None:
    # ".a-offscreen" holds the full price ("₹1,000.00"); the visible part is
    # split into symbol/whole/fraction spans, which we use as a fallback.
    offscreen = el.select_one(".a-offscreen") if "a-offscreen" not in (el.get("class") or []) else el
    for candidate in (offscreen, el.select_one(".a-price-whole"), el):
        price = parse_price(_text(candidate))
        if price:
            return price
    return None


def is_blocked(html: str, status_code: int = 200) -> bool:
    """True for captcha / robot-check / throttling responses."""
    if status_code in (429, 503):
        return True
    if any(marker in html for marker in _REAL_PAGE_MARKERS):
        return False
    lower = html.lower()
    return any(marker in lower for marker in _BLOCK_MARKERS)


def _is_not_found(html: str, status_code: int) -> bool:
    if status_code in (404, 410):
        return True
    if any(marker in html for marker in _REAL_PAGE_MARKERS):
        return False
    return _NOT_FOUND_MARKER in html.lower()


def _enabled(button: Tag) -> bool:
    if button.has_attr("disabled"):
        return False
    for parent in list(button.parents)[:3]:
        if "a-button-disabled" in (parent.get("class") or []):
            return False
    return True


def _page_title(soup: BeautifulSoup) -> str:
    title = _first_text(soup, _TITLE)
    if title:
        return title
    # <title> looks like "Amazon.in: Sony PlayStation ... : Video Games".
    raw = _text(soup.title)
    raw = re.sub(r"^Amazon\.in\s*:\s*", "", raw)
    return raw.split(" : ")[0].strip()


def parse_product(html: str, asin: str, status_code: int = 200) -> Product:
    """Decide whether the product on an Amazon.in product page can be bought."""
    if is_blocked(html, status_code):
        return Product(asin, Status.BLOCKED, reason="captcha/robot check")
    if _is_not_found(html, status_code):
        return Product(asin, Status.NOT_FOUND, reason=f"HTTP {status_code} / dog page")
    if status_code >= 400:
        return Product(asin, Status.ERROR, reason=f"HTTP {status_code}")

    soup = _soup(html)
    product = Product(asin, Status.UNKNOWN, title=_page_title(soup))
    product.seller = _first_text(soup, _SELLER)[:80]
    for sel in _PRICE_CONTAINERS:
        el = soup.select_one(sel)
        if el is not None and (price := _price_of(el)):
            product.price = price
            break

    availability = " ".join(_text(soup.select_one(s)) for s in _AVAILABILITY).lower().strip()
    other_offers = any(soup.select_one(s) is not None for s in _OTHER_OFFERS)

    buy_button = next(
        (sel for sel in _BUY_BUTTONS if (b := soup.select_one(sel)) is not None and _enabled(b)), None
    )
    if buy_button:
        product.status, product.reason = Status.AVAILABLE, f"buy button {buy_button}"
    elif other_offers or _OTHER_SELLERS.search(availability):
        product.status, product.reason = Status.AVAILABLE, "offers from other sellers"
        product.seller = product.seller or "other sellers"
    elif _NEGATIVE.search(availability) or any(
        _text(soup.select_one(s)) for s in _OUT_OF_STOCK_BOXES
    ):
        product.status, product.reason = Status.UNAVAILABLE, availability[:80] or "out-of-stock box"
    elif _UNDELIVERABLE.search(availability + " " + _first_text(soup, _BUY_BOX).lower()):
        product.status = Status.AVAILABLE
        product.reason = "in stock, but Amazon says it can't deliver to your location"
    elif _POSITIVE.search(availability):
        # Positive wording without a recognised button: report it anyway, a
        # false alarm is much cheaper than a missed restock.
        product.status, product.reason = Status.AVAILABLE, availability[:80]
    else:
        product.reason = "no availability signal found"
    return product


def parse_search(html: str, status_code: int = 200) -> SearchPage:
    """Extract result tiles (ASIN, title, price, availability) from a search page."""
    if is_blocked(html, status_code):
        return SearchPage(blocked=True)
    if status_code >= 400:
        return SearchPage(layout_ok=False)

    soup = _soup(html)
    tiles = soup.select('div[data-component-type="s-search-result"][data-asin]')
    page = SearchPage()
    if not tiles:
        # A genuine "no results" page is fine; anything else is a layout change.
        page.layout_ok = "no results for" in html.lower()
        return page

    seen: set[str] = set()
    for tile in tiles:
        asin = (tile.get("data-asin") or "").strip()
        if not asin or asin in seen:
            continue
        seen.add(asin)
        title_el = tile.select_one('[data-cy="title-recipe"]')
        title = _text(title_el) if title_el else " ".join(_text(h) for h in tile.select("h2"))
        title = re.sub(r"^(?:Sponsored\s*(?:Ad)?\s*[-–]?\s*)+", "", title).strip()
        product = Product(asin, Status.UNAVAILABLE, title=title, source="search")

        tile_text = _text(tile).lower()
        price_el = tile.select_one(".a-price:not(.a-text-price)")
        secondary = tile.select_one('[data-cy="secondary-offer-recipe"]')
        if "currently unavailable" in tile_text or "out of stock" in tile_text:
            product.reason = "currently unavailable"
        elif price_el is not None and (price := _price_of(price_el)):
            product.status, product.price, product.reason = Status.AVAILABLE, price, "price shown"
        elif secondary is not None and "₹" in _text(secondary):
            product.status, product.reason = Status.AVAILABLE, "offers from other sellers"
            product.price = parse_price(_text(secondary).split("₹", 1)[1])
            product.seller = "other sellers"
        else:
            product.reason = "no price shown"
        page.products.append(product)
    return page
