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


def test_scalper_price_is_flagged_not_called_in_stock_until_it_drops(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,500.00"))
    m.run_cycle()
    assert m.items[ASIN].status is Status.OVERPRICED
    assert [x.subject.startswith("🎮 IN STOCK above your price limit") for x in dispatcher.messages] == [True]
    m.run_cycle()  # still overpriced: no repeat
    assert len(dispatcher.messages) == 1

    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,000.00"))
    m.run_cycle()
    assert len(dispatcher.messages) == 2 and dispatcher.messages[1].subject.startswith("🎮 IN STOCK:")


def test_overpriced_restock_after_unavailable_alerts(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=True, price="₹1,150.00"))
    m.run_cycle()
    msg = dispatcher.messages[-1]
    assert "above your price limit" in msg.subject and msg.kind == "alert" and "₹1,150" in msg.body
    assert f"/dp/{ASIN}" in msg.body and "max_markup_percent" in msg.body


def test_reseller_listing_always_overpriced_stays_quiet(fetcher, dispatcher, clock):
    # A listing found by search (not watched) that is in stock at a markup from the start.
    m = make_monitor(fetcher, dispatcher, clock, amazon={"search_queries": ["psn"]})
    from scanamzn.models import Product

    reseller = Product("B0RESELL01", Status.AVAILABLE, title="INR 2000 PlayStation Store Gift Card", price=2699.0)
    m._record(m._apply_price_limits(reseller), CycleReport())
    assert m.items["B0RESELL01"].status is Status.OVERPRICED and dispatcher.messages == []


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

    assert 54 <= m.next_wait(report) <= 66  # retry after about a minute, then two, ...
    assert 108 <= m.next_wait(report) <= 132
    assert fetcher.resets == 2
    waits = [m.next_wait(CycleReport()) for _ in range(20)]
    assert all(30 * 0.75 <= w <= 30 * 1.25 for w in waits)
    assert m.block_streak == 0


def test_backoff_is_capped(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(max_backoff_seconds=100))
    for _ in range(10):
        wait = m.next_wait(CycleReport(blocked=True))
    assert 90 <= wait <= 110


def test_waits_after_bot_checks_stay_short_by_default(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    waits = [m.next_wait(CycleReport(blocked=True)) for _ in range(8)]
    assert max(waits) <= 180 * 1.1 and m.s.health_alert_after_minutes == 10


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
    m.run_cycle()
    assert "B0PSN01000" in dispatcher.messages[0].subject or "1000" in dispatcher.messages[0].subject
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


def rotating_monitor(fetcher, dispatcher, clock, rotating=("B0ROT00001", "B0ROT00002", "B0ROT00003"), per_round=1):
    m = make_monitor(fetcher, dispatcher, clock, **watching())
    m.s.rotating_asins = list(rotating)
    m.s.other_listings_per_round = per_round
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
    for asin in ("B0ROT00001", "B0ROT00002"):  # two bot checks in a row: the session is blocked
        fetcher.set_product(asin, fixture("aws_waf_challenge.html"), status=202)
    report = m.run_cycle()  # restock, then bot checks in the middle of the sweep
    assert report.blocked and m.sweep == {"B0ROT00001", "B0ROT00002", "B0ROT00003"}  # none read yet
    fetcher.requests.clear()
    for asin in ("B0ROT00001", "B0ROT00002"):
        fetcher.set_product(asin, product_html("PlayStation card", available=False))
    m.run_cycle()  # the pages that showed a bot check come last now
    assert [u.rsplit("/", 1)[1] for u in fetcher.requests] == [ASIN, "B0ROT00003", "B0ROT00001", "B0ROT00002"]
    assert m.sweep == set() and not m.suspect & {"B0ROT00001", "B0ROT00002"}  # they loaded: back to normal


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


def test_two_other_values_per_round_by_default(fetcher, dispatcher, clock):
    from scanamzn.config import from_dict

    assert from_dict({}).other_listings_per_round == 2
    m = rotating_monitor(fetcher, dispatcher, clock, per_round=2)
    for _ in range(3):
        m.run_cycle()
    pages = [u.rsplit("/", 1)[1] for u in fetcher.requests]
    assert pages == [ASIN, "B0ROT00001", "B0ROT00002", ASIN, "B0ROT00003", "B0ROT00001",
                     ASIN, "B0ROT00002", "B0ROT00003"]



# --- one listing whose page always looks like a bot check must not stop the others -----


def test_a_listing_that_alone_shows_a_bot_check_is_skipped_and_checked_last(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(asins=("B0BROKEN01", ASIN, "B0PSN02000")))
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    fetcher.set_product("B0PSN02000", product_html("Rs.2000 PlayStation card", available=False))
    fetcher.set_product("B0BROKEN01", fixture("aws_waf_challenge.html"), status=202)

    report = m.run_cycle()  # the first page of the round is blocked: maybe a real bot check, back off
    assert report.blocked and fetcher.requests == [fetcher.product_url("B0BROKEN01")]

    fetcher.requests.clear()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    for _ in range(3):  # from now on it comes last and is skipped; the others keep being checked
        report = m.run_cycle()
        assert not report.blocked and report.errors == 1
    pages = [u.rsplit("/", 1)[1] for u in fetcher.requests]
    assert pages == [ASIN, "B0PSN02000", "B0BROKEN01"] * 3
    assert [x.subject.startswith("🎮 IN STOCK") for x in dispatcher.messages] == [True]


def test_a_bot_check_on_one_page_amid_normal_pages_is_skipped_once(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(asins=(ASIN, "B0BROKEN01", "B0PSN02000")))
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    fetcher.set_product("B0BROKEN01", fixture("aws_waf_challenge.html"), status=202)
    fetcher.set_product("B0PSN02000", product_html("Rs.2000 PlayStation card", available=True, price="₹2,000.00"))
    report = m.run_cycle()
    assert not report.blocked and "B0BROKEN01" in m.suspect
    assert any("IN STOCK" in x.subject for x in dispatcher.messages)  # the listing after it was still checked
    fetcher.requests.clear()
    m.run_cycle()
    assert fetcher.requests[-1] == fetcher.product_url("B0BROKEN01")  # checked last from now on


def test_two_bot_checks_in_one_round_stop_the_round(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching(asins=(ASIN, "B0BLOCK001", "B0BLOCK002", "B0PSN02000")))
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    for asin in ("B0BLOCK001", "B0BLOCK002"):
        fetcher.set_product(asin, fixture("aws_waf_challenge.html"), status=202)
    report = m.run_cycle()
    assert report.blocked and fetcher.product_url("B0PSN02000") not in fetcher.requests


def test_a_rotating_listing_that_alone_shows_a_bot_check_does_not_stall_the_sweep(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    fetcher.set_product("B0ROT00001", fixture("aws_waf_challenge.html"), status=202)
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))  # restock: sweep all others
    m.run_cycle()
    assert m.sweep == set()
    fetcher.requests.clear()
    m.run_cycle()
    m.run_cycle()
    assert fetcher.product_url("B0ROT00002") in fetcher.requests  # rotation goes on



# --- review findings on the bot-check guard ---------------------------------------------


def test_a_skipped_page_met_again_in_search_does_not_stop_the_round(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock,
                     amazon={"asins": ["B0PSN02000", "B0PSN01000"], "search_queries": ["playstation gift card"]})
    fetcher.set_product("B0PSN02000", product_html("Rs.2000 PlayStation card", available=False))
    fetcher.set_product("B0PSN01000", fixture("aws_waf_challenge.html"), status=202)  # skipped
    fetcher.set_search("playstation gift card", fixture("search_results.html"))  # shows B0PSN01000 with a price
    report = m.run_cycle()
    assert not report.blocked and report.errors == 1
    assert any("search page" in x.body for x in dispatcher.messages)  # trusted the search result


def test_a_card_skipped_in_the_sweep_is_retried_next_round(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    fetcher.set_product("B0ROT00002", fixture("aws_waf_challenge.html"), status=202)  # a one-off
    assert not m.run_cycle().blocked
    assert m.sweep == {"B0ROT00002"}
    fetcher.set_product("B0ROT00002", product_html("Rs.2000 PlayStation card", available=True, price="₹2,000.00"))
    fetcher.requests.clear()
    m.run_cycle()
    assert fetcher.product_url("B0ROT00002") in fetcher.requests and m.sweep == set()
    assert any("Rs.2000" in x.subject for x in dispatcher.messages)


def test_a_card_that_keeps_showing_a_bot_check_leaves_the_sweep(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    fetcher.set_product("B0ROT00002", fixture("aws_waf_challenge.html"), status=202)
    m.run_cycle()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))
    for _ in range(3):
        m.run_cycle()
    assert m.sweep == set()  # no stall: the normal turns go on


def test_blocked_searches_rest_while_product_pages_keep_being_checked(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock,
                     amazon={"asins": [ASIN], "search_queries": ["psn", "playstation gift card"]})
    m.s.rotating_asins = ["B0ROT00001", "B0ROT00002"]
    m.s.other_listings_per_round = 1
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    for asin in m.s.rotating_asins:
        fetcher.set_product(asin, product_html("PlayStation card", available=False))
    for q in ("psn", "playstation gift card"):
        fetcher.set_search(q, fixture("aws_waf_challenge.html"), status=202)
    report = m.run_cycle()
    assert not report.blocked and set(m.search_rest) == {"psn", "playstation gift card"}
    assert m.next_wait(report) <= m.s.interval_seconds * 1.5  # no back-off for the whole bot
    fetcher.requests.clear()
    clock.advance(45)
    m.run_cycle()  # searches rest; product pages go on
    assert not any("/s?" in u for u in fetcher.requests) and fetcher.product_url(ASIN) in fetcher.requests
    fetcher.set_search("psn", '<html><body>No results for "psn".</body></html>')
    clock.advance(10 * 60)
    m.run_cycle()
    assert "psn" not in m.search_rest  # loaded again


def test_blocked_search_with_no_product_page_loaded_backs_off(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, amazon={"search_queries": ["psn"]})
    fetcher.set_search("psn", fixture("aws_waf_challenge.html"), status=202)
    assert m.run_cycle().blocked


# --- data saver and the self-test's pause ---------------------------------------------


def test_data_saver_checks_only_the_main_cards_half_as_often(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.s.search_queries = ["psn"]
    fetcher.set_search("psn", '<html><body>No results for "psn".</body></html>')
    m.saver_flag.parent.mkdir(parents=True, exist_ok=True)
    m.saver_flag.touch()
    report = m.run_cycle()
    assert fetcher.requests == [fetcher.product_url(ASIN)]  # no values in turn, no searches
    assert m.next_wait(report) >= m.s.interval_seconds * 2 * (1 - m.s.jitter)
    assert m.snapshot()["data_saver"] is True

    fetcher.set_product(ASIN, product_html(TITLE, available=True))  # a restock still sweeps the others
    fetcher.requests.clear()
    m.run_cycle()
    assert {fetcher.product_url(a) for a in ("B0ROT00001", "B0ROT00002", "B0ROT00003")} <= set(fetcher.requests)

    m.saver_flag.unlink()
    fetcher.requests.clear()
    m.run_cycle()
    assert fetcher.search_url("psn") in fetcher.requests and m.saver is False


def _paused_monitor(fetcher, dispatcher, clock):
    from datetime import datetime, timezone

    m = make_monitor(fetcher, dispatcher, clock, **watching())
    m.wall = lambda: datetime.fromtimestamp(clock(), timezone.utc)
    fetcher.set_product(ASIN, product_html(TITLE, available=False))
    m.pause_flag.parent.mkdir(parents=True, exist_ok=True)
    return m


def _hold_pause(m, until):
    """What a running self-test does: write the end time and keep a lock."""
    import fcntl
    import os

    fd = os.open(m.pause_flag, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.write(fd, f"{until:.0f}\n".encode())
    return fd


def test_monitor_waits_while_a_self_test_runs(fetcher, dispatcher, clock):
    import os

    m = _paused_monitor(fetcher, dispatcher, clock)
    m._stop.wait = lambda t: clock.advance(t)
    fd = _hold_pause(m, clock() + 120)
    start = clock()
    m.run_forever(max_runtime_seconds=1)
    assert clock() - start >= 120 and fetcher.requests  # waited until the end time, then checked
    os.close(fd)


def test_monitor_resumes_at_once_when_the_self_test_ends_or_dies(fetcher, dispatcher, clock):
    import os

    m = _paused_monitor(fetcher, dispatcher, clock)
    fd = _hold_pause(m, clock() + 600)

    def wait(t):
        clock.advance(t)
        os.close(fd)  # the self-test is killed: its lock goes, its file stays

    m._stop.wait = wait
    start = clock()
    m.run_forever(max_runtime_seconds=1)
    assert clock() - start <= 10 and fetcher.requests and not m.pause_flag.exists()


def test_a_pause_file_nobody_holds_is_ignored(fetcher, dispatcher, clock):
    m = _paused_monitor(fetcher, dispatcher, clock)
    m._stop.wait = lambda t: clock.advance(t)
    for content in (f"{clock() + 600:.0f}\n", "garbage", "inf"):
        m.pause_flag.write_text(content)
        fetcher.requests.clear()
        start = clock()
        m.run_forever(max_runtime_seconds=1)
        assert clock() - start < 5 and fetcher.requests


def test_a_pause_never_lasts_longer_than_the_limit(fetcher, dispatcher, clock):
    import os

    from scanamzn.selftest import PAUSE_SECONDS

    m = _paused_monitor(fetcher, dispatcher, clock)
    m._stop.wait = lambda t: clock.advance(t)
    fd = _hold_pause(m, clock() + 365 * 86400)  # a wrong clock, say
    start = clock()
    m.run_forever(max_runtime_seconds=1)
    assert PAUSE_SECONDS <= clock() - start <= PAUSE_SECONDS + 10 and fetcher.requests
    os.close(fd)


def test_data_saver_skips_listings_found_by_searches_unless_in_stock(fetcher, dispatcher, clock):
    m = rotating_monitor(fetcher, dispatcher, clock)
    m.discovered = ["B0FOUND001", "B0ROT00003"]  # found by earlier searches (one is also a value in turn)
    fetcher.set_product("B0FOUND001", product_html("Rs.2000 card by a reseller", available=False))
    m.saver_flag.parent.mkdir(parents=True, exist_ok=True)
    m.saver_flag.touch()
    m.run_cycle()
    assert fetcher.requests == [fetcher.product_url(ASIN)]
    fetcher.requests.clear()
    fetcher.set_product(ASIN, product_html(TITLE, available=True))  # restock: sweep the other values
    m.run_cycle()
    assert fetcher.product_url("B0ROT00003") in fetcher.requests


# --- focused mode (default) and extensive mode ------------------------------------------


def focused_monitor(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock,
                     amazon={"asins": [ASIN], "focus_asins": ["B0FOCUS001", "B0FOCUS002"],
                             "search_queries": ["psn"]},
                     monitor={"focus_interval_seconds": 30})
    m.s.rotating_asins = ["B0ROT00001", "B0ROT00002"]
    for asin in ("B0FOCUS001", "B0FOCUS002", ASIN, *m.s.rotating_asins):
        fetcher.set_product(asin, product_html(f"Rs.{asin[-1]}000 PlayStation card", available=False))
    fetcher.set_search("psn", '<html><body>No results for "psn".</body></html>')
    return m


def test_focused_mode_checks_only_the_focused_cards_more_often(fetcher, dispatcher, clock):
    m = focused_monitor(fetcher, dispatcher, clock)
    assert not m.extensive and m.s.focus_asins == ["B0FOCUS001", "B0FOCUS002", ASIN]  # + the user's own
    report = m.run_cycle()
    pages = [u.rsplit("/", 1)[1] for u in fetcher.requests]
    assert pages == ["B0FOCUS001", "B0FOCUS002", ASIN]  # no values in turn, no searches
    assert 30 * 0.75 <= m.next_wait(report) <= 30 * 1.25
    assert m.snapshot()["mode"] == "focused" and "focused mode" in m.mode_line()

    fetcher.set_product("B0FOCUS002", product_html("Rs.2000 PlayStation card", available=True, price="₹2,000.00"))
    fetcher.requests.clear()
    m.run_cycle()  # a restock: alert, but no sweep of the other values in focused mode
    assert any("IN STOCK" in x.subject for x in dispatcher.messages)
    assert not any("B0ROT" in u for u in fetcher.requests) and m.sweep == set()


def test_extensive_mode_is_switched_on_by_the_widget_file(fetcher, dispatcher, clock):
    m = focused_monitor(fetcher, dispatcher, clock)
    m.extensive_flag.parent.mkdir(parents=True, exist_ok=True)
    m.extensive_flag.touch()
    report = m.run_cycle()
    assert m.extensive and m.snapshot()["mode"] == "extensive"
    assert fetcher.product_url(ASIN) in fetcher.requests and fetcher.search_url("psn") in fetcher.requests
    assert any("B0ROT" in u for u in fetcher.requests)
    assert m.next_wait(report) >= m.s.interval_seconds * 0.75
    m.extensive_flag.unlink()
    fetcher.requests.clear()
    m.run_cycle()
    assert not m.extensive and fetcher.search_url("psn") not in fetcher.requests


def test_no_focus_list_means_always_extensive(fetcher, dispatcher, clock):
    m = make_monitor(fetcher, dispatcher, clock, **watching())  # test settings: no known listings
    assert m.s.focus_asins == [] and m.extensive
