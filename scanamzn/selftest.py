"""Self-test against the live Amazon.in site.

PlayStation gift cards are almost always out of stock, so normal running
never proves that the bot would recognise an *in-stock* page. The positive
control below does: it takes an everyday product that the search shows with a
price (so it is surely buyable) and checks that its product page is read as
in stock. The monitor repeats this every few hours; `scanamzn selftest` runs
it together with the other checks and gives a verdict:

* pass - every check passed (warnings may be listed), including the control;
* fail - something was read wrongly (pages are saved for analysis);
* inconclusive - Amazon blocked or the network failed before the in-stock
  control could be completed, so nothing is proven either way.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fetcher import FetchError, Fetcher, Page
from .models import Product, Status
from .parser import delivery_location, is_blocked, parse_product, parse_search

CONTROL_QUERIES = ("usb c cable", "a4 notebook", "ballpoint pen")
CONTROL_CHECK = "In-stock detection"
BLOCK_RETRY_WAIT = 60  # seconds before retrying with a fresh session
MAX_PAGES = 20  # gift card pages opened by the self-test
OTHER_VALUES = 3  # of the values the monitor checks in turn, picked at random each run
PAUSE_SECONDS = 600  # the monitor's pause during a self-test, at most
SAVE_FIRST_PAGES = 3  # always saved; later ones only when not plainly "unavailable"

# pause() sleeps between requests and returns True when the caller wants to stop.
Pause = Callable[[], bool]
Dump = Callable[[str, str], None]


def _inr(amount: float | None) -> str:
    if amount is None:
        return "no price"
    return f"₹{amount:,.0f}" if float(amount).is_integer() else f"₹{amount:,.2f}"


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class ControlResult:
    # True: an in-stock product was recognised; False: it wasn't;
    # None: inconclusive (captcha, network/server error, stopped).
    ok: bool | None
    detail: str
    product: Product | None = None


def in_stock_control(
    fetcher: Fetcher, queries: tuple[str, ...] | list[str], pause: Pause, dump: Dump | None = None
) -> ControlResult:
    """Find a product the search shows with a price and expect its page to read as in stock."""
    misread: list[Product] = []
    for query in queries:
        if pause():
            return ControlResult(None, "stopped")
        try:
            page = fetcher.get(fetcher.search_url(query))
        except FetchError as exc:
            return ControlResult(None, f"search failed: {exc}")
        result = parse_search(page.text, page.status_code)
        if result.blocked:
            return ControlResult(None, "Amazon asked for a bot check")
        candidates = [p for p in result.products if p.status is Status.AVAILABLE and p.price][:2]
        if not candidates and dump:
            dump(f"control_search_{query}", page.text)
        for candidate in candidates:
            if pause():
                return ControlResult(None, "stopped")
            try:
                product_page = fetcher.get(fetcher.product_url(candidate.asin))
            except FetchError as exc:
                return ControlResult(None, f"product page failed: {exc}")
            product = parse_product(product_page.text, candidate.asin, product_page.status_code)
            if product.status is Status.BLOCKED:
                return ControlResult(None, "Amazon asked for a bot check")
            if product.status is Status.ERROR:  # Amazon's server hiccup, not a misread
                return ControlResult(None, f"product page failed: {product.reason}")
            if product.status is Status.AVAILABLE:
                return ControlResult(
                    True,
                    f"'{_short(product.title or candidate.title)}' read as IN STOCK at "
                    f"{_inr(product.price or candidate.price)} ({product.reason})",
                    product,
                )
            misread.append(product)
            if dump:
                dump(f"control_{candidate.asin}", product_page.text)
        if misread:
            break  # pages were found but misread; another query won't change that
    if not misread:
        return ControlResult(False, "no product with a price was recognised in the search results")
    last = misread[-1]
    return ControlResult(
        False,
        f"{len(misread)} product(s) that search showed with a price were read as "
        f"'{last.status}' on their own page ({last.reason})",
        last,
    )


class RetryingFetcher:
    """Wraps a Fetcher: when Amazon answers with a bot check, wait, start a fresh
    session and try again (a limited number of times for the whole test).
    Pages that loaded are kept, so a page is not requested twice in one test."""

    def __init__(self, fetcher: Fetcher, wait: Callable[[float], None], progress: Callable[[str], None],
                 retries: int = 2):
        self.fetcher, self.wait, self.progress, self.retries = fetcher, wait, progress, retries
        self.backend = fetcher.backend
        self.product_url, self.search_url = fetcher.product_url, fetcher.search_url
        self.blocks = 0
        self._loaded: dict[str, Page] = {}

    def get(self, url: str) -> Page:
        if url in self._loaded:
            return self._loaded[url]
        while True:
            page = self.fetcher.get(url)
            if not is_blocked(page.text, page.status_code):
                self._loaded[url] = page
                return page
            self.blocks += 1
            if self.retries <= 0:
                return page
            self.retries -= 1
            self.progress(f"Amazon asked for a bot check; trying again with a fresh session in {BLOCK_RETRY_WAIT} s...")
            self.wait(BLOCK_RETRY_WAIT)
            self.fetcher.reset()


@dataclass
class Check:
    name: str
    ok: bool | None  # None = warning / inconclusive
    detail: str
    lines: list[str] = field(default_factory=list)

    @property
    def icon(self) -> str:
        return {True: "✅", False: "❌", None: "⚠️"}[self.ok]


@dataclass
class SelfTest:
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(c.ok is False for c in self.checks):
            return "fail"
        control = next((c for c in self.checks if c.name == CONTROL_CHECK), None)
        if control is None or control.ok is not True:
            return "inconclusive"
        return "pass"

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def summary(self) -> str:
        lines = ["PS5 gift card monitor - self-test"]
        for c in self.checks:
            lines.append(f"{c.icon} {c.name}: {c.detail}")
            lines.extend(f"     {line}" for line in c.lines)
        lines.append("")
        if self.verdict == "fail":
            lines.append("RESULT: ❌ something is read wrongly - see the ❌ lines. "
                         "Run `ps5 export` and send the files so it can be fixed.")
        elif self.verdict == "inconclusive":
            lines.append("RESULT: ⚠️ not conclusive - Amazon blocked part of the test or the connection failed. "
                         "Try PS5 Check now again in a few minutes.")
        elif any(c.ok is None for c in self.checks):
            lines.append("RESULT: ✅ in-stock detection works on the live site; see the ⚠️ lines for details.")
        else:
            lines.append("RESULT: ✅ the bot reads Amazon.in correctly and would see a restock.")
        return "\n".join(lines)


def first_url(settings, fetcher: Fetcher) -> str:
    """The page that proves Amazon.in answers: one the monitor really reads.
    (Not the home page - Amazon shows it only to browsers that run JavaScript.)"""
    if settings.asins:
        return fetcher.product_url(settings.asins[0])
    return fetcher.search_url(settings.search_queries[0])  # config requires one or the other


def run(settings, fetcher: Fetcher, pause: Pause, dump: Dump, progress: Callable[[str], None],
        wait: Callable[[float], None] | None = None) -> SelfTest:
    """All checks in order. Search and gift card pages are always saved via
    ``dump`` (they are what's needed to fix page reading); control pages only
    when misread."""
    test = SelfTest()
    site = RetryingFetcher(fetcher, wait or (lambda s: None), progress)

    progress("Checking that Amazon.in can be reached...")
    try:
        first = site.get(first_url(settings, fetcher))
    except FetchError as exc:
        # No internet proves nothing about page reading: not conclusive.
        test.checks.append(Check("Amazon.in reachable", None, f"no connection - is the internet on? ({exc})"))
        return test
    if is_blocked(first.text, first.status_code):
        dump("blocked_page", first.text)
        test.checks.append(Check(
            "Amazon.in reachable", None,
            f"Amazon keeps asking for a bot check (HTTP {first.status_code}); the monitor waits and retries "
            "by itself. If this happens often, try `ps5 boost`.",
        ))
        return test
    where = delivery_location(first.text) or "not shown"
    test.checks.append(Check(
        "Amazon.in reachable", True,
        f"via {fetcher.backend}; Amazon's delivery location: {where}"
        + (f" (needed {site.blocks} retry after a bot check)" if site.blocks else ""),
    ))

    progress("Running the gift card searches...")
    found: dict[str, Product] = {}
    results, others = 0, []
    misunderstood, blocked_searches, failed_searches = [], [], []
    for query in settings.search_queries:
        if pause():
            break
        try:
            page = site.get(fetcher.search_url(query))
        except FetchError as exc:
            failed_searches.append(f"'{query}': {exc}")
            continue
        dump(f"search_{query}", page.text)
        parsed = parse_search(page.text, page.status_code)
        if parsed.blocked:
            blocked_searches.append(query)
            continue
        if not parsed.layout_ok:
            misunderstood.append(query)
            continue
        results += len(parsed.products)
        for p in parsed.products:
            if settings.title_filter.matches(p.title):
                found.setdefault(p.asin, p)
            else:
                others.append(p.title)
    lines = [f"{p.asin}  {p.status:<11} {_inr(p.price):>9}  {_short(p.title)}" for p in found.values()]
    lines += [f"not understood: '{q}' (saved)" for q in misunderstood]
    lines += [f"bot check: '{q}'" for q in blocked_searches] + [f"failed: {f}" for f in failed_searches]
    if not settings.search_queries:
        test.checks.append(Check("Searches", None, "no search queries configured"))
    elif misunderstood and not results:
        test.checks.append(Check("Searches", False, "the search pages were not understood (saved)", lines))
    elif misunderstood:
        test.checks.append(Check(
            "Searches", None, f"{len(misunderstood)} of {len(settings.search_queries)} searches not understood",
            lines,
        ))
    elif not results and (blocked_searches or failed_searches):
        test.checks.append(Check("Searches", None, "could not run the searches (bot check / network)", lines))
    elif not found:
        sample = "; ".join(_short(t, 40) for t in others[:3])
        test.checks.append(Check(
            "Searches", None,
            f"{results} results, none look like a PlayStation gift card right now"
            " (Amazon hides out-of-stock items from search)"
            + (f" - e.g. {sample}" if sample else ""),
            lines,
        ))
    else:
        test.checks.append(Check(
            "Searches", None if blocked_searches or failed_searches else True,
            f"{results} results, {len(found)} PlayStation gift card listing(s):", lines,
        ))

    progress("Opening the gift card pages...")
    # The listings checked every round, any the searches found, and a few of
    # those checked in turn (different ones each run: opening all of them at
    # once is the kind of burst Amazon answers with bot checks). Saving them
    # all would make `ps5 export` large: keep the first few (to compare page
    # layouts) and every page that is not a plain "unavailable".
    others = random.sample(settings.rotating_asins, min(OTHER_VALUES, len(settings.rotating_asins)))
    asins = list(dict.fromkeys(settings.asins + list(found) + others))[:MAX_PAGES]
    page_lines, unreadable, inconclusive, mismatched, in_stock = [], 0, 0, 0, []
    for i, asin in enumerate(asins):
        if pause():
            break
        try:
            page = site.get(fetcher.product_url(asin))
        except FetchError as exc:
            page_lines.append(f"{asin}  could not load: {exc}")
            inconclusive += 1
            continue
        product = parse_product(page.text, asin, page.status_code)
        listed = found.get(asin)
        mismatch = bool(listed and listed.status is Status.AVAILABLE and listed.price
                        and product.status is Status.UNAVAILABLE)
        if i < SAVE_FIRST_PAGES or mismatch or product.status not in (Status.UNAVAILABLE, Status.BLOCKED):
            dump(f"product_{asin}", page.text)
        if product.status in (Status.BLOCKED, Status.ERROR):
            page_lines.append(f"{asin}  {'bot check' if product.status is Status.BLOCKED else product.reason}")
            inconclusive += 1
            continue
        if product.status is Status.UNKNOWN:
            unreadable += 1
            page_lines.append(f"{asin}  NOT UNDERSTOOD (saved): {_short(product.title, 45)}")
            continue
        if product.status is Status.AVAILABLE:
            in_stock.append(asin)
        if mismatch:
            mismatched += 1
            page_lines.append(
                f"{asin}  search shows it in stock at {_inr(listed.price)}, but its page reads unavailable (saved)"
            )
            continue
        if product.status is Status.NOT_FOUND:
            page_lines.append(f"{asin}  page not found (Amazon removed this listing?)")
            continue
        page_lines.append(f"{asin}  {product.status}: {_short(product.title or (listed and listed.title) or '', 45)}")
    if not asins:
        test.checks.append(Check("Gift card pages", None, "no listings to open (none found, no ASINs set)"))
    elif unreadable:
        test.checks.append(Check(
            "Gift card pages", False, f"{unreadable} of {len(asins)} page(s) not understood", page_lines,
        ))
    elif inconclusive or mismatched:
        test.checks.append(Check(
            "Gift card pages", None,
            f"{len(asins) - inconclusive - mismatched} of {len(asins)} page(s) understood; "
            f"{inconclusive} could not be loaded, {mismatched} disagree with the search",
            page_lines,
        ))
    else:
        note = f" - {len(in_stock)} IN STOCK NOW!" if in_stock else ""
        test.checks.append(Check("Gift card pages", True, f"{len(asins)} page(s) understood{note}", page_lines))

    progress("Checking that an in-stock product is recognised...")
    control = in_stock_control(site, settings.control_queries, pause, dump)
    test.checks.append(Check(CONTROL_CHECK, control.ok, control.detail))
    return test


def state_dir(settings) -> Path:
    """Where the self-test report and saved pages go (next to the status file)."""
    if settings.status_file:
        return Path(settings.status_file).expanduser().parent
    return Path.home() / ".scanamzn"


def pause_file(settings) -> Path:
    """While it exists (and its time hasn't passed), the running monitor waits:
    the self-test's own requests plus the monitor's would be a burst."""
    return state_dir(settings) / "paused-until"


def pause_monitor(settings, seconds: float) -> None:
    import time

    try:
        path = pause_file(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{time.time() + seconds:.0f}\n", encoding="ascii")
    except OSError:
        pass  # the request budget still keeps the two apart


def resume_monitor(settings) -> None:
    try:
        pause_file(settings).unlink(missing_ok=True)
    except OSError:
        pass


def debug_dir(settings) -> Path:
    """Folder for pages the bot couldn't read (what `ps5 export` copies)."""
    return Path(settings.debug_dump_dir) if settings.debug_dump_dir else state_dir(settings) / "debug"
