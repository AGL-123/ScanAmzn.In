"""The curl and browser transports, the browser rescue and the request-style ranking."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from scanamzn import fetcher as fetcher_mod
from scanamzn.browser import BrowserSession, find_browser
from scanamzn.curl import CurlError, CurlSession

from .conftest import product_html
from .fakesite import FakeAmazon


# A Chromium on PATH, or the one named in SCANAMZN_TEST_BROWSER, or Playwright's.
BROWSER = find_browser(os.environ.get("SCANAMZN_TEST_BROWSER", "")) or find_browser("/opt/pw-browsers/chromium")
needs_browser = pytest.mark.skipif(BROWSER is None, reason="no Chromium on this machine")
needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="no curl on this machine")


@pytest.fixture
def site():
    fake = FakeAmazon()
    yield fake
    fake.close()


@pytest.fixture
def browser(tmp_path, site):
    session = BrowserSession(BROWSER, "127.0.0.1", tmp_path / "profile", log_path=tmp_path / "browser.log")
    yield session
    session.close()


# --- the real browser against a site that challenges like Amazon -------------------------


@needs_browser
def test_browser_runs_the_challenge_and_then_reads_pages_with_fetch(site, browser):
    first = browser.get(f"{site.base}/dp/B07K6RYVJ5")
    assert first.status_code == 200 and 'id="productTitle"' in first.text and "gokuProps" not in first.text
    assert site.hits["/dp/B07K6RYVJ5"] == 2  # the challenge, then the page after the browser's reload
    second = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert second.status_code == 200 and "B093QF35KZ" in second.text
    assert site.hits["/dp/B093QF35KZ"] == 1  # read with fetch(): one request, no challenge
    # Images, styles and Amazon's other scripts are never downloaded.
    assert not {"/img.jpg", "/s.css", "/app.js"} & set(site.hits) and browser.blocked_requests >= 2


@needs_browser
def test_browser_gets_past_the_challenge_again_when_its_token_expires(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.expire_tokens()
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 200 and "B093QF35KZ" in page.text
    # fetch() got the challenge, then the browser loaded the page, ran it and reloaded.
    assert site.hits["/dp/B093QF35KZ"] == 3


@needs_browser
def test_browser_hands_back_a_captcha_unsolved(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.captcha = True
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 405 and "captcha.js" in page.text
    assert "/captcha.js" not in site.hits  # never even loaded


@needs_browser
def test_browser_restarts_after_it_was_killed(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    browser._pipe.proc.kill()
    browser._pipe.proc.wait(5)
    with pytest.raises(Exception):
        browser.get(f"{site.base}/dp/B093QF35KZ")
    page = browser.get(f"{site.base}/dp/B093QF35KZ")  # starts a new one
    assert page.status_code == 200


@needs_browser
def test_a_browser_left_over_from_a_killed_bot_is_replaced(tmp_path, site):
    first = BrowserSession(BROWSER, "127.0.0.1", tmp_path / "profile")
    first.get(f"{site.base}/dp/B07K6RYVJ5")
    old = first._pipe.proc
    # Full Chromium locks its profile (the headless shell doesn't, and has nothing to hand over to).
    locked = (tmp_path / "profile" / "SingletonLock").is_symlink()
    second = BrowserSession(BROWSER, "127.0.0.1", tmp_path / "profile")
    try:
        assert second.get(f"{site.base}/dp/B093QF35KZ").status_code == 200
        if locked:
            assert old.wait(10) is not None  # the old one was stopped, not handed the work
    finally:
        second.close()
        first.close()


def test_missing_browser_program_is_reported(tmp_path):
    session = BrowserSession(str(tmp_path / "no-such-chromium"), "www.amazon.in", tmp_path / "profile")
    with pytest.raises(Exception, match="browser"):
        session.get("https://www.amazon.in/dp/B07K6RYVJ5")
    session.close()


def test_find_browser(tmp_path, monkeypatch):
    program = tmp_path / "chromium-browser"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_browser() == str(program)
    assert find_browser(str(program)) == str(program)
    assert find_browser(str(tmp_path / "missing")) is None
    monkeypatch.setenv("PATH", "")
    assert find_browser() is None


# --- curl --------------------------------------------------------------------------------


@needs_curl
def test_curl_session_sends_headers_and_cookies(site):
    session = CurlSession(http2=True)
    try:
        session.headers["User-Agent"] = "Mozilla/5.0 test"
        page = session.get(f"{site.base}/dp/B07K6RYVJ5")
        assert page.status_code == 202 and "gokuProps" in page.text  # curl can't run the challenge
        session.cookies.set("aws-waf-token", "9999999999999", domain="127.0.0.1")
        page = session.get(f"{site.base}/dp/B07K6RYVJ5")
        assert page.status_code == 200 and page.url.endswith("/dp/B07K6RYVJ5")
        with pytest.raises(CurlError):
            session.get("http://127.0.0.1:1/", timeout=3)
    finally:
        session.close()
    assert not Path(session._dir).exists()


# --- the fetcher: rescue by the browser, switching, ranking ---------------------------------


CHALLENGE = "<html><script>window.gokuProps = {}</script>challenge.js</html>"


class _Session:
    """A session of any transport: answers from a script, records requests."""

    def __init__(self, transport, answers, sent):
        self.transport, self.answers, self.sent = transport, answers, sent
        self.headers, self.cookies = {}, type("Jar", (), {"set": lambda *a, **k: None})()
        self.closed = False

    def get(self, url, **kw):
        self.sent.append((self.transport, url))
        answer = self.answers(self.transport, url)
        if isinstance(answer, Exception):
            raise answer
        status, text = (answer, CHALLENGE) if answer == 202 else (answer, product_html("Card", False))
        return type("R", (), {"url": url, "status_code": status, "text": text})()

    def close(self):
        self.closed = True


@pytest.fixture
def make(monkeypatch, tmp_path):
    def build(answers, **kw):
        sent = []
        monkeypatch.setattr(fetcher_mod, "find_browser", lambda path="": "/usr/bin/chromium-browser")
        monkeypatch.setattr(fetcher_mod.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(fetcher_mod, "cffi_requests", None)
        f = fetcher_mod.Fetcher(profile_file=tmp_path / "request-profile",
                                ranking_file=tmp_path / "request-ranking", **kw)
        sessions = []

        def new_session(profile):
            s = _Session(profile.transport, answers, sent)
            sessions.append(s)
            return s

        f._new_session = new_session
        f.wait = lambda t: None
        f.sessions_made = sessions
        return f, sent

    return build


def test_default_order_puts_the_proven_style_first_and_the_browser_next(make):
    f, _ = make(lambda t, u: 200)
    assert f.profiles == ("legacy", "browser", "curl", "plain", "client-hints")
    assert f.rescue


def test_a_bot_check_is_read_by_the_browser_at_once(make):
    f, sent = make(lambda t, url: 200 if t == "browser" else 202)
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.status_code == 200 and not page.blocked and page.profile == "browser"
    assert sent == [("requests", "https://www.amazon.in/dp/B0X"), ("browser", "https://www.amazon.in/dp/B0X")]
    assert f.stats["legacy"]["blocked"] == 1 and f.stats["browser"]["ok"] == 1
    assert f.profile == "legacy"  # one bot check isn't a pattern yet
    f.get("https://www.amazon.in/dp/B0Y")
    f.get("https://www.amazon.in/dp/B0Z")  # after two in a row: the browser it is
    assert f.profile == "browser"
    assert (f.profile_file.read_text().strip()) == "browser"
    sent.clear()
    f.get("https://www.amazon.in/dp/B0X")
    assert sent == [("browser", "https://www.amazon.in/dp/B0X")]


def test_rescue_failure_pauses_the_browser_for_a_while(make):
    clock = [0.0]
    f, sent = make(lambda t, url: RuntimeError("chromium crashed") if t == "browser" else 202)
    f.clock = lambda: clock[0]
    assert f.get("https://www.amazon.in/dp/B0X").blocked  # the bot check stands; the monitor backs off
    f.reset()
    sent.clear()
    assert f.get("https://www.amazon.in/dp/B0X").blocked
    assert ("browser", "https://www.amazon.in/dp/B0X") not in sent  # paused, not tried again at once
    f.reset()
    assert f.profile == "curl"  # the next style, skipping the browser that just failed
    clock[0] += fetcher_mod.RESCUE_PAUSE_SECONDS + 1
    f.get("https://www.amazon.in/dp/B0X")
    assert sent[-2:] == [("curl", "https://www.amazon.in/dp/B0X"), ("browser", "https://www.amazon.in/dp/B0X")]


def test_no_rescue_when_the_browser_also_gets_a_bot_check(make):
    f, sent = make(lambda t, url: 202)
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.blocked and page.profile == "legacy"
    f.reset()
    f.get("https://www.amazon.in/dp/B0X")
    f.reset()
    assert f.profile == "browser"  # then the next style in order...
    sent.clear()
    f.get("https://www.amazon.in/dp/B0X")
    assert sent == [("browser", "https://www.amazon.in/dp/B0X")]  # ...which can't rescue itself


def test_transport_errors_switch_style_after_three(make):
    f, _ = make(lambda t, url: RuntimeError("no route") if t == "requests" else 200, rescue=False)
    for _ in range(3):
        with pytest.raises(fetcher_mod.FetchError):
            f.get("https://www.amazon.in/dp/B0X")
    assert f.get("https://www.amazon.in/dp/B0X").profile == "browser"
    assert f.stats["legacy"]["errors"] == 3


def test_monitor_and_probe_follow_the_ranking_file(make, tmp_path):
    f, sent = make(lambda t, url: 200)
    f.get("https://www.amazon.in/dp/B0X")
    assert f.profile == "legacy"
    (tmp_path / "request-ranking").write_text("curl\nnot-a-style\nlegacy\n")
    f.get("https://www.amazon.in/dp/B0X")
    assert f.profiles[:2] == ("curl", "legacy") and f.profile == "curl" and sent[-1][0] == "curl"
    assert all(s.closed for s in f.sessions_made[:1])  # the old style's session was closed
    # A new process (restart) starts with the ranking's order and the remembered style.
    g, _ = make(lambda t, url: 200)
    assert g.profiles[:2] == ("curl", "legacy") and g.profile == "curl"


def test_only_one_style_for_the_probe(make):
    f, _ = make(lambda t, url: 202, only="curl", rescue=False)
    assert f.profiles == ("curl",)
    assert f.get("https://www.amazon.in/dp/B0X").blocked
    with pytest.raises(fetcher_mod.FetchError, match="not available"):
        make(lambda t, url: 200, only="nonsense")


def test_explicit_backend_limits_the_styles(make):
    f, _ = make(lambda t, url: 200, backend="browser")
    assert f.profiles == ("browser",)
    f, _ = make(lambda t, url: 200, backend="requests")
    assert f.profiles == ("legacy", "plain", "client-hints") and not f.rescue
