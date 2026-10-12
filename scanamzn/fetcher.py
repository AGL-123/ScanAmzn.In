"""HTTP access to Amazon.in.

Amazon fingerprints the TLS handshake as well as headers, so plain ``requests``
gets far more captchas than a real browser. When ``curl_cffi`` is installed we
use it to impersonate Chrome's TLS/HTTP2 fingerprint; ``requests`` is the
fallback.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus

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
        log.debug("HTTP backend: %s", backend)

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
            session.headers.update(chrome_headers(self._rotation))
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=self.domain.removeprefix("www"))
        # No warm-up visit to the home page: Amazon now answers it with an AWS
        # JavaScript challenge that only a real browser can pass, while product
        # and search pages are served normally. The first real page sets the
        # session cookies, as for anyone arriving from a search engine.
        return session

    def reset(self) -> None:
        """Drop cookies and switch to a different browser fingerprint."""
        self._rotation += random.randint(1, 3)
        self.close()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
        self._session = None

    def get(self, url: str) -> Page:
        if self._session is None:
            self._session = self._new_session()
        try:
            resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
        except Exception as exc:  # both backends raise their own exception types
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        return Page(url=str(resp.url), status_code=resp.status_code, text=resp.text)
