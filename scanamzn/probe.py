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

from .fetcher import PROFILES, FetchError, Fetcher, Stopped
from .models import Status
from .parser import parse_product

log = logging.getLogger(__name__)

TRIES = 4  # per style, alternating between the watched cards
MIN_SPACING_SECONDS = 5.0
# At most: the whole test must fit in the monitor's pause (selftest.PAUSE_SECONDS).
MAX_SPACING_SECONDS = 15.0
BETWEEN_STYLES_SECONDS = 10.0
_ABOUT = {p.name: p.about for p in PROFILES}


@dataclass
class StyleResult:
    name: str
    read: int = 0  # pages understood (in stock, unavailable, ...)
    unclear: int = 0  # loaded, but not understood
    blocked: int = 0
    errors: int = 0
    seconds: list[float] = field(default_factory=list)
    last_error: str = ""
    said: str = ""  # what the last understood page said
    unavailable: str = ""  # why the style couldn't be tried at all

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
            parts.append(f"{self.errors} failed ({self.last_error[:80]})")
        if self.seconds:
            parts.append(f"{statistics.median(self.seconds):.1f} s each")
        return f"{self.icon} {self.name}: {', '.join(parts)} - {about}"


def rank(results: list[StyleResult]) -> list[str]:
    """Best first: most pages read, then fewest failures (a style that fails
    outright is likely broken here), then fewest bot checks;
    ties keep the default order. Styles that couldn't be tried go last."""
    tried = [r for r in results if not r.unavailable]
    order = sorted(tried, key=lambda r: (-r.read, r.errors, r.blocked))
    return [r.name for r in order] + [r.name for r in results if r.unavailable]


def run(urls: list[tuple[str, str]], styles: list[str], make_fetcher: Callable[[str], Fetcher],
        wait: Callable[[float], object], progress: Callable[[str], None], spacing: float,
        tries: int = TRIES, dump: Callable[[str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic) -> list[StyleResult]:
    """Try each style ``tries`` times on ``urls`` ((asin, url) pairs, used in
    turn), ``spacing`` seconds apart."""
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
                    result.errors += 1
                    result.last_error = str(exc)
                    continue
                result.seconds.append(clock() - started)
                product = parse_product(page.text, asin, page.status_code)
                if product.status is Status.BLOCKED:
                    result.blocked += 1
                elif product.status is Status.UNKNOWN:
                    result.unclear += 1
                    if dump is not None:
                        dump(f"probe_{name}_{asin}", page.text)
                else:
                    result.read += 1
                    result.said = f"{asin}: {product.status}"
        finally:
            fetcher.close()
        progress(result.line())
    return results


def summary(results: list[StyleResult], ranking: list[str], adopted: bool, tries: int, spacing: float,
            browser_missing: bool) -> str:
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
    else:
        lines.append("No way got through this time, so the bot keeps its previous order. "
                     "Amazon may be checking this network harder right now: try again in an hour, "
                     "or on another network (Wi-Fi / mobile data).")
    if browser_missing:
        lines.append("")
        lines.append("Tip: `ps5 browser` adds a real browser engine (about 1 GB of space). The bot then "
                     "reads a page that gets a bot check with it right away, so no check is lost.")
    return "\n".join(lines)


def save_ranking(ranking_file: Path, profile_file: Path, ranking: list[str]) -> None:
    ranking_file.parent.mkdir(parents=True, exist_ok=True)
    ranking_file.write_text("\n".join(ranking) + "\n", encoding="ascii")
    profile_file.write_text(ranking[0] + "\n", encoding="ascii")
