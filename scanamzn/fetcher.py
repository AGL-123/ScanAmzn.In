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

from .browser import FULL_NAMES, BrowserSession, BrowserStopped, find_browser
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
    # (the curl program: HTTP/2 and its own TLS) or "browser" (a real Chromium:
    # "browser" prefers the light headless shell, "chromium" is the full one).
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
    Profile("browser", transport="browser", about="a real Chromium browser engine on the phone (ps5 browser)"),
    Profile("chromium", transport="browser", about="the full Chromium browser, if the light one is stopped"),
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
# After a browser failed or got a bot check, it rescues pages again only after
# this (doubling, up to 30 min).
RESCUE_PAUSE_SECONDS = 60.0
# On a style other than the best-ranked one, try the best one again after this
# (Amazon's mood changes; a page it blocks is read by the browser anyway).
RETRY_BEST_SECONDS = 3600.0
# Failures that every way of asking would get (no network): not a reason to
# change the request style.
_NETWORK_ERRORS = ("Failed to fetch", "net::ERR_", "could not load the page", "did not load within",
                   "ConnectionError", "ConnectTimeout", "ReadTimeout", "NameResolution", "Network is unreachable",
                   "Temporary failure in name resolution", "curl exit 6:", "curl exit 7:", "curl exit 28:")

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
        # The browser styles' programs: the configured one (else the headless
        # shell, else a full Chromium) and, if it's a different program, a full Chromium.
        self.browser_paths: dict[str, str] = {}
        first = find_browser(browser_path)
        full = find_browser("", FULL_NAMES)
        if first:
            self.browser_paths["browser"] = first
        if full and full != first:
            self.browser_paths["chromium"] = full
        self.browser_path = first
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
        self._switched_at = self.clock()
        # A browser reads a page the current style can't get past a bot
        # check (it runs Amazon's challenge like any visitor's browser).
        self.rescue = rescue and any(self._usable[n].transport == "browser" for n in self.profiles)
        self._rescue_failures: dict[str, int] = {}
        self._rescue_paused_until: dict[str, float] = {}
        self._rescuer: str | None = None
        log.debug("request styles: %s; starting with %s", ", ".join(self.profiles), self.profile)

    def _installed(self, p: Profile) -> bool:
        if p.transport == "curl_cffi":
            return cffi_requests is not None
        if p.transport == "curl":
            return self.curl_path is not None
        if p.transport == "browser":
            return p.name in self.browser_paths
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

    @property
    def has_browser(self) -> bool:
        return bool(self._browsers())

    def is_browser(self, name: str) -> bool:
        return name in self._usable and self._usable[name].transport == "browser"

    def _browsers(self) -> list[str]:
        """The browser styles, in the current order."""
        return [n for n in self.profiles if self._usable[n].transport == "browser"]

    def _paused(self, name: str) -> bool:
        return self.clock() < self._rescue_paused_until.get(name, 0.0)

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
            budget = self.budget
            session = BrowserSession(
                self.browser_paths[p.name], self.domain, self.browser_dir / p.name, proxy=self.proxy,
                log_path=self.browser_dir / f"{p.name}.log", wait=self.wait,
                # its extra load (a second try at Amazon's check) counts like any request
                pace=(lambda: budget.acquire(self.wait)) if budget is not None else None,
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

    def _retry_best_if_due(self) -> None:
        """After an hour on a fallback style, try the best-ranked one again."""
        if self.profile_index == 0 or self._switch_to is not None:
            return
        if self.clock() - self._switched_at < RETRY_BEST_SECONDS:
            return
        best = self.profiles[0]
        if self.is_browser(best) and self._paused(best):
            return
        log.info("trying the best-ranked request style %r again", best)
        self._switch_to = best

    def _switch_if_due(self) -> None:
        target, self._switch_to = self._switch_to, None
        if target is None or len(self.profiles) < 2:
            return
        old = self.profile
        if target == "" or target not in self.profiles:
            for _ in range(len(self.profiles) - 1):
                self.profile_index = (self.profile_index + 1) % len(self.profiles)
                if not (self.is_browser(self.profile) and self._paused(self.profile)):
                    break  # (a browser that just failed is skipped)
        else:
            self.profile_index = self.profiles.index(target)
        self._blocked_in_a_row = self._errors_in_a_row = 0
        self._switched_at = self.clock()
        # A browser stays open while a light style is used (it reads the pages
        # Amazon blocks), but only one browser runs at a time.
        if not self.is_browser(old) or self.is_browser(self.profile):
            self._close_session(old)
        if target and target == self.profiles[0] and self.profile_index == 0:
            log.info("using request style %r", self.profile)
        else:
            log.info("request style %r keeps getting bot checks or failing; using %r next", old, self.profile)

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
        except BrowserStopped as exc:
            raise Stopped("stopped") from exc
        except Exception as exc:  # every transport raises its own exception types
            self.stats[p.name]["errors"] += 1
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        page = Page(url=str(resp.url), status_code=resp.status_code, text=resp.text, profile=p.name)
        self.stats[p.name]["blocked" if page.blocked else "ok"] += 1
        return page

    def get(self, url: str) -> Page:
        self._apply_ranking()
        self._retry_best_if_due()
        self._switch_if_due()
        p = self.current
        try:
            page = self._get_with(p, url)
        except Stopped:
            raise
        except FetchError as exc:
            if not any(marker in str(exc) for marker in _NETWORK_ERRORS):  # no network: all styles fail
                self._errors_in_a_row += 1
            if self._errors_in_a_row >= ERRORS_BEFORE_SWITCH:
                self._switch_to = ""
            if p.transport != "browser":  # the browser restarts itself when it crashed or hung
                self._close_session(p.name)
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
            # When a browser keeps getting the pages, go straight to it.
            self._switch_to = self._rescuer if rescued is not None else ""
        return rescued or page

    def _rescue(self, failed: Profile, url: str) -> Page | None:
        """The page through a browser, which runs Amazon's challenge itself -
        so a bot check doesn't cost a check. The browser styles are tried in
        order (the light one, then the full Chromium); None if none gets it."""
        if not self.rescue:
            return None
        for name in self._browsers():
            if name == failed.name or self._paused(name):
                continue
            if self.wait(0):  # the bot is stopping: don't start a browser now
                raise Stopped("stopped")
            log.info("Amazon showed a bot check to request style %r; reading the page with %r", failed.name, name)
            try:
                page = self._get_with(self._usable[name], url)
            except Stopped:
                raise
            except FetchError as exc:
                self._rest(name, f"could not read it ({exc})")
                continue
            if page.blocked:
                self._rest(name, "got a bot check too")
                if page.status_code == 405 or "captcha" in page.text[:50000].lower():
                    break  # a CAPTCHA: the other browser would only get one too
                continue
            self._rescue_failures[name] = 0
            self._rescuer = name
            return page
        return None

    def _rest(self, name: str, why: str) -> None:
        """A browser that failed or got a bot check: closed, and left alone for
        a while (1, 2, 4 ... 30 min) - also when switching styles."""
        failures = self._rescue_failures.get(name, 0)
        pause = min(RESCUE_PAUSE_SECONDS * 2 ** failures, 1800.0)
        self._rescue_failures[name] = failures + 1
        self._rescue_paused_until[name] = self.clock() + pause
        if name != self.profile:
            self._close_session(name)
        log.warning("%r %s; trying it again in %.0f min", name, why, pause / 60)
