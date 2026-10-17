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
def test_a_challenge_needing_more_than_the_strict_filter_allows_gets_a_second_try(site, browser, monkeypatch):
    from scanamzn import browser as browser_mod

    monkeypatch.setattr(browser_mod, "CHALLENGE_SECONDS", 2.0)
    site.script_challenge = True
    page = browser.get(f"{site.base}/dp/B07K6RYVJ5", timeout=3)
    assert page.status_code == 200 and 'id="productTitle"' in page.text
    assert site.hits["/waf.js"] == 1  # refused at first, then let through
    assert "Script 127.0.0.1" in browser.recently_blocked
    assert not {"/img.jpg", "/s.css"} & set(site.hits)  # pictures and styles still held back
    assert not browser._relaxed  # only for that one try


@needs_browser
def test_browser_hands_back_a_captcha_unsolved(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.captcha = True
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 405 and "captcha.js" in page.text
    assert "/captcha.js" not in site.hits  # never even loaded


@needs_browser
def test_browser_killed_while_idle_restarts_within_the_same_check(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    browser._pipe.proc.kill()  # e.g. Android's low-memory killer between rounds
    browser._pipe.proc.wait(5)
    page = browser.get(f"{site.base}/dp/B093QF35KZ")  # notices it, starts a new one
    assert page.status_code == 200 and "B093QF35KZ" in page.text


def _renderers(profile):
    """pids of the browser's page processes (renderers) for this profile."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{entry}/cmdline").read_bytes()
        except OSError:
            continue
        if b"--type=renderer" in cmd and str(profile).encode() in cmd:
            found.append(int(entry))
    return found


@needs_browser
def test_a_crashed_page_is_noticed_at_once_and_the_check_still_succeeds(site, browser, tmp_path):
    import signal
    import time

    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    renderers = _renderers(tmp_path / "profile")
    if not renderers:
        pytest.skip("renderer runs inside the browser process here")
    for pid in renderers:
        os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    started = time.monotonic()
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 200 and time.monotonic() - started < 4  # no 30 s timeout, no 5 s wait to kill


@needs_browser
def test_a_network_blip_during_a_load_doesnt_leave_the_browser_stuck(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.expire_tokens()
    site.drop_pages = 2  # fetch() and the navigation get no answer
    with pytest.raises(Exception):
        browser.get(f"{site.base}/dp/B093QF35KZ")
    site.drop_pages = 0  # the network is back
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 200 and "B093QF35KZ" in page.text


@needs_browser
def test_a_dropped_challenge_reload_is_an_error_not_an_empty_page(site, browser):
    site.drop_token_reloads = 2  # Chrome itself retries a dropped request once
    with pytest.raises(Exception, match="could not load the page"):
        browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.drop_token_reloads = 0  # the network is back
    page = browser.get(f"{site.base}/dp/B07K6RYVJ5")
    assert page.status_code == 200 and 'id="productTitle"' in page.text


@needs_browser
def test_when_amazon_keeps_refusing_the_browser_it_stops_after_one_retry(site, browser):
    import time

    site.accept_tokens = False
    started = time.monotonic()
    page = browser.get(f"{site.base}/dp/B07K6RYVJ5")
    assert page.status_code == 202  # a bot check, whichever page the browser was left on
    hits = site.hits["/dp/B07K6RYVJ5"]
    assert 2 <= hits <= 3  # the page and its reloads, at most MAX_DOCUMENTS - not dozens
    assert time.monotonic() - started < 15
    time.sleep(1.5)  # the challenge page was left: nothing keeps reloading it
    assert site.hits["/dp/B07K6RYVJ5"] == hits


@needs_browser
def test_a_challenge_served_with_200_is_run_too(site, browser):
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    site.expire_tokens()
    site.challenge_status = 200
    page = browser.get(f"{site.base}/dp/B093QF35KZ")
    assert page.status_code == 200 and "B093QF35KZ" in page.text and "gokuProps" not in page.text


@needs_browser
def test_stopping_the_bot_ends_a_load_at_once(site, tmp_path):
    from scanamzn.browser import BrowserStopped

    site.accept_tokens = False
    session = BrowserSession(BROWSER, "127.0.0.1", tmp_path / "p", wait=lambda t: True)  # stop requested
    try:
        with pytest.raises(BrowserStopped):
            session.get(f"{site.base}/dp/B07K6RYVJ5")
    finally:
        session.close()


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


@needs_browser
def test_fetcher_reads_a_challenged_page_with_the_real_browser(site, tmp_path, monkeypatch):
    f = fetcher_mod.Fetcher(site.base.removeprefix("http://"), browser_path=BROWSER, browser_dir=tmp_path / "b")
    monkeypatch.setattr(f, "product_url", lambda asin: f"{site.base}/dp/{asin}")
    f.wait = lambda t: None
    f.profile_index = f.profiles.index("legacy")
    try:
        page = f.get(f.product_url("B07K6RYVJ5"))  # Python's requests get the challenge...
        assert page.status_code == 200 and page.profile == "browser" and not page.blocked  # ...the browser the page
        assert f.stats["legacy"]["blocked"] == 1 and f.stats["browser"]["ok"] == 1
        f.get(f.product_url("B093QF35KZ"))
        f.get(f.product_url("B093QF35KZ"))
        assert f.profile == "browser"
        assert f.get(f.product_url("B07K6RYVJ5")).profile == "browser"
    finally:
        f.close()


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
    """A fetcher whose sessions answer from a script. Sessions are named by
    transport ("requests", "curl", "browser"), except the full-Chromium style
    ("chromium"). two_browsers: the phone has both Chromium programs."""
    from scanamzn.browser import FULL_NAMES

    def build(answers, two_browsers=False, **kw):
        sent = []
        if two_browsers:
            monkeypatch.setattr(fetcher_mod, "find_browser", lambda path="", names=None:
                                "/usr/bin/chromium-browser" if names == FULL_NAMES else "/usr/bin/headless_shell")
        else:
            monkeypatch.setattr(fetcher_mod, "find_browser", lambda path="", names=None: "/usr/bin/headless_shell")
        monkeypatch.setattr(fetcher_mod.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(fetcher_mod, "cffi_requests", None)
        f = fetcher_mod.Fetcher(profile_file=tmp_path / "request-profile",
                                ranking_file=tmp_path / "request-ranking", **kw)
        sessions = []

        def new_session(profile):
            s = _Session("chromium" if profile.name == "chromium" else profile.transport, answers, sent)
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


def test_a_browser_that_gets_a_bot_check_too_is_closed_and_left_alone_for_a_while(make):
    clock = [0.0]
    f, sent = make(lambda t, url: 202)
    f.clock = lambda: clock[0]
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.blocked and page.profile == "legacy"
    assert [t for t, _ in sent] == ["requests", "browser"]
    assert "browser" not in f._sessions  # closed again: no idle Chromium
    f.reset()
    sent.clear()
    f.get("https://www.amazon.in/dp/B0X")
    assert [t for t, _ in sent] == ["requests"]  # resting: not tried again at once
    f.reset()
    assert f.profile == "curl"  # the switch skips it too
    clock[0] += fetcher_mod.BLOCKED_REST_SECONDS + 1  # a short rest after a bot check
    sent.clear()
    f.get("https://www.amazon.in/dp/B0X")
    assert [t for t, _ in sent] == ["curl", "browser"]


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


# --- two browser styles: the light headless shell, then the full Chromium -------------------


def test_both_browser_styles_are_offered_in_order(make):
    f, _ = make(lambda t, url: 200, two_browsers=True)
    assert f.profiles == ("legacy", "browser", "chromium", "curl", "plain", "client-hints")
    assert f.browser_paths == {"browser": "/usr/bin/headless_shell", "chromium": "/usr/bin/chromium-browser"}
    assert f.has_browser and f.is_browser("chromium") and not f.is_browser("curl")


def test_full_chromium_reads_the_page_when_the_light_browser_fails(make):
    answers = {"requests": 202, "browser": RuntimeError("headless_shell crashed"), "chromium": 200}
    f, sent = make(lambda t, url: answers[t], two_browsers=True)
    page = f.get("https://www.amazon.in/dp/B0X")
    assert not page.blocked and page.profile == "chromium"
    assert [t for t, _ in sent] == ["requests", "browser", "chromium"]
    sent.clear()
    f.get("https://www.amazon.in/dp/B0Y")  # the light browser is paused: straight to the full one
    assert [t for t, _ in sent] == ["requests", "chromium"]
    f.get("https://www.amazon.in/dp/B0Z")
    assert f.profile == "chromium"  # two bot checks in a row, both read by it: it takes over
    assert (f.profile_file.read_text().strip()) == "chromium"


def test_light_browser_with_a_bot_check_is_rescued_by_the_full_one(make, tmp_path):
    (tmp_path / "request-ranking").write_text("browser\nchromium\n")
    answers = {"browser": 202, "chromium": 200}
    f, sent = make(lambda t, url: answers.get(t, 202), two_browsers=True)
    assert f.profile == "browser"
    page = f.get("https://www.amazon.in/dp/B0X")
    assert not page.blocked and page.profile == "chromium"
    assert f.stats["browser"]["blocked"] == 1 and f.stats["chromium"]["ok"] == 1


def test_switching_skips_a_browser_that_just_failed(make):
    answers = {"requests": 202, "browser": RuntimeError("no memory"), "chromium": RuntimeError("no memory")}
    f, sent = make(lambda t, url: answers.get(t, 202), two_browsers=True)
    f.get("https://www.amazon.in/dp/B0X")
    f.reset()
    f.get("https://www.amazon.in/dp/B0X")
    f.reset()
    assert f.profile == "curl"  # both browsers failed a moment ago


@needs_browser
def test_both_real_chromium_builds_through_the_fetcher(site, tmp_path, monkeypatch):
    shell = find_browser("/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell")
    full = find_browser("/opt/pw-browsers/chromium")
    if not (shell and full):
        pytest.skip("needs Playwright's headless shell and full Chromium")
    from scanamzn.browser import FULL_NAMES

    monkeypatch.setattr(fetcher_mod, "find_browser",
                        lambda path="", names=None: full if names == FULL_NAMES else shell)
    for name in ("browser", "chromium"):
        f = fetcher_mod.Fetcher(site.base.removeprefix("http://"), browser_dir=tmp_path / "b", only=name,
                                rescue=False)
        monkeypatch.setattr(f, "product_url", lambda asin: f"{site.base}/dp/{asin}")
        f.wait = lambda t: None
        try:
            page = f.get(f.product_url("B07K6RYVJ5"))
            assert page.status_code == 200 and page.profile == name and 'id="productTitle"' in page.text
            assert (tmp_path / "b" / name).is_dir() and (tmp_path / "b" / f"{name}.log").exists()
        finally:
            f.close()


def test_only_one_chromium_runs_when_switching_between_browsers(make):
    answers = {"requests": 202, "browser": 202, "chromium": 200}
    f, sent = make(lambda t, url: answers[t], two_browsers=True)
    f.profile_index = f.profiles.index("browser")
    f.get("https://www.amazon.in/dp/B0X")  # the light browser is challenged, the full one reads it
    f.get("https://www.amazon.in/dp/B0Y")
    f.get("https://www.amazon.in/dp/B0Z")
    assert f.profile == "chromium"
    assert set(f._sessions) == {"chromium"}  # the light one was closed, not left idle


def test_network_outages_dont_change_the_request_style(make):
    import requests

    f, _ = make(lambda t, url: requests.ConnectionError("Network is unreachable"))
    for _ in range(6):
        with pytest.raises(fetcher_mod.FetchError):
            f.get("https://www.amazon.in/dp/B0X")
    assert f.profile == "legacy"  # every style would have failed the same way


def test_the_best_ranked_style_is_tried_again_after_an_hour(make, tmp_path):
    clock = [0.0]
    answers = {"requests": 202, "browser": 200}
    f, sent = make(lambda t, url: answers[t])
    f.clock = lambda: clock[0]
    for asin in ("B0X", "B0Y", "B0Z"):
        f.get(f"https://www.amazon.in/dp/{asin}")
    assert f.profile == "browser"
    answers["requests"] = 200  # Amazon lets the light requests through again
    clock[0] += fetcher_mod.RETRY_BEST_SECONDS + 1
    sent.clear()
    page = f.get("https://www.amazon.in/dp/B0X")
    assert f.profile == "legacy" and page.profile == "legacy" and sent == [("requests", "https://www.amazon.in/dp/B0X")]
    assert "browser" in f._sessions  # still there to read what Amazon blocks


def test_stopping_during_a_bot_check_starts_no_browser(make):
    f, sent = make(lambda t, url: 202)
    f.wait = lambda t: True  # the bot is stopping
    with pytest.raises(fetcher_mod.Stopped):
        f.get("https://www.amazon.in/dp/B0X")
    assert [t for t, _ in sent] == ["requests"]


def test_after_a_captcha_the_second_browser_is_not_tried(make, monkeypatch):
    answers = {"requests": 202, "browser": 405, "chromium": 200}
    f, sent = make(lambda t, url: answers[t], two_browsers=True)
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.blocked and [t for t, _ in sent] == ["requests", "browser"]


@needs_browser
def test_browser_check_command(monkeypatch, tmp_path, capsys):
    from scanamzn import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCANAMZN_SMTP_USER", "someone@gmail.com")
    monkeypatch.setenv("SCANAMZN_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SCANAMZN_TIMEZONE", "Asia/Jerusalem")
    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(tmp_path / "state" / "status.json"))
    monkeypatch.setenv("SCANAMZN_BROWSER_PATH", BROWSER)
    assert cli.main(["browser-check"]) == 0
    assert "✅ browser" in capsys.readouterr().out
    broken = tmp_path / "headless_shell"
    broken.write_text("#!/bin/sh\necho 'CANNOT LINK EXECUTABLE: libfoo.so not found' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setenv("SCANAMZN_BROWSER_PATH", str(broken))
    monkeypatch.setattr(fetcher_mod, "find_browser",
                        lambda path="", names=None: str(broken) if path else None)
    assert cli.main(["browser-check"]) == 1
    out = capsys.readouterr().out
    assert "❌ browser" in out and "libfoo.so not found" in out
    assert (tmp_path / "state" / "browser-check" / "browser-check.log").exists()  # for ps5 export


# --- round two: slow is not dead, hard limits, quiet browser ---------------------------------


@needs_browser
def test_a_stalled_connection_is_a_slow_page_not_a_dead_browser(site, browser, monkeypatch):
    import time

    from scanamzn import browser as browser_mod

    monkeypatch.setattr(browser_mod, "CHALLENGE_SECONDS", 1.0)
    browser.get(f"{site.base}/dp/B07K6RYVJ5")
    pid = browser._pipe.proc.pid
    site.expire_tokens()
    site.hang_seconds = 12  # the phone's connection stalls
    site.hits.clear()
    started = time.monotonic()
    with pytest.raises(Exception, match="did not load within"):  # a network-type error
        browser.get(f"{site.base}/dp/B093QF35KZ", timeout=2)
    assert time.monotonic() - started < 25
    assert site.hits["/dp/B093QF35KZ"] <= 2  # fetch(), maybe the navigation - never sent again
    assert browser._pipe is not None and browser._pipe.proc.pid == pid  # not killed and restarted
    site.hang_seconds = 0
    time.sleep(12)  # let the stalled requests finish
    assert browser.get(f"{site.base}/dp/B093QF35KZ").status_code == 200


@needs_browser
def test_a_busy_challenge_page_is_waited_for(site, browser):
    site.busy_ms = 11000  # longer than one 10 s state poll
    page = browser.get(f"{site.base}/dp/B07K6RYVJ5", timeout=20)
    assert page.status_code == 200 and site.hits["/dp/B07K6RYVJ5"] == 2  # not restarted, not re-requested


@needs_browser
def test_the_page_load_cap_holds_even_for_an_instant_challenge(tmp_path):
    import time

    site = FakeAmazon(challenge_delay_ms=0)
    session = BrowserSession(BROWSER, "127.0.0.1", tmp_path / "p")
    try:
        site.accept_tokens = False
        page = session.get(f"{site.base}/dp/B07K6RYVJ5")
        assert page.status_code == 202
        from scanamzn.browser import MAX_DOCUMENTS

        assert site.hits["/dp/B07K6RYVJ5"] <= MAX_DOCUMENTS
        hits = site.hits["/dp/B07K6RYVJ5"]
        time.sleep(1.5)
        session._alive()  # anything paused meanwhile is answered now - and refused
        time.sleep(0.5)
        assert site.hits["/dp/B07K6RYVJ5"] == hits
    finally:
        session.close()
        site.close()


@needs_browser
def test_a_stuck_challenge_gets_no_second_try_when_nothing_was_held_back(site, browser, monkeypatch):
    from scanamzn import browser as browser_mod

    monkeypatch.setattr(browser_mod, "CHALLENGE_SECONDS", 1.0)
    site.stuck_challenge = True
    page = browser.get(f"{site.base}/dp/B07K6RYVJ5", timeout=2)
    assert page.status_code == 202
    assert site.hits["/dp/B07K6RYVJ5"] == 1 and "/favicon.ico" not in site.hits  # one load, no favicon


def test_the_request_filter():
    session = BrowserSession("/bin/false", "www.amazon.in", "/nonexistent")
    sent = []
    session._pipe = type("P", (), {"send": lambda self, method, params, sid: sent.append(method)})()
    session._main_frame = "MAIN"

    def decide(url, kind, frame="OTHER"):
        sent.clear()
        session._on_request({"request": {"url": url}, "resourceType": kind, "requestId": "1", "frameId": frame},
                            "S")
        return sent[0] == "Fetch.continueRequest"

    assert decide("https://abc.eu-central-1.token.awswaf.com/abc/challenge.js", "Script")
    assert not decide("https://abc.ap-south-1.captcha.awswaf.com/abc/captcha.js", "Script")  # never the puzzle
    assert decide("https://www.amazon.in/dp/B0X", "Fetch")
    assert not decide("https://m.media-amazon.com/images/x.jpg", "Image")
    assert not decide("https://www.amazon.in/favicon.ico", "Other")
    assert not decide("https://aax-eu.amazon-adsystem.com/x", "Document")
    # Page loads of the tab: only while a load is under way, and only so many.
    assert not decide("https://www.amazon.in/dp/B0X", "Document", "MAIN")
    session._doc_limit = 2
    assert decide("https://www.amazon.in/dp/B0X", "Document", "MAIN")
    assert not decide("https://www.amazon.com/ap/signin", "Document", "MAIN")  # another site
    assert not decide("https://www.amazon.in/dp/B0X", "Document", "MAIN") and session._docs_refused


def test_the_browser_may_reach_only_amazon_and_the_check():
    args = BrowserSession("/usr/bin/headless_shell", "www.amazon.in", "/tmp/x")._args()
    rules = next(a for a in args if a.startswith("--host-resolver-rules="))
    assert "MAP * ~NOTFOUND" in rules and "EXCLUDE amazon.in" in rules and "EXCLUDE *.awswaf.com" in rules
    assert "--headless=new" not in args  # the headless shell is headless anyway
    proxied = BrowserSession("/usr/bin/chromium-browser", "www.amazon.in", "/tmp/x", proxy="http://p:8080")._args()
    assert not any(a.startswith("--host-resolver-rules=") for a in proxied) and "--headless=new" in proxied


# --- round two: one Chromium, CAPTCHAs, network failures, the hourly retry ----------------


def _browsers_open(f):
    return [n for n in f._sessions if f.is_browser(n)]


def test_with_a_browser_first_a_bot_check_never_leaves_two_chromiums(make, tmp_path):
    (tmp_path / "request-ranking").write_text("browser\nchromium\nlegacy\n")
    answers = {"browser": 200, "chromium": 200}
    f, sent = make(lambda t, url: answers[t], two_browsers=True)
    opened = []
    real_get_with = f._get_with

    def watch(p, url):
        page = real_get_with(p, url)
        opened.append(len(_browsers_open(f)))
        return page

    f._get_with = watch
    f.get("https://www.amazon.in/dp/B0X")
    answers["browser"] = 202  # one bot check for the light browser
    page = f.get("https://www.amazon.in/dp/B0Y")
    assert page.profile == "chromium" and not page.blocked
    answers["browser"] = 200
    for asin in ("B0Z", "B0W", "B0V"):
        f.get(f"https://www.amazon.in/dp/{asin}")
        assert len(_browsers_open(f)) == 1
    assert f.profile == "chromium"  # it took over at once
    assert max(opened) == 1  # never two at the same moment


def test_a_captcha_for_the_main_browser_is_not_passed_to_the_other(make, tmp_path):
    (tmp_path / "request-ranking").write_text("browser\nchromium\n")
    f, sent = make(lambda t, url: {"browser": 405, "chromium": 200}[t], two_browsers=True)
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.blocked and [t for t, _ in sent] == ["browser"] and f.last_rescue == "CAPTCHA"


def test_a_network_failure_during_a_rescue_doesnt_bench_the_browser(make):
    answers = {"requests": 202, "browser": RuntimeError("the page did not load within 30 s")}
    f, sent = make(lambda t, url: answers[t])
    page = f.get("https://www.amazon.in/dp/B0X")
    assert page.blocked and f.last_rescue.startswith("failed:")
    assert not f._paused("browser")  # tried again on the next bot check
    answers["browser"] = 200
    assert f.get("https://www.amazon.in/dp/B0Y").profile == "browser"


def test_a_failing_best_style_is_retried_less_and_less_often(make, tmp_path):
    clock = [0.0]
    (tmp_path / "request-ranking").write_text("curl\nlegacy\n")
    answers = {"curl": RuntimeError("curl exit 92: HTTP/2 stream error"), "requests": 200, "browser": 200}
    f, sent = make(lambda t, url: answers[t], rescue=False)
    f.clock = lambda: clock[0]
    for _ in range(4):
        try:
            f.get("https://www.amazon.in/dp/B0X")
        except fetcher_mod.FetchError:
            pass
    assert f.profile == "legacy"
    waits = []
    for _ in range(3):
        clock[0] += f._retry_best_wait + 1
        waits.append(f._retry_best_wait)
        for _ in range(4):  # the retry, 3 failures, back to legacy
            try:
                f.get("https://www.amazon.in/dp/B0X")
            except fetcher_mod.FetchError:
                pass
        assert f.profile == "legacy"
    assert waits == [3600.0, 7200.0, 14400.0]
    answers["curl"] = 200  # it works again: back to hourly
    clock[0] += f._retry_best_wait + 1
    f.get("https://www.amazon.in/dp/B0X")
    assert f.profile == "curl" and f._retry_best_wait == fetcher_mod.RETRY_BEST_SECONDS
