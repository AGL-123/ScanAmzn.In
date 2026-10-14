"""HTTP access to Amazon.in.

Amazon answers some requests with a bot check (an AWS WAF JavaScript
challenge) instead of the page. Which requests depends on the network, the
pace and how the requests look, so the fetcher has several request styles
(PROFILES) and moves to the next one when Amazon keeps stopping the current
one. When a real Chromium browser is installed on the phone, a page that gets
a bot check is read with it at once: the browser runs Amazon's challenge the
way it does for any visitor, so the check isn't lost.
"""

from __future__ import annotations

import logging
import random
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote_plus

from .browser import BrowserSession, find_browser
from .curl import CurlSession
from .parser import is_blocked

if TYPE_CHECKING:
    from .budget import RequestBudget

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - depends on the environment
    cffi_requests = None

import requests

# Rotated after a captcha so the next session looks like a different browser.
_IMPERSONATE = ("chrome", "chrome136", "chrome131", "edge101", "safari184")
# (user-agent platform, sec-ch-ua-platform) of desktop Chrome.
_PLATFORMS = (
    ("Windows NT 10.0; Win64; x64", "Windows"),
    ("Macintosh; Intel Mac OS X 10_15_7", "macOS"),
    ("X11; Linux x86_64", "Linux"),
)
# Chrome ships a new major version about every four weeks. A browser claiming
# a version from years ago is itself a bot signal, so derive a current one.
_CHROME_RELEASE = (140, date(2025, 9, 2))


def chrome_major(today: date | None = None) -> int:
    """Chrome's current major version, estimated about six weeks behind the
    release schedule (updates roll out gradually; never claim a future one)."""
    version, released = _CHROME_RELEASE
    days = ((today or date.today()) - released).days
    return version + max(days - 42, 0) // 28


def _sec_ch_ua(major: int) -> str:
    """The sec-ch-ua brand list exactly as Chrome builds it: a decoy brand and
    the order of the three entries both depend on the major version."""
    chars = " (:-./);=?_"
    decoy = (f"Not{chars[major % 11]}A{chars[(major + 1) % 11]}Brand", ("8", "99", "24")[major % 3])
    order = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))[major % 6]
    brands: list[tuple[str, str]] = [("", "")] * 3
    for slot, brand in zip(order, (decoy, ("Chromium", str(major)), ("Google Chrome", str(major)))):
        brands[slot] = brand
    return ", ".join(f'"{name}";v="{v}"' for name, v in brands)


def chrome_headers(rotation: int, today: date | None = None) -> dict[str, str]:
    """User agent plus the client hints real Chrome sends with it."""
    ua_platform, hint_platform = _PLATFORMS[rotation % len(_PLATFORMS)]
    major = chrome_major(today)
    return {
        "User-Agent": f"Mozilla/5.0 ({ua_platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{major}.0.0.0 Safari/537.36",
        "sec-ch-ua": _sec_ch_ua(major),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{hint_platform}"',
    }


# How the bot's requests look. Amazon's answer depends on the network and
# changes over time, so the bot tries them in this order: after two bot checks
# in a row it moves to the next one, and the one that last worked is
# remembered (also across restarts). `ps5 probe` measures them all on the
# phone and puts them in the order that works there (request-ranking).
@dataclass(frozen=True)
class Profile:
    name: str
    # "requests" (Python), "curl_cffi" (Chrome's TLS, after `ps5 boost`), "curl"
    # (the curl program: HTTP/2 and its own TLS) or "browser" (a real Chromium).
    transport: str = "requests"
    # "legacy": the Chrome 140 user agent that ran for hours; "current": today's Chrome.
    user_agent: str = "legacy"
    hints: bool = False  # Chrome's client hints (sec-ch-ua...)
    warm_up: bool = False  # a first visit to the home page and a short pause
    http2: bool = False
    about: str = ""


PROFILES = (
    Profile("chrome-tls", transport="curl_cffi", about="Chrome's network fingerprint (ps5 boost)"),
    # On the user's home connection in October 2026 this one ran for 6 hours
    # (3-5 requests a minute) without a bot check after the first request.
    Profile("legacy", warm_up=True, about="desktop Chrome 140, home page first"),
    Profile("browser", transport="browser", about="a real Chromium browser on the phone (ps5 browser)"),
    Profile("curl", transport="curl", warm_up=True, http2=True, about="the curl program over HTTP/2"),
    Profile("plain", user_agent="current", about="today's desktop Chrome"),
    Profile("client-hints", user_agent="current", hints=True, about="today's desktop Chrome with client hints"),
)
PROFILE_NAMES = tuple(p.name for p in PROFILES)
TRANSPORTS = ("requests", "curl_cffi", "curl", "browser")
_LEGACY_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
)
BLOCKS_BEFORE_SWITCH = 2  # the first request of a fresh session is often challenged anyway
ERRORS_BEFORE_SWITCH = 3  # e.g. curl without HTTP/2, or a browser that keeps crashing
# After the browser failed, it rescues pages again only after this (doubling, up to 30 min).
RESCUE_PAUSE_SECONDS = 60.0

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class FetchError(Exception):
    pass


class Stopped(FetchError):
    """The bot is stopping (while waiting for the request budget)."""


@dataclass
class Page:
    url: str
    status_code: int
    text: str
    profile: str = ""  # the request style that loaded it

    @property
    def blocked(self) -> bool:
        return is_blocked(self.text, self.status_code)


class Fetcher:
    def __init__(
        self,
        domain: str = "www.amazon.in",
        timeout: float = 20.0,
        backend: str = "auto",
        proxy: str = "",
        budget: RequestBudget | None = None,
        profile_file: str | Path | None = None,
        ranking_file: str | Path | None = None,
        browser_path: str = "",
        browser_dir: str | Path | None = None,
        rescue: bool = True,
        only: str | None = None,
    ):
        self.domain = domain
        self.timeout = timeout
        self.proxy = proxy or None
        if backend != "auto" and backend not in TRANSPORTS:
            raise FetchError(f"unknown http_backend {backend!r}")
        self.curl_path = shutil.which("curl")
        self.browser_path = find_browser(browser_path)
        self.browser_dir = Path(browser_dir) if browser_dir else Path(tempfile.gettempdir()) / "scanamzn-browser"
        usable = [p for p in PROFILES if (backend == "auto" or p.transport == backend) and self._installed(p)]
        if only is not None:
            usable = [p for p in usable if p.name == only]
        if not usable:
            what = only or backend
            raise FetchError(f"request style {what!r} is not available on this device (not installed?)")
        self._usable = {p.name: p for p in usable}
        self.profiles: tuple[str, ...] = tuple(p.name for p in usable)
        # Random start, so every run doesn't open with the same fingerprint.
        self._rotation = random.randrange(len(_IMPERSONATE) * len(_PLATFORMS))
        self._sessions: dict[str, Any] = {}
        # At most so many requests a minute, shared with the other bot processes.
        self.budget = budget
        # Waits for the budget; the monitor swaps in its stop event's wait, so
        # stopping isn't held up (a true result means: give up).
        self.wait: Callable[[float], Any] = time.sleep
        self.clock: Callable[[], float] = time.monotonic
        self.profile_file = Path(profile_file) if profile_file else None
        self.ranking_file = Path(ranking_file) if ranking_file else None
        self._ranking_mtime: float | None = None
        self._apply_ranking(initial=True)
        self._remembered = self._read_profile()
        self.profile_index = self.profiles.index(self._remembered) if self._remembered in self.profiles else 0
        self.stats = {p: {"ok": 0, "blocked": 0, "errors": 0} for p in self.profiles}
        self._blocked_in_a_row = 0
        self._errors_in_a_row = 0
        self._switch_to: str | None = None  # after the next reset() or before the next get()
        # The browser reads a page the current style can't get past a bot
        # check (it runs Amazon's challenge like any visitor's browser).
        self.rescue = rescue and "browser" in self._usable
        self._rescue_failures = 0
        self._rescue_paused_until = 0.0
        log.debug("request styles: %s; starting with %s", ", ".join(self.profiles), self.profile)

    def _installed(self, p: Profile) -> bool:
        if p.transport == "curl_cffi":
            return cffi_requests is not None
        if p.transport == "curl":
            return self.curl_path is not None
        if p.transport == "browser":
            return self.browser_path is not None
        return True

    @property
    def profile(self) -> str:
        return self.profiles[self.profile_index]

    @property
    def current(self) -> Profile:
        return self._usable[self.profile]

    @property
    def backend(self) -> str:
        return self.current.transport

    # -- remembered style and the connection test's ranking ---------------------
    def _read_profile(self) -> str | None:
        try:
            return self.profile_file.read_text(encoding="ascii").strip() if self.profile_file else None
        except OSError:
            return None

    def _remember(self) -> None:
        self._remembered = self.profile
        if self.profile_file is None:
            return
        try:
            self.profile_file.parent.mkdir(parents=True, exist_ok=True)
            self.profile_file.write_text(self.profile + "\n", encoding="ascii")
        except OSError as exc:
            log.debug("could not save the request profile: %s", exc)

    def _apply_ranking(self, initial: bool = False) -> None:
        """Use the order `ps5 probe` found best (re-read whenever it changes)."""
        if self.ranking_file is None:
            return
        try:
            mtime = self.ranking_file.stat().st_mtime
        except OSError:
            mtime = None
        if mtime == self._ranking_mtime:
            return
        self._ranking_mtime = mtime
        if mtime is None:
            return
        try:
            names = self.ranking_file.read_text(encoding="ascii").split()
        except (OSError, UnicodeDecodeError):
            return
        ranked = [n for n in dict.fromkeys(names) if n in self._usable]
        if not ranked:
            return
        self.profiles = tuple(ranked + [p for p in self.profiles if p not in ranked])
        if not initial:
            self.close()
            self.profile_index = 0
            self._blocked_in_a_row = self._errors_in_a_row = 0
            self._switch_to = None
            log.info("using the connection test's order of request styles: %s", ", ".join(self.profiles))

    # -- URLs ---------------------------------------------------------------
    def product_url(self, asin: str) -> str:
        return f"https://{self.domain}/dp/{asin}"

    def search_url(self, query: str) -> str:
        if query.startswith(("http://", "https://")):
            return query
        return f"https://{self.domain}/s?k={quote_plus(query)}"

    def cart_url(self, asin: str) -> str:
        return f"https://{self.domain}/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1"

    # -- sessions -----------------------------------------------------------
    def _new_session(self, p: Profile):
        cookies = {"i18n-prefs": "INR", "lc-acbin": "en_IN"}
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        if p.transport == "curl_cffi":
            target = _IMPERSONATE[self._rotation % len(_IMPERSONATE)]
            session = cffi_requests.Session(impersonate=target, proxies=proxies)
            session.headers.update({"Accept-Language": _BROWSER_HEADERS["Accept-Language"]})
            log.debug("new curl_cffi session impersonating %s", target)
        elif p.transport == "browser":
            session = BrowserSession(
                self.browser_path or "chromium", self.domain, self.browser_dir, proxy=self.proxy,
                log_path=self.browser_dir.with_name(self.browser_dir.name + ".log"), wait=self.wait,
            )
        else:
            if p.transport == "curl":
                session = CurlSession(self.curl_path or "curl", http2=p.http2, proxy=self.proxy)
            else:
                session = requests.Session()
                if proxies:
                    session.proxies.update(proxies)
            session.headers.update(_BROWSER_HEADERS)
            if p.user_agent == "legacy":
                session.headers["User-Agent"] = _LEGACY_USER_AGENTS[self._rotation % len(_LEGACY_USER_AGENTS)]
            elif p.hints:
                session.headers.update(chrome_headers(self._rotation))
            else:
                session.headers["User-Agent"] = chrome_headers(self._rotation)["User-Agent"]
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=self.domain.removeprefix("www"))
        if p.warm_up:
            self._warm_up(session)
        return session

    def _warm_up(self, session) -> None:
        """A first visit to the home page and short pause. Amazon answers that
        page with a JavaScript challenge, but after it the product pages loaded
        for hours (the "legacy" style)."""
        if self.budget is not None and not self.budget.acquire(self.wait):
            raise Stopped("stopped while pacing requests")
        try:
            session.get(f"https://{self.domain}/", timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            log.debug("warm-up request failed: %s", exc)
        self.wait(random.uniform(2.0, 4.0))

    def reset(self) -> None:
        """After a bot check: a fresh session (new cookies); after
        BLOCKS_BEFORE_SWITCH of them in a row, also the next request style."""
        self._rotation += random.randint(1, 3)
        self._close_session(self.profile)
        self._switch_if_due()

    def _switch_if_due(self) -> None:
        target, self._switch_to = self._switch_to, None
        if target is None or len(self.profiles) < 2:
            return
        old = self.profile
        if target == "" or target not in self.profiles:
            self.profile_index = (self.profile_index + 1) % len(self.profiles)
            if self.profile == "browser" and self.clock() < self._rescue_paused_until and len(self.profiles) > 2:
                self.profile_index = (self.profile_index + 1) % len(self.profiles)  # it just failed
        else:
            self.profile_index = self.profiles.index(target)
        self._blocked_in_a_row = self._errors_in_a_row = 0
        if old != "browser":
            self._close_session(old)
        log.info("Amazon keeps stopping request style %r; using %r next", old, self.profile)

    def _close_session(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        for name in list(self._sessions):
            self._close_session(name)

    # -- requests -----------------------------------------------------------
    def _get_with(self, p: Profile, url: str) -> Page:
        if self.budget is not None and not self.budget.acquire(self.wait):
            raise Stopped("stopped while pacing requests")
        session = self._sessions.get(p.name)
        try:
            if session is None:
                session = self._sessions[p.name] = self._new_session(p)
            resp = session.get(url, timeout=self.timeout, allow_redirects=True)
        except Stopped:
            raise
        except Exception as exc:  # every transport raises its own exception types
            self.stats[p.name]["errors"] += 1
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        page = Page(url=str(resp.url), status_code=resp.status_code, text=resp.text, profile=p.name)
        self.stats[p.name]["blocked" if page.blocked else "ok"] += 1
        return page

    def get(self, url: str) -> Page:
        self._apply_ranking()
        self._switch_if_due()
        p = self.current
        try:
            page = self._get_with(p, url)
        except Stopped:
            raise
        except FetchError:
            self._errors_in_a_row += 1
            if self._errors_in_a_row >= ERRORS_BEFORE_SWITCH:
                self._switch_to = ""
            self._close_session(p.name)  # e.g. a browser that crashed starts again
            raise
        self._errors_in_a_row = 0
        if not page.blocked:
            self._blocked_in_a_row = 0
            if self.profile != self._remembered:
                self._remember()
            return page
        self._blocked_in_a_row += 1
        rescued = self._rescue(p, url)
        if self._blocked_in_a_row >= BLOCKS_BEFORE_SWITCH:
            # When the browser keeps getting the pages, go straight to it.
            self._switch_to = "browser" if rescued is not None else ""
        return rescued or page

    def _rescue(self, failed: Profile, url: str) -> Page | None:
        """The page through the browser, which runs Amazon's challenge itself
        - so a bot check doesn't cost a check. None if that doesn't work."""
        if not self.rescue or failed.name == "browser" or self.clock() < self._rescue_paused_until:
            return None
        log.info("Amazon showed a bot check to request style %r; reading the page with the browser", failed.name)
        try:
            page = self._get_with(self._usable["browser"], url)
        except Stopped:
            raise
        except FetchError as exc:
            self._close_session("browser")
            pause = min(RESCUE_PAUSE_SECONDS * 2 ** self._rescue_failures, 1800.0)
            self._rescue_failures += 1
            self._rescue_paused_until = self.clock() + pause
            log.warning("the browser could not read it either (%s); trying it again in %.0f min", exc, pause / 60)
            return None
        self._rescue_failures = 0
        if page.blocked:
            log.warning("the browser got a bot check too")
            return None
        return page
