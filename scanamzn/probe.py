"""`ps5 probe`: which request styles get Amazon.in's pages on this phone's network.

Amazon decides per network (and changes its mind over time) which requests
get a bot check, so instead of guessing, the probe tries every request style
the phone has on the watched cards, at the monitor's own pace, and the
monitor then uses them in the order that worked best (request-ranking in the
state folder; a running monitor picks it up by itself).
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fetcher import PROFILES, FetchError, Fetcher, Stopped, is_network_error
from .models import Status
from .parser import parse_product

log = logging.getLogger(__name__)

TRIES = 4  # per style, alternating between the watched cards
MIN_SPACING_SECONDS = 5.0
# At most, so the whole test fits in the monitor's pause (selftest.MAX_PAUSE_SECONDS).
MAX_SPACING_SECONDS = 15.0
BETWEEN_STYLES_SECONDS = 10.0
BROWSER_START_SECONDS = 10.0  # a browser's first page on a phone: start-up plus Amazon's check


def duration(styles: int, browsers: int, tries: int, spacing: float, timeout: float) -> tuple[float, float]:
    """(typical, worst) seconds for the whole test. The worst case is a
    browser that fails slowly: the test then stops trying it (``slow``)."""
    waits = styles * (tries - 1) * spacing + max(styles - 1, 0) * BETWEEN_STYLES_SECONDS
    typical = waits + styles * tries * 2 + browsers * BROWSER_START_SECONDS
    worst = waits + styles * tries * timeout + browsers * (60 + 2 * (timeout + 15))
    return typical, worst
_ABOUT = {p.name: p.about for p in PROFILES}


@dataclass
class StyleResult:
    name: str
    read: int = 0  # pages understood (in stock, unavailable, ...)
    unclear: int = 0  # loaded, but not understood
    blocked: int = 0
    errors: int = 0
    network_errors: int = 0  # of the errors: no or a slow network (any style would fail)
    seconds: list[float] = field(default_factory=list)
    last_error: str = ""
    said: str = ""  # what the last understood page said
    unavailable: str = ""  # why the style couldn't be tried at all
    cut_short: bool = False  # slow failures: the remaining tries were not made

    @property
    def tries(self) -> int:
        return self.read + self.unclear + self.blocked + self.errors

    @property
    def icon(self) -> str:
        if self.unavailable or not self.read:
            return "❌"
        return "✅" if self.read == self.tries else "⚠️"

    def line(self) -> str:
        about = _ABOUT.get(self.name, "")
        if self.unavailable:
            return f"{self.icon} {self.name}: {self.unavailable}"
        parts = [f"{self.read} of {self.tries} read"]
        if self.blocked:
            parts.append(f"{self.blocked} bot check{'s' if self.blocked > 1 else ''}")
        if self.unclear:
            parts.append(f"{self.unclear} not understood")
        if self.errors:
            parts.append(f"{self.errors} failed ({_tail(self.last_error)})")
        if self.cut_short:
            parts.append("stopped after two slow failures")
        if self.seconds:
            parts.append(f"{statistics.median(self.seconds):.1f} s each")
        return f"{self.icon} {self.name}: {', '.join(parts)} - {about}"


def _tail(text: str, limit: int = 160) -> str:
    """The end of an error message: that's where the reason is."""
    return text if len(text) <= limit else "…" + text[-limit:]


def rank(results: list[StyleResult]) -> list[str]:
    """Best first: most pages read, then fewest failures (a style that fails
    outright is likely broken here; network failures don't count, every
    style gets those), then fewest bot checks; ties keep the default order.
    Styles that couldn't be tried go last."""
    tried = [r for r in results if not r.unavailable]
    order = sorted(tried, key=lambda r: (-r.read, r.errors - r.network_errors, r.blocked))
    return [r.name for r in order] + [r.name for r in results if r.unavailable]


def run(urls: list[tuple[str, str]], styles: list[str], make_fetcher: Callable[[str], Fetcher],
        wait: Callable[[float], object], progress: Callable[[str], None], spacing: float,
        tries: int = TRIES, dump: Callable[[str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic, slow: float = 30.0,
        browsers: set[str] | frozenset[str] = frozenset()) -> list[StyleResult]:
    """Try each style ``tries`` times on ``urls`` ((asin, url) pairs, used in
    turn), ``spacing`` seconds apart. A browser style whose tries fail after
    more than ``slow`` seconds twice in a row is not tried again (so a broken
    or refused browser can't make the test outlast the monitor's pause)."""
    results: list[StyleResult] = []
    for n, name in enumerate(styles):
        result = StyleResult(name)
        results.append(result)
        if n:
            wait(BETWEEN_STYLES_SECONDS)
        try:
            fetcher = make_fetcher(name)
        except FetchError as exc:
            result.unavailable = str(exc)
            progress(result.line())
            continue
        progress(f"Trying {name} ({_ABOUT.get(name, '')})...")
        slow_in_a_row = 0
        try:
            for i in range(tries):
                if i:
                    wait(spacing)
                asin, url = urls[i % len(urls)]
                started = clock()
                try:
                    page = fetcher.get(url)
                except Stopped:
                    raise
                except FetchError as exc:
                    failed = True
                    result.errors += 1
                    result.network_errors += is_network_error(str(exc))
                    result.last_error = str(exc)
                else:
                    result.seconds.append(clock() - started)
                    product = parse_product(page.text, asin, page.status_code)
                    failed = product.status is Status.BLOCKED
                    if failed:
                        result.blocked += 1
                    elif product.status is Status.UNKNOWN:
                        result.unclear += 1
                        if dump is not None:
                            dump(f"probe_{name}_{asin}", page.text)
                    else:
                        result.read += 1
                        result.said = f"{asin}: {product.status}"
                slow_in_a_row = slow_in_a_row + 1 if failed and clock() - started > slow else 0
                if name in browsers and slow_in_a_row >= 2 and i < tries - 1:
                    result.cut_short = True
                    break
        finally:
            fetcher.close()
        progress(result.line())
    return results


def summary(results: list[StyleResult], ranking: list[str], adopted: bool, tries: int, spacing: float,
            browser_missing: bool, browsers: set[str] | frozenset[str] = frozenset()) -> str:
    lines = [
        "PS5 gift card monitor - connection test",
        f"Which ways of asking get Amazon.in's pages on this network ({tries} tries each, "
        f"{spacing:.0f} s apart, like the monitor):",
        "",
    ]
    lines += [r.line() for r in results]
    lines.append("")
    if adopted:
        lines.append(f"The bot now uses {ranking[0]} first, then: {', '.join(ranking[1:]) or 'nothing else'}.")
    failing = [r for r in results if r.name in browsers and not r.read and r.errors and not r.blocked]
    # A browser that doesn't start or keeps crashing is broken here; one that
    # only met a dead or slow network is not.
    broken = [r for r in failing if r.errors > r.network_errors]
    offline = [r for r in results if not r.unavailable and not r.read and r.errors and r.errors == r.network_errors]
    refused = [r for r in results if r.name in browsers and not r.read and r.blocked]
    if broken:
        lines.append(f"The browser engine ({', '.join(r.name for r in broken)}) did not work on this phone: "
                     f"{_tail(broken[0].last_error, 240)}")
        lines.append("Run `ps5 browser` again (it re-checks the install). If that doesn't help, run "
                     "`ps5 export` and send the files from Downloads/ScanAmzn.")
    elif offline and not adopted:
        lines.append("The connection failed or was very slow during the test "
                     f"({_tail(offline[0].last_error, 120)}). Tap PS5 Test connection again when the internet "
                     "works; the bot keeps its previous order meanwhile.")
    if not adopted and not (offline and not broken):
        if refused and not broken:
            lines.append("No way got through, not even the browser engine: Amazon is asking this network for "
                         "more than a browser check right now. The bot keeps trying by itself; checking less "
                         "often (PS5 Settings) or another network (Wi-Fi / mobile data) helps.")
        elif not broken:
            lines.append("No way got through this time, so the bot keeps its previous order. "
                         "Amazon may be checking this network harder right now: try again in an hour, "
                         "or on another network (Wi-Fi / mobile data).")
        else:
            lines.append("So the bot keeps its previous order for now.")
    if browser_missing:
        lines.append("")
        lines.append("Tip: `ps5 browser` adds a real browser engine (about 1 GB of space). The bot then "
                     "reads a page that gets a bot check with it right away, so no check is lost.")
    return "\n".join(lines)


def save_ranking(ranking_file: Path, profile_file: Path, ranking: list[str]) -> None:
    ranking_file.parent.mkdir(parents=True, exist_ok=True)
    ranking_file.write_text("\n".join(ranking) + "\n", encoding="ascii")
    profile_file.write_text(ranking[0] + "\n", encoding="ascii")
