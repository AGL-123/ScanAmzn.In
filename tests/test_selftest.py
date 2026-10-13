import random

from scanamzn import cli, selftest
from scanamzn.monitor import Monitor
from scanamzn.selftest import in_stock_control

from .conftest import fixture, make_settings, product_html

GLOW = '<span id="glow-ingress-line2">Israel</span>'
NO_PAUSE = lambda: False  # noqa: E731


def control_fetcher(fetcher, first="available", second="available"):
    """Control searches return the search fixture; its first two priced results are
    B0PSN01000 and B0PSNUSD10."""
    for q in selftest.CONTROL_QUERIES:
        fetcher.set_search(q, fixture("search_results.html"))
    fetcher.set_product("B0PSN01000", product_html("USB-C cable", available=first == "available"))
    fetcher.set_product("B0PSNUSD10", product_html("Notebook", available=second == "available"))
    return fetcher


def test_control_passes_on_in_stock_page(fetcher):
    result = in_stock_control(control_fetcher(fetcher), selftest.CONTROL_QUERIES, NO_PAUSE)
    assert result.ok is True and "IN STOCK" in result.detail and "₹1,000" in result.detail


def test_control_tries_a_second_product(fetcher):
    result = in_stock_control(control_fetcher(fetcher, first="unavailable"), selftest.CONTROL_QUERIES, NO_PAUSE)
    assert result.ok is True and "Notebook" in result.detail


def test_control_fails_when_priced_products_read_as_unavailable(fetcher):
    dumped = []
    fetcher = control_fetcher(fetcher, first="unavailable", second="unavailable")
    result = in_stock_control(fetcher, selftest.CONTROL_QUERIES, NO_PAUSE, lambda k, h: dumped.append(k))
    assert result.ok is False and "read as 'unavailable'" in result.detail
    assert dumped == ["control_B0PSN01000", "control_B0PSNUSD10"]


def test_control_fails_when_search_is_not_understood(fetcher):
    for q in selftest.CONTROL_QUERIES:
        fetcher.set_search(q, "<html><body>new layout</body></html>")
    result = in_stock_control(fetcher, selftest.CONTROL_QUERIES, NO_PAUSE)
    assert result.ok is False and "no product with a price" in result.detail


def test_control_is_inconclusive_on_captcha_or_network(fetcher):
    for q in selftest.CONTROL_QUERIES:
        fetcher.set_search(q, fixture("captcha.html"))
    assert in_stock_control(fetcher, selftest.CONTROL_QUERIES, NO_PAUSE).ok is None
    fetcher.fail.add(fetcher.search_url(selftest.CONTROL_QUERIES[0]))
    assert in_stock_control(fetcher, selftest.CONTROL_QUERIES, NO_PAUSE).ok is None


def full_site(fetcher):
    # Amazon's home page is a JavaScript challenge now; the test must not need it.
    fetcher.pages["https://www.amazon.in/"] = (202, fixture("aws_waf_challenge.html"))
    fetcher.set_search("playstation gift card", fixture("search_results.html").replace("</body>", GLOW + "</body>"))
    fetcher.set_product("B0PSN02000", fixture("product_unavailable.html"))
    fetcher.set_product("B0PSN05000", fixture("product_other_sellers.html"))
    return control_fetcher(fetcher, first="available")


def test_full_selftest_passes(fetcher):
    s = make_settings(amazon={"search_queries": ["playstation gift card"]})
    test = selftest.run(s, full_site(fetcher), NO_PAUSE, lambda k, h: None, lambda m: None)
    assert [c.ok for c in test.checks] == [True, True, True, True]
    assert "Israel" in test.checks[0].detail
    assert "3 PlayStation gift card listing(s)" in test.checks[1].detail
    assert test.passed and "would see a restock" in test.summary()


def test_full_selftest_reports_unreadable_gift_card_page(fetcher):
    s = make_settings(amazon={"search_queries": ["playstation gift card"]})
    fetcher = full_site(fetcher)
    fetcher.set_product("B0PSN02000", '<span id="productTitle">x</span><div>redesigned page</div>')
    dumped = []
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: dumped.append(k), lambda m: None)
    assert test.checks[2].ok is False and not test.passed
    assert "product_B0PSN02000" in dumped
    assert test.verdict == "fail" and "read wrongly" in test.summary()


def test_full_selftest_stops_on_captcha(fetcher):
    fetcher.set_search("psn", fixture("captcha.html"))
    s = make_settings(amazon={"search_queries": ["psn"]})
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    # A bot check is not a reading failure: the verdict is "not conclusive".
    assert len(test.checks) == 1 and test.checks[0].ok is None
    assert test.verdict == "inconclusive" and "not conclusive" in test.summary()


def test_monitor_runs_control_and_warns_after_two_failures(fetcher, dispatcher, clock):
    s = make_settings(amazon={"asins": ["B0PSN02000"]}, monitor={"selftest_every_hours": 6})
    fetcher.set_product("B0PSN02000", fixture("product_unavailable.html"))
    control_fetcher(fetcher)
    m = Monitor(s, fetcher, dispatcher, clock=clock, rng=random.Random(1))
    m._stop.wait = lambda t: clock.advance(t)
    m.run_forever(max_runtime_seconds=60)
    assert m.control["ok"] is True and "passed" in m.control_line()
    assert dispatcher.messages == []

    # Amazon "changes its pages": priced products now read as unavailable.
    control_fetcher(fetcher, first="unavailable", second="unavailable")
    m.next_control_at = clock()
    m.run_forever(max_runtime_seconds=60)
    assert m.control_failures == 1 and dispatcher.messages == []  # first failure: re-check soon
    m.run_forever(max_runtime_seconds=45 * 60)
    warnings = [x for x in dispatcher.messages if "may miss restocks" in x.subject]
    assert len(warnings) == 1 and warnings[0].kind == "info"
    assert "FAILED" in m.daily_status_message(m.wall()).body

    control_fetcher(fetcher)  # fixed again
    m.next_control_at = clock()
    m.run_forever(max_runtime_seconds=60)
    assert any("passes again" in x.subject for x in dispatcher.messages)


def test_selftest_command(cli_env, monkeypatch, tmp_path, capsys):
    from .conftest import FakeFetcher

    monkeypatch.setenv("SCANAMZN_SEARCH_QUERIES", "playstation gift card")
    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(tmp_path / "state" / "status.json"))
    monkeypatch.setenv("SCANAMZN_ASINS", "B0PSN02000")
    fake = full_site(FakeFetcher())
    monkeypatch.setattr(cli, "_fetcher", lambda s: fake)
    sent = []

    class Capture:
        name, rationed = "email", False

        def send(self, message):
            sent.append(message)

    monkeypatch.setattr(cli, "build_notifiers", lambda s: [Capture()])
    monkeypatch.setattr(cli.time, "sleep", lambda t: None)
    assert cli.main(["selftest", "--send"]) == 0
    out = capsys.readouterr().out
    assert "In-stock detection" in out and "would see a restock" in out
    assert (tmp_path / "state" / "selftest.txt").read_text().startswith("PS5 gift card monitor - self-test")
    assert sent and sent[0].subject.startswith("✅") and sent[0].kind == "info"


import pytest  # noqa: E402


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCANAMZN_SMTP_USER", "someone@gmail.com")
    monkeypatch.setenv("SCANAMZN_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SCANAMZN_TIMEZONE", "Asia/Jerusalem")


# --- review findings -------------------------------------------------------------------


def _settings(**amazon):
    return make_settings(amazon={"search_queries": ["playstation gift card"], **amazon})


def test_blocked_control_makes_the_verdict_inconclusive(fetcher):
    fetcher = full_site(fetcher)
    for q in selftest.CONTROL_QUERIES:
        fetcher.set_search(q, fixture("captcha.html"))
    test = selftest.run(_settings(), fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    assert test.checks[-1].ok is None and test.verdict == "inconclusive" and not test.passed


def test_captcha_gift_card_pages_are_not_counted_as_understood(fetcher):
    fetcher = full_site(fetcher)
    fetcher.set_product("B0PSN02000", fixture("captcha.html"))
    test = selftest.run(_settings(), fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    pages = test.checks[2]
    assert pages.ok is None and "could not be loaded" in pages.detail
    assert any("bot check" in line for line in pages.lines)


def test_search_and_page_disagreeing_is_flagged(fetcher):
    fetcher = full_site(fetcher)
    fetcher.set_product("B0PSN01000", fixture("product_unavailable.html"))  # search shows ₹1,000
    dumped = []
    test = selftest.run(_settings(), fetcher, NO_PAUSE, lambda k, h: dumped.append(k), lambda m: None)
    pages = test.checks[2]
    assert pages.ok is None and "disagree with the search" in pages.detail
    assert "product_B0PSN01000" in dumped


def test_partly_misunderstood_searches_are_flagged(fetcher):
    fetcher = full_site(fetcher)
    fetcher.set_search("psn wallet", "<html><body>new layout</body></html>")
    s = make_settings(amazon={"search_queries": ["playstation gift card", "psn wallet"]})
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    assert test.checks[1].ok is None and "1 of 2 searches not understood" in test.checks[1].detail


def test_network_error_on_gift_card_page_is_not_a_reading_failure(fetcher):
    fetcher = full_site(fetcher)
    fetcher.fail.add(fetcher.product_url("B0PSN02000"))
    test = selftest.run(_settings(), fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    assert test.checks[2].ok is None and test.verdict == "pass"


def test_control_server_error_is_inconclusive(fetcher):
    fetcher = control_fetcher(fetcher)
    fetcher.set_product("B0PSN01000", "<html>Sorry! Something went wrong</html>", status=500)
    result = in_stock_control(fetcher, selftest.CONTROL_QUERIES, NO_PAUSE)
    assert result.ok is None and "failed" in result.detail


def test_control_stops_when_asked(fetcher):
    fetcher = control_fetcher(fetcher)
    result = in_stock_control(fetcher, selftest.CONTROL_QUERIES, lambda: True)
    assert result.ok is None and result.detail == "stopped" and fetcher.requests == []


def test_bot_check_is_retried_with_a_fresh_session(fetcher):
    fetcher = full_site(fetcher)
    real_get, calls = fetcher.get, []

    def flaky_get(url):  # the first request of the test gets Amazon's HTTP 202 challenge
        calls.append(url)
        if len(calls) == 1:
            from scanamzn.fetcher import Page

            return Page(url, 202, "<html><script src='challenge.js'></script></html>")
        return real_get(url)

    fetcher.get = flaky_get
    waits, progress = [], []
    test = selftest.run(_settings(), fetcher, NO_PAUSE, lambda k, h: None, progress.append, wait=waits.append)
    assert waits == [selftest.BLOCK_RETRY_WAIT] and fetcher.resets == 1
    assert test.checks[0].ok is True and "retry" in test.checks[0].detail
    assert any("fresh session" in p for p in progress)


def test_http_202_is_a_bot_check():
    from scanamzn.parser import is_blocked, parse_product

    assert is_blocked("<html><script src='challenge.js'></script></html>", 202)
    assert parse_product("<html></html>", "B0X", 202).status.value == "blocked"


def test_monitor_dumps_to_the_default_folder_with_home_expanded(fetcher, dispatcher, clock, tmp_path, monkeypatch):
    from scanamzn.config import from_dict

    monkeypatch.setenv("HOME", str(tmp_path))
    quiet = {"selftest_every_hours": 0, "request_delay_seconds": [0, 0]}
    amazon = {"asins": ["B0PSN02000"], "watch_known_listings": False, "search_queries": []}
    s = from_dict({"amazon": amazon, "monitor": {"debug_dump_dir": "~/.scanamzn/debug", **quiet}})
    assert s.debug_dump_dir == str(tmp_path / ".scanamzn" / "debug")
    fetcher.set_product("B0PSN02000", '<span id="productTitle">x</span><div>redesigned</div>')
    Monitor(s, fetcher, dispatcher, clock=clock).run_cycle()
    assert len(list((tmp_path / ".scanamzn" / "debug").glob("*.html"))) == 1

    # Without the setting, pages go next to the status file (where `ps5 export` looks).
    s2 = from_dict({"amazon": amazon, "monitor": {"status_file": str(tmp_path / "state" / "status.json"), **quiet}})
    Monitor(s2, fetcher, dispatcher, clock=clock).run_cycle()
    assert len(list((tmp_path / "state" / "debug").glob("*.html"))) == 1


def test_monitor_searches_every_round_when_nothing_is_watched(fetcher, dispatcher, clock):
    s = make_settings(amazon={"search_queries": ["psn"]}, monitor={"search_every_cycles": 3})
    m = Monitor(s, fetcher, dispatcher, clock=clock)
    fetcher.set_search("psn", '<html><body>No results for "psn".</body></html>')
    for _ in range(3):
        m.run_cycle()
    assert fetcher.requests.count(fetcher.search_url("psn")) == 3


def test_known_listings_are_watched_by_default():
    from scanamzn.config import KNOWN_LISTINGS, from_dict

    assert from_dict({}).asins == list(KNOWN_LISTINGS)
    assert from_dict({"amazon": {"watch_known_listings": False}}).asins == []


def test_selftest_command_clears_old_pages_and_reports_inconclusive(cli_env, monkeypatch, tmp_path, capsys):
    from .conftest import FakeFetcher

    state = tmp_path / "state"
    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(state / "status.json"))
    monkeypatch.setenv("SCANAMZN_SEARCH_QUERIES", "playstation gift card")
    (state / "debug").mkdir(parents=True)
    (state / "debug" / "selftest_product_OLD.html").write_text("old")
    fake = full_site(FakeFetcher())
    for q in selftest.CONTROL_QUERIES:
        fake.set_search(q, fixture("captcha.html"))
    monkeypatch.setattr(cli, "_fetcher", lambda s: fake)
    monkeypatch.setattr(cli.time, "sleep", lambda t: None)
    sent = []

    class Capture:
        name, rationed = "telegram", False

        def send(self, message):
            sent.append(message)

    monkeypatch.setattr(cli, "build_notifiers", lambda s: [Capture()])
    assert cli.main(["selftest", "--send"]) == 3
    assert not (state / "debug" / "selftest_product_OLD.html").exists()
    assert sent[0].subject.startswith("⚠️") and "not conclusive" in sent[0].body


# --- Amazon's home page became a JavaScript challenge (seen on the user's phone) ----


def test_selftest_starts_with_a_watched_gift_card_page_not_the_home_page(fetcher):
    fetcher = full_site(fetcher)
    s = _settings(asins=["B0PSN02000"])
    dumped = []
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: dumped.append(k), lambda m: None)
    assert "https://www.amazon.in/" not in fetcher.requests
    assert fetcher.requests[0] == fetcher.product_url("B0PSN02000")
    assert fetcher.requests.count(fetcher.product_url("B0PSN02000")) == 1  # loaded once, reused
    assert test.checks[0].ok is True and "product_B0PSN02000" in dumped
    assert test.verdict == "pass"


def test_selftest_saves_the_page_when_the_first_page_stays_blocked(fetcher):
    s = _settings(asins=["B0PSN02000"])
    fetcher.set_product("B0PSN02000", fixture("aws_waf_challenge.html"), status=202)
    dumped, waits = [], []
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: dumped.append(k), lambda m: None, wait=waits.append)
    assert waits == [selftest.BLOCK_RETRY_WAIT] * 2 and fetcher.resets == 2
    assert dumped == ["blocked_page"] and test.verdict == "inconclusive"
    assert "bot check (HTTP 202)" in test.checks[0].detail



def test_selftest_opens_listings_checked_in_turn_and_saves_only_some(fetcher):
    fetcher = full_site(fetcher)
    s = _settings(asins=["B0PSN02000"])
    s.rotating_asins = ["B0ROT00001", "B0ROT00002", "B0ROT00003"]
    for asin in s.rotating_asins:
        fetcher.set_product(asin, fixture("product_unavailable.html"))
    fetcher.set_product("B0ROT00003", '<span id="productTitle">x</span><div>redesigned</div>')
    dumped = []
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: dumped.append(k), lambda m: None)
    opened = [u.rsplit("/", 1)[1] for u in fetcher.requests if "/dp/" in u]
    assert {"B0ROT00001", "B0ROT00002", "B0ROT00003"} <= set(opened)
    pages = [k for k in dumped if k.startswith("product_")]
    assert "product_B0ROT00001" not in pages and "product_B0ROT00002" not in pages  # plain "unavailable"
    assert "product_B0ROT00003" in pages and test.checks[2].ok is False  # not understood: saved, reported


def test_removed_listing_is_reported_plainly_and_not_as_a_failure(fetcher):
    fetcher = full_site(fetcher)
    s = _settings(asins=["B0PSN02000"])
    s.rotating_asins = ["B0GONE0001"]  # no page: Amazon's 404 "dog" page
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    pages = test.checks[2]
    assert pages.ok is True and any("B0GONE0001  page not found" in line for line in pages.lines)


def test_selftest_opens_main_cards_and_only_a_few_values_checked_in_turn(fetcher):
    fetcher = full_site(fetcher)
    s = _settings(asins=["B0PSN02000"])
    s.rotating_asins = [f"B0ROT0000{i}" for i in range(1, 10)]
    for asin in s.rotating_asins:
        fetcher.set_product(asin, fixture("product_unavailable.html"))
    selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    opened = {u.rsplit("/", 1)[1] for u in fetcher.requests if "/dp/" in u}
    assert "B0PSN02000" in opened and len(opened & set(s.rotating_asins)) == selftest.OTHER_VALUES


def test_selftest_command_pauses_the_monitor_while_it_runs(cli_env, monkeypatch, tmp_path):
    from .conftest import FakeFetcher

    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(tmp_path / "state" / "status.json"))
    monkeypatch.setenv("SCANAMZN_SEARCH_QUERIES", "playstation gift card")
    monkeypatch.setattr(cli, "_fetcher", lambda s: full_site(FakeFetcher()))
    monkeypatch.setattr(cli.time, "sleep", lambda t: None)
    seen = []
    real_run = selftest.run

    def run(settings, *a, **kw):
        with open(selftest.pause_file(settings), encoding="ascii") as fh:
            seen.append(selftest.pause_holder_alive(fh))  # as the monitor sees it
        return real_run(settings, *a, **kw)

    monkeypatch.setattr(selftest, "run", run)
    cli.main(["selftest"])
    assert seen == [True] and not list((tmp_path / "state").glob("paused-until*"))


def test_selftest_in_focused_mode_opens_the_focused_cards_and_skips_searches(fetcher):
    fetcher = full_site(fetcher)
    s = _settings(asins=["B0PSN02000"], focus_asins=["B0PSN05000"])
    assert not selftest.extensive_mode(s)
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    assert [c.name for c in test.checks] == ["Amazon.in reachable", "Gift card pages", selftest.CONTROL_CHECK]
    opened = [u.rsplit("/", 1)[1] for u in fetcher.requests if "/dp/" in u]
    assert opened[:2] == ["B0PSN05000", "B0PSN02000"]  # focused cards (+ the user's own)
    assert fetcher.search_url("playstation gift card") not in fetcher.requests
    assert test.verdict == "pass"
