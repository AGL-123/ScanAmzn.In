from scanamzn.models import Status
from scanamzn.parser import is_blocked, is_product_page, parse_price, parse_product, parse_search

from .conftest import fixture, product_html


def test_available_product_page():
    p = parse_product(fixture("product_available.html"), "B0TEST1000")
    assert p.status is Status.AVAILABLE
    assert p.price == 1000.0
    assert p.seller == "Amazon"
    assert p.title.startswith("Sony PlayStation Network Wallet Top-Up ₹ 1000")
    assert "add-to-cart" in p.reason


def test_unavailable_product_page_ignores_carousel_prices_and_buttons():
    p = parse_product(fixture("product_unavailable.html"), "B0TEST2000")
    assert p.status is Status.UNAVAILABLE
    # The page has a ₹5,990 controller in a carousel; it must not be read as our price.
    assert p.price is None


def test_other_sellers_counts_as_available():
    p = parse_product(fixture("product_other_sellers.html"), "B0TEST3000")
    assert p.status is Status.AVAILABLE
    assert p.price == 3450.0
    assert p.seller == "other sellers"


def test_captcha_is_blocked():
    html = fixture("captcha.html")
    assert is_blocked(html)
    assert parse_product(html, "B0TEST").status is Status.BLOCKED
    assert parse_search(html).blocked


def test_throttling_status_codes_are_blocked():
    assert parse_product("<html></html>", "B0TEST", status_code=503).status is Status.BLOCKED
    assert parse_product("<html></html>", "B0TEST", status_code=429).status is Status.BLOCKED


def test_real_page_mentioning_robots_is_not_blocked():
    html = product_html("PSN Card ₹1000", True).replace(
        "</body>", "<p>Review: make sure you're not a robot lol</p></body>"
    )
    assert parse_product(html, "B0TEST").status is Status.AVAILABLE


def test_not_found():
    assert parse_product(fixture("not_found.html"), "B0TEST").status is Status.NOT_FOUND
    assert parse_product("<html></html>", "B0TEST", status_code=404).status is Status.NOT_FOUND


def test_server_error():
    assert parse_product("<html></html>", "B0TEST", status_code=500).status is Status.ERROR


def test_back_in_stock_wording_is_not_positive():
    html = (
        '<span id="productTitle">x</span><div id="availability">'
        "We don't know when or if this item will be back in stock.</div>"
    )
    assert parse_product(html, "B0TEST").status is Status.UNAVAILABLE


def test_positive_text_without_button_still_alerts():
    html = '<span id="productTitle">x</span><div id="availability">Only 2 left in stock.</div>'
    assert parse_product(html, "B0TEST").status is Status.AVAILABLE


def test_disabled_buy_button_is_ignored():
    html = '<span id="productTitle">x</span><input id="add-to-cart-button" disabled>'
    assert parse_product(html, "B0TEST").status is Status.UNKNOWN


def test_unrecognised_page_is_unknown():
    p = parse_product('<span id="productTitle">x</span><div>Hello</div>', "B0TEST")
    assert p.status is Status.UNKNOWN


def test_title_falls_back_to_html_title():
    html = "<html><head><title>Amazon.in: PSN Card ₹500 : Video Games</title></head><body></body></html>"
    assert parse_product(html, "B0TEST").title == "PSN Card ₹500"


def test_parse_price():
    assert parse_price("₹1,000.00") == 1000.0
    assert parse_price("₹ 2,499") == 2499.0
    assert parse_price("Rs. 500.50") == 500.5
    assert parse_price("") is None
    assert parse_price("free") is None


def test_search_results():
    page = parse_search(fixture("search_results.html"))
    assert not page.blocked and page.layout_ok
    by_asin = {p.asin: p for p in page.products}
    assert list(by_asin) == ["B0PSN01000", "B0PSN02000", "B0PSNUSD10", "B0CTRL0001", "B0PSN05000", "B0PSPLUS03"]

    first = by_asin["B0PSN01000"]
    assert first.status is Status.AVAILABLE
    assert first.price == 1000.0  # not the struck-through M.R.P. of ₹1,100
    assert first.title == "Sony PlayStation Network Wallet Top-Up ₹1000 (Digital Code - Email Delivery)"
    assert first.source == "search"

    assert by_asin["B0PSN02000"].status is Status.UNAVAILABLE
    other = by_asin["B0PSN05000"]
    assert other.status is Status.AVAILABLE and other.price == 5500.0
    assert not other.title.lower().startswith("sponsored")


def test_search_with_no_results_is_fine_but_unknown_layout_is_flagged():
    assert parse_search('<html><body>No results for "psn xyz".</body></html>').layout_ok
    assert not parse_search("<html><body>Something else entirely</body></html>").layout_ok


def test_in_stock_but_not_deliverable_to_visitor_location():
    # What Amazon shows when it guesses a delivery location outside its area,
    # e.g. someone browsing Amazon.in from Israel.
    html = (
        '<span id="productTitle">PSN ₹1000</span><div id="desktop_buybox">'
        '<span class="a-price priceToPay"><span class="a-offscreen">₹1,000.00</span></span>'
        "<div>This item cannot be shipped to your selected delivery location. "
        "Please choose a different delivery location.</div></div>"
    )
    p = parse_product(html, "B0TEST")
    assert p.status is Status.AVAILABLE
    assert "deliver to your location" in p.reason


def test_out_of_stock_wins_over_delivery_location_message():
    html = (
        '<span id="productTitle">x</span><div id="availability">Currently unavailable.</div>'
        "<div id=\"rightCol\">This item cannot be delivered to your selected location.</div>"
    )
    assert parse_product(html, "B0TEST").status is Status.UNAVAILABLE


def test_aws_waf_challenge_page_is_blocked_whatever_the_status():
    # Saved from Amazon.in's home page on the user's phone (tokens removed).
    page = fixture("aws_waf_challenge.html")
    for status in (202, 200, 405):
        assert is_blocked(page, status)
        assert parse_product(page, "B0X", status).status is Status.BLOCKED
    assert parse_search(page, 200).blocked
    assert not is_product_page(page)
    assert is_product_page(product_html("x", available=False))
