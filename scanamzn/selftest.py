"""Self-test against the live Amazon.in site.

PlayStation gift cards are almost always out of stock, so normal running
never proves that the bot would recognise an *in-stock* page. The positive
control below does: it takes an everyday product that the search shows with a
price (so it is surely buyable) and checks that its product page is read as
in stock. The monitor repeats this every few hours; `scanamzn selftest` runs
it together with the other checks and prints a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fetcher import FetchError, Fetcher
from .models import DEFINITIVE, Product, Status
from .parser import delivery_location, is_blocked, parse_product, parse_search

CONTROL_QUERIES = ("usb c cable", "a4 notebook", "ballpoint pen")

Pause = Callable[[], None]
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
    # None: inconclusive (captcha, no internet).
    ok: bool | None
    detail: str
    product: Product | None = None


def in_stock_control(
    fetcher: Fetcher, queries: tuple[str, ...] | list[str], pause: Pause, dump: Dump | None = None
) -> ControlResult:
    """Find a product the search shows with a price and expect its page to read as in stock."""
    tried: list[Product] = []
    for query in queries:
        pause()
        try:
            page = fetcher.get(fetcher.search_url(query))
        except FetchError as exc:
            return ControlResult(None, f"search failed: {exc}")
        result = parse_search(page.text, page.status_code)
        if result.blocked:
            return ControlResult(None, "Amazon showed a captcha")
        candidates = [p for p in result.products if p.status is Status.AVAILABLE and p.price][:2]
        if not candidates and dump:
            dump(f"control_search_{query}", page.text)
        for candidate in candidates:
            pause()
            try:
                product_page = fetcher.get(fetcher.product_url(candidate.asin))
            except FetchError as exc:
                return ControlResult(None, f"product page failed: {exc}")
            product = parse_product(product_page.text, candidate.asin, product_page.status_code)
            if product.status is Status.BLOCKED:
                return ControlResult(None, "Amazon showed a captcha")
            if product.status is Status.AVAILABLE:
                return ControlResult(
                    True,
                    f"'{_short(product.title or candidate.title)}' read as IN STOCK at "
                    f"{_inr(product.price or candidate.price)} ({product.reason})",
                    product,
                )
            tried.append(product)
            if dump:
                dump(f"control_{candidate.asin}", product_page.text)
        if tried:
            break  # pages were found but misread; another query won't change that
    if not tried:
        return ControlResult(False, "no product with a price was recognised in the search results")
    last = tried[-1]
    return ControlResult(
        False,
        f"{len(tried)} product(s) that search showed with a price were read as "
        f"'{last.status}' on their own page ({last.reason})",
        last,
    )


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
    def passed(self) -> bool:
        return all(c.ok is not False for c in self.checks) and any(c.ok for c in self.checks)

    def summary(self) -> str:
        lines = ["PS5 gift card monitor - self-test"]
        for c in self.checks:
            lines.append(f"{c.icon} {c.name}: {c.detail}")
            lines.extend(f"     {line}" for line in c.lines)
        if self.passed:
            lines.append("\nRESULT: the bot reads Amazon.in correctly and would see a restock.")
        else:
            lines.append("\nRESULT: something is not right - see the ❌ / ⚠️ lines above.")
        return "\n".join(lines)


def run(settings, fetcher: Fetcher, pause: Pause, dump: Dump, progress: Callable[[str], None]) -> SelfTest:
    """All checks, in order; stops early when Amazon can't be reached at all."""
    test = SelfTest()

    progress("Checking that Amazon.in can be reached...")
    try:
        home = fetcher.get(f"https://{settings.domain}/")
    except FetchError as exc:
        test.checks.append(Check("Amazon.in reachable", False, f"no connection ({exc})"))
        return test
    if is_blocked(home.text, home.status_code):
        test.checks.append(Check("Amazon.in reachable", False, "Amazon answered with a captcha; try again later"))
        return test
    where = delivery_location(home.text) or "not shown"
    test.checks.append(Check(
        "Amazon.in reachable", True,
        f"HTTP {home.status_code} via {fetcher.backend}; Amazon's delivery location: {where}",
    ))

    progress("Running the gift card searches...")
    found: dict[str, Product] = {}
    results = recognised_pages = 0
    for query in settings.search_queries:
        pause()
        try:
            page = fetcher.get(fetcher.search_url(query))
        except FetchError as exc:
            test.checks.append(Check("Searches", False, f"'{query}' failed: {exc}"))
            return test
        parsed = parse_search(page.text, page.status_code)
        if parsed.blocked:
            test.checks.append(Check("Searches", False, "Amazon answered with a captcha; try again later"))
            return test
        if not parsed.layout_ok:
            dump(f"search_{query}", page.text)
        recognised_pages += parsed.layout_ok
        results += len(parsed.products)
        for p in parsed.products:
            if settings.title_filter.matches(p.title):
                found.setdefault(p.asin, p)
    listing_lines = [f"{p.asin}  {p.status:<11} {_inr(p.price):>9}  {_short(p.title)}" for p in found.values()]
    if not settings.search_queries:
        test.checks.append(Check("Searches", None, "no search queries configured"))
    elif not recognised_pages or not results:
        test.checks.append(Check("Searches", False, "the search pages were not understood (saved for analysis)"))
    elif not found:
        test.checks.append(Check(
            "Searches", None,
            f"{results} results, but none look like an Indian-rupee PlayStation gift card right now",
        ))
    else:
        test.checks.append(Check(
            "Searches", True, f"{results} results, {len(found)} PlayStation gift card listing(s):", listing_lines,
        ))

    progress("Opening the gift card pages...")
    asins = list(dict.fromkeys(settings.asins + list(found)))[:4]
    page_lines, unreadable, in_stock = [], 0, []
    for asin in asins:
        pause()
        try:
            page = fetcher.get(fetcher.product_url(asin))
        except FetchError as exc:
            page_lines.append(f"{asin}  error: {exc}")
            unreadable += 1
            continue
        product = parse_product(page.text, asin, page.status_code)
        if product.status is Status.BLOCKED:
            page_lines.append(f"{asin}  captcha")
            continue
        if product.status not in DEFINITIVE | {Status.NOT_FOUND}:
            unreadable += 1
            dump(f"product_{asin}", page.text)
        if product.status is Status.AVAILABLE:
            in_stock.append(asin)
        page_lines.append(f"{asin}  {product.status}: {product.reason[:70]}")
    if not asins:
        test.checks.append(Check("Gift card pages", None, "no listings to open (none found, no ASINs set)"))
    elif unreadable:
        test.checks.append(Check(
            "Gift card pages", False, f"{unreadable} of {len(asins)} page(s) not understood (saved)", page_lines,
        ))
    else:
        note = f" - {len(in_stock)} IN STOCK NOW!" if in_stock else ""
        test.checks.append(Check("Gift card pages", True, f"{len(asins)} page(s) understood{note}", page_lines))

    progress("Checking that an in-stock product is recognised...")
    control = in_stock_control(fetcher, settings.control_queries, pause, dump)
    test.checks.append(Check("In-stock detection", control.ok, control.detail))
    return test


def state_dir(settings) -> Path:
    """Where the self-test report and saved pages go (next to the status file)."""
    if settings.status_file:
        return Path(settings.status_file).expanduser().parent
    return Path.home() / ".scanamzn"
