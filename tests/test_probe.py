"""`ps5 probe`: measuring the request styles and adopting the order that works."""

from __future__ import annotations

import pytest

from scanamzn import cli, probe
from scanamzn import fetcher as fetcher_mod

from .conftest import product_html

CHALLENGE = "<html><script>window.gokuProps = {}</script>challenge.js</html>"


class _Session:
    def __init__(self, answer, sent, name):
        self.answer, self.sent, self.name = answer, sent, name
        self.headers, self.cookies = {}, type("Jar", (), {"set": lambda *a, **k: None})()

    def get(self, url, **kw):
        self.sent.append((self.name, url))
        status = self.answer(self.name, url)
        if isinstance(status, Exception):
            raise status
        text = {202: CHALLENGE, 200: product_html("PlayStation card", False), 299: "<html>?</html>"}[status]
        return type("R", (), {"url": url, "status_code": 200 if status == 299 else status, "text": text})()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCANAMZN_SMTP_USER", "someone@gmail.com")
    monkeypatch.setenv("SCANAMZN_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SCANAMZN_TIMEZONE", "Asia/Jerusalem")
    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(tmp_path / "state" / "status.json"))
    monkeypatch.setattr(cli.time, "sleep", lambda t: None)


@pytest.fixture
def styles(monkeypatch):
    """Every style installed; each answers as the test says."""
    sent = []
    answers = {}
    monkeypatch.setattr(fetcher_mod, "find_browser", lambda path="", names=None: "/usr/bin/headless_shell")
    monkeypatch.setattr(fetcher_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(fetcher_mod, "cffi_requests", None)
    monkeypatch.setattr(fetcher_mod.Fetcher, "_new_session",
                        lambda self, p: _Session(lambda name, url: answers.get(name, 200), sent, p.name))
    return answers, sent


def test_run_measures_each_style_and_ranks_them(styles):
    answers, sent = styles
    answers.update({"legacy": 202, "curl": RuntimeError("curl: (1) no HTTP/2"), "plain": 299})
    urls = [("B07K6RYVJ5", "https://www.amazon.in/dp/B07K6RYVJ5"), ("B093QF35KZ", "https://www.amazon.in/dp/B093QF35KZ")]
    waits, dumped = [], []

    def make(name):
        if name == "client-hints":
            raise fetcher_mod.FetchError("request style 'client-hints' is not available on this device")
        return fetcher_mod.Fetcher(only=name, rescue=False)

    results = probe.run(urls, ["legacy", "browser", "curl", "plain", "client-hints"], make, wait=waits.append,
                        progress=lambda text: None, spacing=7, tries=4, dump=lambda k, html: dumped.append(k))
    by = {r.name: r for r in results}
    assert (by["legacy"].blocked, by["browser"].read, by["curl"].errors, by["plain"].unclear) == (4, 4, 4, 4)
    assert by["client-hints"].unavailable
    # Pages alternate between the cards, spaced like the monitor; a pause between styles.
    assert [u for n, u in sent if n == "browser"] == [u for _, u in urls] * 2
    assert waits.count(7) == 4 * 3 and waits.count(probe.BETWEEN_STYLES_SECONDS) == 4
    assert dumped and all(k.startswith("probe_plain_") for k in dumped)  # not understood: kept for a fix
    assert probe.rank(results) == ["browser", "plain", "legacy", "curl", "client-hints"]
    text = probe.summary(results, probe.rank(results), True, 4, 7, browser_missing=False)
    assert "✅ browser: 4 of 4 read" in text and "❌ legacy: 0 of 4 read, 4 bot checks" in text
    assert "The bot now uses browser first" in text and "Tip:" not in text


def test_probe_command_saves_the_order_and_the_monitor_picks_it_up(styles, monkeypatch, tmp_path, capsys):
    answers, sent = styles
    answers.update({"legacy": 202, "plain": 202, "client-hints": 202})
    state = tmp_path / "state"
    messages = []

    class Capture:
        name, rationed = "telegram", False

        def send(self, message):
            messages.append(message)

    monkeypatch.setattr(cli, "build_notifiers", lambda s: [Capture()])
    # The monitor that runs meanwhile, still on the old order.
    from scanamzn.config import load

    monitor_fetcher = cli._fetcher(load("config.toml"))
    assert monitor_fetcher.profile == "legacy"

    assert cli.main(["probe", "--send", "--tries", "2"]) == 0
    out = capsys.readouterr().out
    assert "5 request styles, 2 pages each, 15 s apart" in out  # focused: 30 s over two cards
    assert (state / "request-ranking").read_text().split() == ["browser", "curl", "legacy", "plain", "client-hints"]
    assert (state / "request-profile").read_text().strip() == "browser"
    assert "connection test" in messages[0].subject and "✅ browser: 2 of 2 read" in messages[0].body
    assert (state / "probe.txt").exists() and not (state / "paused-until").exists()

    sent.clear()
    monitor_fetcher.wait = lambda t: None
    monitor_fetcher.get(monitor_fetcher.product_url("B07K6RYVJ5"))
    assert sent == [("browser", "https://www.amazon.in/dp/B07K6RYVJ5")]


def test_probe_keeps_the_old_order_when_nothing_gets_through(styles, monkeypatch, tmp_path, capsys):
    answers, _ = styles
    answers.update({name: 202 for name in fetcher_mod.PROFILE_NAMES})
    state = tmp_path / "state"
    state.mkdir()
    (state / "request-ranking").write_text("plain\nlegacy\n")
    monkeypatch.setattr(cli, "build_notifiers", lambda s: [])
    assert cli.main(["probe", "--tries", "1", "--style", "legacy", "--style", "curl"]) == 3
    assert "keeps its previous order" in capsys.readouterr().out
    assert (state / "request-ranking").read_text() == "plain\nlegacy\n"


def test_probe_with_some_styles_keeps_the_others_after_them(styles, monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "request-ranking").write_text("plain\nlegacy\n")
    monkeypatch.setattr(cli, "build_notifiers", lambda s: [])
    assert cli.main(["probe", "--tries", "1", "--style", "curl"]) == 0
    assert (state / "request-ranking").read_text().split() == ["curl", "plain", "legacy", "browser", "client-hints"]


def test_a_broken_browser_is_named_as_the_cause_not_amazon(styles):
    answers, sent = styles
    error = RuntimeError("the browser did not start: CANNOT LINK EXECUTABLE \"/data/data/com.termux/files/usr/lib/"
                         "chromium/headless_shell\": cannot locate symbol \"foo\" referenced by libnss3.so (EOF)")
    answers.update({"legacy": 202, "browser": error})
    urls = [("B07K6RYVJ5", "https://www.amazon.in/dp/B07K6RYVJ5")]
    results = probe.run(urls, ["legacy", "browser"], lambda n: fetcher_mod.Fetcher(only=n, rescue=False),
                        wait=lambda t: None, progress=lambda text: None, spacing=5, tries=2, browsers={"browser"})
    text = probe.summary(results, probe.rank(results), False, 2, 5, browser_missing=False, browsers={"browser"})
    assert "cannot locate symbol" in text and "libnss3.so" in text  # the reason, not cut off
    assert "did not work on this phone" in text and "ps5 export" in text
    assert "Amazon may be checking" not in text


def test_a_slow_failing_browser_is_not_tried_again(styles):
    answers, sent = styles
    answers["browser"] = 202
    now = [0.0]

    def clock():
        now[0] += 20  # every call: each request seems to take 40 s
        return now[0]

    urls = [("B07K6RYVJ5", "https://www.amazon.in/dp/B07K6RYVJ5")]
    results = probe.run(urls, ["browser"], lambda n: fetcher_mod.Fetcher(only=n, rescue=False),
                        wait=lambda t: None, progress=lambda text: None, spacing=5, tries=4,
                        clock=clock, slow=30, browsers={"browser"})
    assert results[0].blocked == 4 and results[0].cut_short and len(sent) == 1
    assert "stopped after a slow failure" in results[0].line()


def test_duration_estimate_covers_slow_browsers():
    typical, worst = probe.duration(6, 2, 4, 12.5, 20)
    assert 4 * 60 < typical < 7 * 60 and worst > typical


def test_refused_browser_gets_its_own_advice(styles):
    answers, sent = styles
    answers.update({name: 202 for name in fetcher_mod.PROFILE_NAMES})
    urls = [("B07K6RYVJ5", "https://www.amazon.in/dp/B07K6RYVJ5")]
    results = probe.run(urls, ["legacy", "browser"], lambda n: fetcher_mod.Fetcher(only=n, rescue=False),
                        wait=lambda t: None, progress=lambda text: None, spacing=5, tries=1, browsers={"browser"})
    text = probe.summary(results, probe.rank(results), False, 1, 5, browser_missing=False, browsers={"browser"})
    assert "not even the browser engine" in text
