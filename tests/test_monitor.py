import random

from scanamzn.models import Status
from scanamzn.monitor import CycleReport, Monitor, format_inr

from .conftest import fixture, make_settings, product_html

ASIN = "B0PSN01000"
TITLE = "Sony PlayStation Network Wallet Top-Up ₹1000 (Digital Code)"


def make_monitor(fetcher, dispatcher, clock, **settings):
    return Monitor(make_settings(**settings), fetcher, dispatcher, clock=clock, rng=random.Random(1))


def watching(asins=(ASIN,), **monitor):
    return {"amazon": {"asins": list(asins)}, "monitor": monitor}


def test_alerts_once_per_restock(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    assert dispatcher.messages == []
    assert m.items[ASIN].status is Status.UNAVAILABLE

    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    report = m.run_cycle()
    assert report.available == [ASIN]
    assert len(dispatcher.messages) == 1
    msg = dispatcher.messages[0]
    assert "IN STOCK" in msg.subject and "₹1,000" in msg.subject
    assert f"https://www.amazon.in/dp/{ASIN}" in msg.body
    assert f"ASIN.1={ASIN}" in msg.body
    assert "\n" not in msg.summary

    # Still in stock: no repeat.
    clock.advance(60)
    m.run_cycle()
    assert len(dispatcher.messages) == 1

    # Sells out, then restocks after the cooldown: alert again.
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    clock.advance(60)
    m.run_cycle()
    assert len(dispatcher.messages) == 1
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    clock.advance(11 * 60)
    m.run_cycle()
    assert len(dispatcher.messages) == 2


def test_flapping_within_cooldown_is_suppressed(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    for available in (True, False, True):
        fetcher.set_product(ASIN, product_html(TITLE, available=available))
        m.run_cycle()
        clock.advance(60)
    assert len(dispatcher.messages) == 1


def test_already_in_stock_at_startup_alerts(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    m.run_cycle()
    assert len(dispatcher.messages) == 1


def test_sold_out_notification_is_optional(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(notify_when_sold_out=True))
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    assert [msg.subject.split(":")[0] for msg in dispatcher.messages] == ["🎮 IN STOCK", "Gone"]


def test_scalper_price_is_not_alerted_until_it_drops(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,500.00"))
    m.run_cycle()
    assert m.items[ASIN].status is Status.OVERPRICED
    assert dispatcher.messages == []

    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,000.00"))
    m.run_cycle()
    assert len(dispatcher.messages) == 1


def test_markup_check_can_be_disabled_and_max_price_applies(fetcher, dispatcher, clock):
    m = make_monitor(
        fetcher, dispatcher, clock, **watching(), filter={"max_markup_percent": -1, "max_price": 1200}
    )
    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,150.00"))
    m.run_cycle()
    assert len(dispatcher.messages) == 1
    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,300.00"))
    m.run_cycle()
    assert m.items[ASIN].status is Status.OVERPRICED


def test_captcha_stops_cycle_backs_off_and_keeps_state(fetcher, dispatcher, clock):
    other = "B0PSN02000"
    m = make_monitor(fetcher, dispatcher, clock, **watching(asins=(ASIN, other)))
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    fetcher.set_product(other, product_html("PSN ₹2000 wallet", available=False))
    m.run_cycle()
    assert len(dispatcher.messages) == 1

    fetcher.set_product(ASIN, fixture("captcha.html"))
    fetcher.requests.clear()
    report = m.run_cycle()
    assert report.blocked
    assert fetcher.requests == [fetcher.product_url(ASIN)]  # stopped after the captcha
    assert m.items[ASIN].in_stock  # a captcha says nothing about stock

    assert m.next_wait(report) == 60  # 30s * 2
    assert m.next_wait(report) == 120
    assert fetcher.resets == 2
    waits = [m.next_wait(CycleReport()) for _ in range(20)]
    assert all(30 * 0.75 <= w <= 30 * 1.25 for w in waits)
    assert m.block_streak == 0


def test_backoff_is_capped(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(max_backoff_seconds=100))
    for _ in range(10):
        wait = m.next_wait(CycleReport(blocked=True))
    assert wait == 100


def test_network_errors_do_not_change_state(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    m.run_cycle()
    fetcher.fail.add(fetcher.product_url(ASIN))
    report = m.run_cycle()
    assert report.errors == 1
    assert m.items[ASIN].in_stock and m.items[ASIN].status is Status.AVAILABLE


def test_search_discovers_and_confirms(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, amazon={"search_queries": ["playstation gift card"]})
    fetcher.set_search("playstation gift card", fixture("search_results.html"))
    fetcher.set_product("B0PSN01000", fixture("product_available.html"))
    fetcher.set_product("B0PSN05000", product_html("PlayStation Store Gift Card INR 5000", available=False))

    m.run_cycle()
    # Only Indian-rupee PlayStation wallet cards are picked up.
    assert m.discovered == ["B0PSN01000", "B0PSN02000", "B0PSN05000"]
    # The available ones were confirmed on their product pages; only B0PSN01000 really is.
    assert fetcher.product_url("B0PSN01000") in fetcher.requests
    assert fetcher.product_url("B0PSN05000") in fetcher.requests
    assert fetcher.product_url("B0PSN02000") not in fetcher.requests
    assert len(dispatcher.messages) == 1
    assert "Wallet Top-Up ₹ 1000" in dispatcher.messages[0].body
    assert "Seller: Amazon" in dispatcher.messages[0].body
    assert m.items["B0PSN05000"].status is Status.UNAVAILABLE

    # Next cycle the discovered ASINs are watched via their product pages.
    fetcher.requests.clear()
    m.run_cycle()
    assert fetcher.requests[:3] == [fetcher.product_url(a) for a in m.discovered]
    assert len(dispatcher.messages) == 1


def test_search_result_trusted_when_product_page_cannot_be_read(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, amazon={"search_queries": ["playstation gift card"]})
    fetcher.set_search("playstation gift card", fixture("search_results.html"))
    fetcher.set_product("B0PSN01000", fixture("captcha.html"))
    report = m.run_cycle()
    assert report.blocked
    assert len(dispatcher.messages) == 1
    assert "search page" in dispatcher.messages[0].body


def test_discovery_cap(fetcher, dispatcher, clock):
    m = make_monitor(
        fetcher, dispatcher, clock,
        amazon={"search_queries": ["playstation gift card"]}, monitor={"max_discovered": 1},
    )
    fetcher.set_search("playstation gift card", fixture("search_results.html"))
    m.run_cycle()
    assert m.discovered == ["B0PSN01000"]
    # Not watched, so its search status is still tracked.
    assert m.items["B0PSN02000"].status is Status.UNAVAILABLE


def test_search_runs_every_n_cycles(fetcher, dispatcher, clock):
    m = make_monitor(
        fetcher, dispatcher, clock,
        amazon={"asins": [ASIN], "search_queries": ["psn"]}, monitor={"search_every_cycles": 3},
    )
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    for _ in range(6):
        m.run_cycle()
    assert fetcher.requests.count(fetcher.search_url("psn")) == 2


def test_health_alert_and_recovery(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(health_alert_after_minutes=30))
    fetcher.set_product(ASIN, fixture("captcha.html"))
    for _ in range(4):
        m.run_cycle()
        m._check_health()
        clock.advance(10 * 60)
    assert len(dispatcher.messages) == 1
    assert "cannot check" in dispatcher.messages[0].subject
    assert "captcha" in dispatcher.messages[0].body

    m.run_cycle()
    m._check_health()
    assert len(dispatcher.messages) == 1  # only once

    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    assert len(dispatcher.messages) == 2
    assert "again" in dispatcher.messages[1].subject


def test_debug_dump_of_unrecognised_page(fetcher, dispatcher, clock, tmp_path):
    m = make_monitor(fetcher, dispatcher, clock, **watching(debug_dump_dir=str(tmp_path)))
    fetcher.set_product(ASIN, '<span id="productTitle">x</span>')
    m.run_cycle()
    m.run_cycle()
    assert len(list(tmp_path.iterdir())) == 1


def test_format_inr():
    assert format_inr(1000.0) == "₹1,000"
    assert format_inr(999.5) == "₹999.50"
    assert format_inr(None) == "price not shown"


def test_search_result_used_when_watched_product_page_failed(fetcher, dispatcher, clock):
    m = make_monitor(
        fetcher, dispatcher, clock,
        amazon={"asins": ["B0PSN01000"], "search_queries": ["playstation gift card"]},
    )
    fetcher.set_search("playstation gift card", fixture("search_results.html"))
    fetcher.fail.add(fetcher.product_url("B0PSN01000"))
    m.run_cycle()
    # The failed product page is not fetched a second time in the same cycle...
    assert fetcher.requests.count(fetcher.product_url("B0PSN01000")) == 1
    # ...and the in-stock search result still triggers the alert.
    assert any("₹1000" in msg.subject for msg in dispatcher.messages)
