"""HTTP access to Amazon.in.

Amazon fingerprints the TLS handshake as well as headers, so plain ``requests``
gets far more captchas than a real browser. When ``curl_cffi`` is installed we
use it to impersonate Chrome's TLS/HTTP2 fingerprint; ``requests`` is the
fallback.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote_plus

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


# How the bot's requests look, tried in this order: after two bot checks in a
# row the next one is used, and the one that last worked is remembered (also
# across restarts). Amazon's answer depends on the network. On the user's home
# connection in October 2026, "legacy" - desktop Chrome 140, no client hints,
# a first visit to the home page - ran for 6 hours without a single bot check
# after the first request, while "client-hints" was challenged again and again.
PROFILES = ("legacy", "plain", "client-hints")
_LEGACY_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
)
BLOCKS_BEFORE_SWITCH = 2  # the first request of a fresh session is often challenged anyway

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


@dataclass
class Page:
    url: str
    status_code: int
    text: str


class Fetcher:
    def __init__(
        self,
        domain: str = "www.amazon.in",
        timeout: float = 20.0,
        backend: str = "auto",
        proxy: str = "",
        budget: RequestBudget | None = None,
        profile_file: str | Path | None = None,
    ):
        self.domain = domain
        self.timeout = timeout
        self.proxy = proxy or None
        if backend == "auto":
            backend = "curl_cffi" if cffi_requests is not None else "requests"
        if backend == "curl_cffi" and cffi_requests is None:
            raise FetchError("http_backend is 'curl_cffi' but curl_cffi is not installed")
        if backend not in ("curl_cffi", "requests"):
            raise FetchError(f"unknown http_backend {backend!r}")
        self.backend = backend
        # Random start, so every run doesn't open with the same fingerprint.
        self._rotation = random.randrange(len(_IMPERSONATE) * len(_PLATFORMS))
        self._session = None
        # At most so many requests a minute, shared with the other bot processes.
        self.budget = budget
        # Waits for the budget; the monitor swaps in its stop event's wait, so
        # stopping isn't held up (a true result means: give up).
        self.wait: Callable[[float], Any] = time.sleep
        # Request profiles (see PROFILES); curl_cffi has its own fingerprints.
        self.profiles = PROFILES if backend == "requests" else (backend,)
        self.profile_file = Path(profile_file) if profile_file else None
        self._remembered = self._read_profile()
        self.profile_index = self.profiles.index(self._remembered) if self._remembered in self.profiles else 0
        self.stats = {p: {"ok": 0, "blocked": 0} for p in self.profiles}
        self._blocked_in_a_row = 0
        log.debug("HTTP backend: %s, request profile %s", backend, self.profile)

    @property
    def profile(self) -> str:
        return self.profiles[self.profile_index]

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
    def _new_session(self):
        cookies = {"i18n-prefs": "INR", "lc-acbin": "en_IN"}
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        if self.backend == "curl_cffi":
            target = _IMPERSONATE[self._rotation % len(_IMPERSONATE)]
            session = cffi_requests.Session(impersonate=target, proxies=proxies)
            session.headers.update({"Accept-Language": _BROWSER_HEADERS["Accept-Language"]})
            log.debug("new curl_cffi session impersonating %s", target)
        else:
            session = requests.Session()
            if proxies:
                session.proxies.update(proxies)
            session.headers.update(_BROWSER_HEADERS)
            if self.profile == "legacy":
                session.headers["User-Agent"] = _LEGACY_USER_AGENTS[self._rotation % len(_LEGACY_USER_AGENTS)]
            elif self.profile == "plain":
                session.headers["User-Agent"] = chrome_headers(self._rotation)["User-Agent"]
            else:
                session.headers.update(chrome_headers(self._rotation))
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=self.domain.removeprefix("www"))
        if self.profile == "legacy":
            self._warm_up(session)
        return session

    def _warm_up(self, session) -> None:
        """The legacy profile's first visit to the home page and short pause.
        Amazon answers that page with a JavaScript challenge, but after it the
        product pages loaded for hours (the other profiles skip it)."""
        if self.budget is not None and not self.budget.acquire(self.wait):
            raise FetchError("stopped while pacing requests")
        try:
            session.get(f"https://{self.domain}/", timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            log.debug("warm-up request failed: %s", exc)
        self.wait(random.uniform(2.0, 4.0))

    def reset(self) -> None:
        """After a bot check: drop cookies; after BLOCKS_BEFORE_SWITCH of them
        in a row, also switch to the next request profile."""
        self._rotation += random.randint(1, 3)
        self._blocked_in_a_row += 1
        if self._blocked_in_a_row >= BLOCKS_BEFORE_SWITCH and len(self.profiles) > 1:
            self._blocked_in_a_row = 0
            self.profile_index = (self.profile_index + 1) % len(self.profiles)
            log.info("Amazon keeps showing bot checks; trying request style %r next", self.profile)
        self.close()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
        self._session = None

    def get(self, url: str) -> Page:
        if self.budget is not None and not self.budget.acquire(self.wait):
            raise FetchError("stopped while pacing requests")
        if self._session is None:
            self._session = self._new_session()
        try:
            resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
        except Exception as exc:  # both backends raise their own exception types
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        page = Page(url=str(resp.url), status_code=resp.status_code, text=resp.text)
        if is_blocked(page.text, page.status_code):
            self.stats[self.profile]["blocked"] += 1
        else:
            self.stats[self.profile]["ok"] += 1
            self._blocked_in_a_row = 0
            if self.profile != self._remembered:
                self._remember()
        return page
