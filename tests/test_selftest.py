import random

from scanamzn import cli, selftest
from scanamzn.monitor import Monitor
from scanamzn.selftest import in_stock_control

from .conftest import fixture, make_settings, product_html

HOME = '<html><body><span id="glow-ingress-line2">Israel</span></body></html>'
NO_PAUSE = lambda: None  # noqa: E731


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
    fetcher.pages["https://www.amazon.in/"] = (200, HOME)
    fetcher.set_search("playstation gift card", fixture("search_results.html"))
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
    assert "something is not right" in test.summary()


def test_full_selftest_stops_on_captcha(fetcher):
    fetcher.pages["https://www.amazon.in/"] = (200, fixture("captcha.html"))
    s = make_settings(amazon={"search_queries": ["psn"]})
    test = selftest.run(s, fetcher, NO_PAUSE, lambda k, h: None, lambda m: None)
    assert len(test.checks) == 1 and test.checks[0].ok is False and not test.passed


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
