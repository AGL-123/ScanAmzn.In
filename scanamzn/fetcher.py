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
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - depends on the environment
    cffi_requests = None

import requests

# Rotated after a captcha so the next session looks like a different browser.
_IMPERSONATE = ("chrome", "chrome136", "chrome131", "edge101", "safari184")
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36",
)
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
        self._rotation = 0
        self._session = None
        self.sleep = time.sleep
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
            session.headers["User-Agent"] = _USER_AGENTS[self._rotation % len(_USER_AGENTS)]
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=self.domain.removeprefix("www"))
        # Visit the home page first so Amazon issues its normal session cookies.
        try:
            session.get(f"https://{self.domain}/", timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            log.debug("warm-up request failed: %s", exc)
        # A person looks at the home page for a moment before searching; an
        # instant second request is a classic bot signature.
        self.sleep(random.uniform(2.0, 4.0))
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
