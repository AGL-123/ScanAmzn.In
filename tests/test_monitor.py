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


class FakeWall:
    def __init__(self, clock, start):
        self.clock, self.start, self.t0 = clock, start, clock.t

    def __call__(self):
        from datetime import timedelta

        return self.start + timedelta(seconds=self.clock.t - self.t0)


def _wall(clock, hour=8, minute=30):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return FakeWall(clock, datetime(2026, 10, 12, hour, minute, tzinfo=ZoneInfo("Asia/Jerusalem")))


def test_run_forever_stops_after_max_runtime(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m._stop.wait = lambda t: clock.advance(t)  # simulated time instead of sleeping
    m.run_forever(max_runtime_seconds=10 * 60)
    # ~30s per cycle (+/- jitter) over 10 minutes, and never beyond the deadline.
    assert 15 <= m.cycle <= 30
    assert clock.t - 1000 <= 10 * 60 + 1


def test_run_forever_survives_unexpected_errors(fetcher, dispatcher, clock, monkeypatch):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    m._stop.wait = lambda t: clock.advance(t)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("parser bug")

    monkeypatch.setattr(m, "run_cycle", boom)
    m.run_forever(max_runtime_seconds=120)
    assert len(calls) >= 3
    assert m.last_problem == "internal error (see log)"


def test_daily_status_message_once_a_day(fetcher, dispatcher, clock):
    s = make_settings(**watching(daily_status_hour=9, interval_seconds=600, timezone="Asia/Jerusalem"))
    m = Monitor(s, fetcher, dispatcher, clock=clock, rng=random.Random(1), wall=_wall(clock, 8, 30))
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m._stop.wait = lambda t: clock.advance(t)
    m.run_forever(max_runtime_seconds=25 * 3600)  # 08:30 today -> 09:30 tomorrow
    statuses = [msg for msg in dispatcher.messages if "is running" in msg.subject]
    assert len(statuses) == 2  # 09:00 today and 09:00 tomorrow
    assert "nothing in stock" in statuses[0].subject
    assert "Watching 1 listing(s)" in statuses[0].body
    assert "unavailable" in statuses[0].body
    assert "\n" not in statuses[0].summary


def test_daily_status_disabled_by_default(fetcher, dispatcher, clock):
    m = Monitor(make_settings(**watching()), fetcher, dispatcher, clock=clock, wall=_wall(clock))
    assert m.next_status_at is None


def test_next_daily_status_time(fetcher, dispatcher, clock):
    s = make_settings(**watching(daily_status_hour=9, timezone="Asia/Jerusalem"))
    early = Monitor(s, fetcher, dispatcher, clock=clock, wall=_wall(clock, 8, 30))
    late = Monitor(s, fetcher, dispatcher, clock=clock, wall=_wall(clock, 9, 0))
    assert (early.next_status_at.day, early.next_status_at.hour) == (12, 9)
    assert (late.next_status_at.day, late.next_status_at.hour) == (13, 9)


def test_status_file(fetcher, dispatcher, clock, tmp_path):
    import json

    path = tmp_path / "state" / "status.json"
    m = make_monitor(fetcher, dispatcher, clock, **watching(status_file=str(path)))
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    m.run_cycle()
    m.write_status_file(next_check_in=42)
    data = json.loads(path.read_text())
    assert data["cycle"] == 1 and data["next_check_in_seconds"] == 42
    item = data["items"][0]
    assert (item["asin"], item["status"], item["in_stock"], item["price"]) == (ASIN, "available", True, 1000.0)
    assert not list(path.parent.glob("*.tmp"))


def test_heartbeat_ping_is_rate_limited(fetcher, dispatcher, clock):
    pings = []
    s = make_settings(**watching(heartbeat_url="https://hc-ping.com/abc"))
    m = Monitor(s, fetcher, dispatcher, clock=clock, rng=random.Random(1), pinger=pings.append)
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m._stop.wait = lambda t: clock.advance(t)
    m.run_forever(max_runtime_seconds=20 * 60)
    import time

    time.sleep(0.2)  # pings run in background threads
    # ~30 s cycles for 20 minutes, but at most one ping per 4 minutes.
    assert 4 <= len(pings) <= 6
    assert set(pings) == {"https://hc-ping.com/abc"}


def test_no_heartbeat_without_url(fetcher, dispatcher, clock):
    pings = []
    m = Monitor(make_settings(**watching()), fetcher, dispatcher, clock=clock, pinger=pings.append)
    m._heartbeat()
    assert pings == []


def test_status_file_written_before_first_cycle(fetcher, dispatcher, clock, tmp_path):
    import json

    path = tmp_path / "status.json"
    m = make_monitor(fetcher, dispatcher, clock, **watching(status_file=str(path)))
    seen = {}

    def first_cycle():
        seen.update(json.loads(path.read_text()))  # what `status` shows during round 1
        m.stop()
        from scanamzn.monitor import CycleReport

        return CycleReport()

    m.run_cycle = first_cycle
    m.run_forever()
    assert seen["cycle"] == 0 and seen["pid"] > 0


def test_not_found_asin_is_shown_and_reported(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(asins=("B0TYPO0000",)))
    m.run_cycle()  # FakeFetcher answers unknown URLs with Amazon's 404 page
    assert m.items["B0TYPO0000"].status is Status.NOT_FOUND
    assert "wrong ASIN" in m.last_problem
    assert m.snapshot()["items"][0]["status"] == "not_found"
    from datetime import datetime

    assert "page not found (removed?)" in m.daily_status_message(datetime.now()).body


# --- a watched page that changes to a layout the bot can't read -----------------------

REDESIGNED = f'<html><body><span id="productTitle">{TITLE}</span><div>new buy box</div></body></html>'


def test_unavailable_page_that_becomes_unreadable_alerts_check_now(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    fetcher.set_product(ASIN, REDESIGNED)
    clock.advance(60)
    m.run_cycle()
    assert len(dispatcher.messages) == 1
    msg = dispatcher.messages[0]
    assert "CHECK NOW" in msg.subject and msg.kind == "alert"
    assert f"/dp/{ASIN}" in msg.body and "ps5 export" in msg.body
    from pathlib import Path

    saved = [f.name for f in Path(m.s.debug_dump_dir).glob("*.html")]
    assert len(saved) == 1 and saved[0].endswith(f"_product_{ASIN}.html")  # for `ps5 export`
    clock.advance(60)
    m.run_cycle()  # still unreadable: no repeat
    assert len(dispatcher.messages) == 1

    # A real restock right after is still announced (separate timer).
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    clock.advance(60)
    m.run_cycle()
    assert len(dispatcher.messages) == 2 and "IN STOCK" in dispatcher.messages[1].subject


def test_changed_page_alert_is_limited_to_one_per_hour(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    for minutes, html in ((0, False), (1, REDESIGNED), (2, False), (3, REDESIGNED), (63, False), (64, REDESIGNED)):
        clock.t = 1000 + minutes * 60
        fetcher.set_product(ASIN, html if isinstance(html, str) else product_html(TITLE, available=False))
        m.run_cycle()
    assert [x.subject.startswith("🎮 CHECK NOW") for x in dispatcher.messages] == [True, True]


def test_no_changed_page_alert_without_a_prior_unavailable_reading(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, REDESIGNED)  # unreadable from the start: nothing changed
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    fetcher.set_product(ASIN, "<html><body>Sorry, something went wrong</body></html>")  # not a product page
    m.run_cycle()
    assert dispatcher.messages == []


# --- other card values, checked in turn ---------------------------------------------------


def rotating_monitor(fetcher, dispatcher, clock, rotating=("B0ROT00001", "B0ROT00002", "B0ROT00003")):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    m.s.rotating_asins = list(rotating)
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    for asin in rotating:
        fetcher.set_product(asin, product_html(f"Rs.{asin[-1]}000 PlayStation card", available=False))
    return m


def test_other_values_are_checked_one_per_round(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    for _ in range(4):
        m.run_cycle()
    pages = [u.rsplit("/", 1)[1] for u in fetcher.requests]
    assert pages == [ASIN, "B0ROT00001", ASIN, "B0ROT00002", ASIN, "B0ROT00003", ASIN, "B0ROT00001"]


def test_a_restock_checks_all_other_values_at_once(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.run_cycle()
    fetcher.requests.clear()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    fetcher.set_product("B0ROT00003", product_html("Rs.3000 PlayStation card", available=True, price="₹3,000.00"))
    report = m.run_cycle()
    assert [u.rsplit("/", 1)[1] for u in fetcher.requests] == [ASIN, "B0ROT00001", "B0ROT00002", "B0ROT00003"]
    assert report.restocked == [ASIN, "B0ROT00003"] and len(dispatcher.messages) == 2

    fetcher.requests.clear()
    m.run_cycle()  # nothing new: back to one per round, plus the card that is in stock
    assert [u.rsplit("/", 1)[1] for u in fetcher.requests] == [ASIN, "B0ROT00003", "B0ROT00002"]


def test_known_listings_include_other_values_in_turn():
    from scanamzn.config import KNOWN_LISTINGS, MORE_LISTINGS, from_dict

    s = from_dict({"amazon": {"asins": [next(iter(MORE_LISTINGS))]}})
    assert s.asins[1:] == list(KNOWN_LISTINGS)
    assert s.rotating_asins == list(MORE_LISTINGS)[1:]  # a listing given by hand is checked every round
    assert from_dict({"amazon": {"watch_known_listings": False}}).rotating_asins == []
    assert not set(KNOWN_LISTINGS) & set(MORE_LISTINGS)


def test_a_card_checked_in_turn_is_checked_every_round_while_in_stock(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.run_cycle()  # B0ROT00001's turn: unavailable
    fetcher.set_product("B0ROT00002", product_html("Rs.2000 PlayStation card", available=True))
    m.run_cycle()  # B0ROT00002's turn: back in stock - the sweep checks the others too
    fetcher.requests.clear()
    m.run_cycle()
    m.run_cycle()
    pages = [u.rsplit("/", 1)[1] for u in fetcher.requests]
    assert pages == [ASIN, "B0ROT00002", "B0ROT00003", ASIN, "B0ROT00002", "B0ROT00001"]


def test_a_card_found_in_its_turn_starts_the_sweep_in_the_same_round(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.run_cycle()  # B0ROT00001's turn
    fetcher.requests.clear()
    fetcher.set_product("B0ROT00002", product_html("Rs.2000 PlayStation card", available=True))
    fetcher.set_product("B0ROT00003", product_html("Rs.3000 PlayStation card", available=True, price="₹3,000.00"))
    m.run_cycle()
    assert [u.rsplit("/", 1)[1] for u in fetcher.requests] == [ASIN, "B0ROT00002", "B0ROT00001", "B0ROT00003"]
    assert len(dispatcher.messages) == 2 and m.sweep == set()


def test_a_sweep_cut_short_by_a_bot_check_continues_next_round(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    fetcher.set_product("B0ROT00002", fixture("aws_waf_challenge.html"), status=202)
    report = m.run_cycle()  # restock, then a bot check in the middle of the sweep
    assert report.blocked and m.sweep == {"B0ROT00002", "B0ROT00003"}
    fetcher.requests.clear()
    fetcher.set_product("B0ROT00002", product_html("Rs.2000 PlayStation card", available=False))
    m.run_cycle()
    assert [u.rsplit("/", 1)[1] for u in fetcher.requests] == [ASIN, "B0ROT00002", "B0ROT00003"]
    assert m.sweep == set()


def test_changed_page_alert_survives_a_404_in_between(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    fetcher.set_product(ASIN, fixture("not_found.html"), status=404)  # briefly taken down
    m.run_cycle()
    fetcher.set_product(ASIN, REDESIGNED)
    m.run_cycle()
    assert [x.subject.startswith("🎮 CHECK NOW") for x in dispatcher.messages] == [True]


def test_missing_listing_is_reported_once(fetcher, dispatcher, clock, caplog):
    m = make_monitor(fetcher, dispatcher, clock, **watching())  # ASIN has no page: 404
    for _ in range(3):
        m.run_cycle()
    assert sum("product page not found" in r.message for r in caplog.records) == 1
