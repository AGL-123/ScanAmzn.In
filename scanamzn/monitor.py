"""The polling loop: check watched products and searches, alert on restocks."""

from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import Settings
from .fetcher import FetchError, Fetcher
from .filters import face_value
from .models import DEFINITIVE, Product, SearchPage, Status
from .notifiers import INFO, Dispatcher, Message
from .parser import parse_product, parse_search

log = logging.getLogger(__name__)

HEARTBEAT_EVERY_SECONDS = 240


def _http_ping(url: str) -> None:
    import requests

    try:
        requests.get(url, timeout=15)
    except requests.RequestException as exc:
        log.debug("heartbeat ping failed: %s", type(exc).__name__)

def format_inr(amount: float | None) -> str:
    if amount is None:
        return "price not shown"
    return f"₹{amount:,.0f}" if float(amount).is_integer() else f"₹{amount:,.2f}"


def shorten(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class ItemState:
    asin: str
    title: str = ""
    status: Status | None = None
    price: float | None = None
    seller: str = ""
    in_stock: bool = False
    last_alert_at: float = -math.inf


@dataclass
class CycleReport:
    checked: int = 0
    available: list[str] = field(default_factory=list)
    blocked: bool = False
    errors: int = 0


@dataclass
class _Stats:
    """Counters for the daily status message."""

    cycles: int = 0
    pages: int = 0
    alerts: int = 0
    blocked: int = 0
    errors: int = 0

    def add(self, report: CycleReport) -> None:
        self.cycles += 1
        self.pages += report.checked
        self.blocked += int(report.blocked)
        self.errors += report.errors


class Monitor:
    def __init__(
        self,
        settings: Settings,
        fetcher: Fetcher,
        dispatcher: Dispatcher,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        wall: Callable[[], datetime] | None = None,
        pinger: Callable[[str], None] = _http_ping,
    ):
        self.s = settings
        self.fetcher = fetcher
        self.dispatcher = dispatcher
        self.clock = clock
        self.rng = rng or random.Random()
        self.items: dict[str, ItemState] = {}
        self.discovered: list[str] = []
        self.cycle = 0
        self.block_streak = 0
        self.last_success = clock()
        self.last_problem = ""
        self.health_alerted = False
        self._requests_in_cycle = 0
        self._dumped: set[str] = set()
        self._stop = threading.Event()
        self.wall = wall or (lambda: datetime.now(settings.tz))
        self.started_at = self.wall()
        self.stats = _Stats()
        self.stats_since = self.started_at
        self.next_status_at = self._next_daily_status(self.started_at)
        self.pinger = pinger
        self.last_ping = -math.inf

    # -- public API ------------------------------------------------------------
    def watch_list(self) -> list[str]:
        return list(dict.fromkeys(self.s.asins + self.discovered))

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, max_runtime_seconds: float | None = None) -> None:
        """Check until stop() is called or ``max_runtime_seconds`` have passed."""
        log.info(
            "watching %d product(s) and %d search(es) on %s every ~%ds",
            len(self.s.asins), len(self.s.search_queries), self.s.domain, self.s.interval_seconds,
        )
        deadline = self.clock() + max_runtime_seconds if max_runtime_seconds else math.inf
        while not self._stop.is_set():
            started = self.clock()
            try:
                report = self.run_cycle()
            except Exception:  # noqa: BLE001 - an unattended bot must survive its own bugs
                log.exception("unexpected error during cycle %d; continuing", self.cycle)
                self.last_problem = "internal error (see log)"
                report = CycleReport(errors=1)
            self._check_health()
            wait = self.next_wait(report)
            self.stats.add(report)
            self._maybe_daily_status()
            self._heartbeat()
            self.write_status_file(next_check_in=wait)
            log.info(
                "cycle %d: %d page(s) checked, %d in stock%s%s in %.1fs; next check in %.0fs",
                self.cycle, report.checked, len(report.available),
                ", BLOCKED by Amazon" if report.blocked else "",
                f", {report.errors} error(s)" if report.errors else "",
                self.clock() - started, wait,
            )
            remaining = deadline - self.clock()
            if remaining <= 0:
                log.info("maximum run time reached; stopping")
                break
            self._stop.wait(min(wait, remaining))

    def next_wait(self, report: CycleReport) -> float:
        if report.blocked:
            self.block_streak += 1
            self.fetcher.reset()
            wait = min(self.s.interval_seconds * 2**self.block_streak, self.s.max_backoff_seconds)
            log.warning("Amazon is showing a captcha; new session, backing off %.0fs", wait)
            return wait
        self.block_streak = 0
        j = self.s.jitter
        return self.s.interval_seconds * self.rng.uniform(1 - j, 1 + j)

    def run_cycle(self) -> CycleReport:
        self.cycle += 1
        self._requests_in_cycle = 0
        report = CycleReport()
        checked: dict[str, Product] = {}

        for asin in self.watch_list():
            if self._stop.is_set():
                return report
            product = self._check_product(asin, report)
            checked[asin] = product
            if product.status is Status.BLOCKED:
                report.blocked = True
                return report

        if self.s.search_queries and (self.cycle - 1) % self.s.search_every_cycles == 0:
            for query in self.s.search_queries:
                if self._stop.is_set():
                    return report
                page = self._search(query, report)
                if page is None:
                    continue
                if page.blocked:
                    report.blocked = True
                    return report
                self._handle_search_results(page, checked, report)
                if report.blocked:
                    return report
        return report

    # -- checks ----------------------------------------------------------------
    def _pause(self) -> None:
        """Human-ish delay between requests within one cycle."""
        if self._requests_in_cycle:
            self._stop.wait(self.rng.uniform(*self.s.request_delay_seconds))
        self._requests_in_cycle += 1

    def _check_product(self, asin: str, report: CycleReport) -> Product:
        self._pause()
        report.checked += 1
        try:
            page = self.fetcher.get(self.fetcher.product_url(asin))
        except FetchError as exc:
            product = Product(asin, Status.ERROR, reason=str(exc))
        else:
            product = parse_product(page.text, asin, page.status_code)
            if product.status is Status.UNKNOWN:
                self._dump(f"product_{asin}", page.text)
        self._record(product, report)
        return product

    def _search(self, query: str, report: CycleReport) -> SearchPage | None:
        self._pause()
        report.checked += 1
        try:
            page = self.fetcher.get(self.fetcher.search_url(query))
        except FetchError as exc:
            report.errors += 1
            self.last_problem = f"search failed: {exc}"
            log.warning("search %r failed: %s", query, exc)
            return None
        result = parse_search(page.text, page.status_code)
        if result.blocked:
            self.last_problem = "captcha/robot check"
            return result
        if not result.layout_ok:
            report.errors += 1
            self.last_problem = f"search page not understood (HTTP {page.status_code})"
            log.warning("search %r: no results recognised (HTTP %d)", query, page.status_code)
            self._dump(f"search_{query}", page.text)
            return None
        self._mark_success()
        log.debug("search %r: %d results", query, len(result.products))
        return result

    def _handle_search_results(
        self, page: SearchPage, checked: dict[str, Product], report: CycleReport
    ) -> None:
        for item in page.products:
            reason = self.s.title_filter.why_not(item.title)
            if reason:
                log.debug("ignoring %s (%s): %s", item.asin, reason, shorten(item.title))
                continue
            self._discover(item)
            prior = checked.get(item.asin)
            if prior is not None and prior.status in DEFINITIVE:
                continue  # the product page already gave a fresher answer this cycle
            if item.status is Status.AVAILABLE:
                # Search results can lag; confirm on the product page before alerting
                # (unless that page already failed this cycle).
                confirmed = prior or self._check_product(item.asin, report)
                checked[item.asin] = confirmed
                if confirmed.status not in DEFINITIVE:
                    log.warning(
                        "could not confirm %s on its product page (%s); trusting search result",
                        item.asin, confirmed.status,
                    )
                    self._record(item, report)
                if confirmed.status is Status.BLOCKED:
                    report.blocked = True
                    return
            elif prior is None and item.asin not in self.watch_list():
                self._record(item, report)

    def _discover(self, item: Product) -> None:
        if item.asin in self.s.asins or item.asin in self.discovered:
            return
        if len(self.discovered) >= self.s.max_discovered:
            log.debug("not watching %s: max_discovered (%d) reached", item.asin, self.s.max_discovered)
            return
        self.discovered.append(item.asin)
        self.items.setdefault(item.asin, ItemState(item.asin, title=item.title))
        log.info("found listing %s (%s): %s", item.asin, item.status, shorten(item.title, 90))

    # -- state & alerts -----------------------------------------------------------
    def _apply_price_limits(self, product: Product) -> Product:
        if product.status is not Status.AVAILABLE or product.price is None:
            return product
        title = product.title or self.items.get(product.asin, ItemState(product.asin)).title
        if self.s.max_price is not None and product.price > self.s.max_price:
            product.status = Status.OVERPRICED
            product.reason = f"{format_inr(product.price)} is above max_price {format_inr(self.s.max_price)}"
        elif self.s.max_markup_percent is not None and (face := face_value(title)):
            limit = face * (1 + self.s.max_markup_percent / 100)
            if product.price > limit + 0.5:
                product.status = Status.OVERPRICED
                product.reason = f"{format_inr(product.price)} for a {format_inr(face)} card"
        return product

    def _record(self, product: Product, report: CycleReport) -> None:
        if product.status is Status.BLOCKED:
            self.last_problem = "captcha/robot check"
            return
        if product.status in (Status.ERROR, Status.UNKNOWN):
            report.errors += 1
            self.last_problem = f"{product.asin}: {product.status} ({product.reason})"
            log.warning("%s: %s (%s)", product.asin, product.status, product.reason)
            return
        self._mark_success()
        if product.status is Status.NOT_FOUND:
            log.warning("%s: product page not found - check the ASIN", product.asin)
            return

        product = self._apply_price_limits(product)
        state = self.items.setdefault(product.asin, ItemState(product.asin))
        state.title = product.title or state.title
        state.price = product.price
        state.seller = product.seller
        if state.status != product.status:
            log.info(
                "%s is now %s (%s, %s via %s): %s",
                product.asin, product.status.upper(), product.reason, format_inr(product.price),
                product.source, shorten(state.title),
            )
        state.status = product.status

        now = self.clock()
        if product.status is Status.AVAILABLE:
            report.available.append(product.asin)
            if not state.in_stock:
                state.in_stock = True
                cooldown = self.s.realert_cooldown_minutes * 60
                if now - state.last_alert_at >= cooldown:
                    state.last_alert_at = now
                    self.stats.alerts += 1
                    self.dispatcher.send(self._restock_message(state, product))
                else:
                    log.info("%s back in stock, but alerted less than %.0f min ago", product.asin, cooldown / 60)
        elif state.in_stock:
            state.in_stock = False
            if self.s.notify_when_sold_out:
                self.dispatcher.send(self._sold_out_message(state, product))

    def _restock_message(self, state: ItemState, product: Product) -> Message:
        title = state.title or f"ASIN {state.asin}"
        url = self.fetcher.product_url(state.asin)
        face = face_value(title)
        price = format_inr(product.price)
        if face and product.price:
            price += f" (face value {format_inr(face)})"
        now = self.wall().strftime("%d %b %Y, %H:%M:%S %Z").strip()
        lines = [
            "A PlayStation gift card you are watching is AVAILABLE on Amazon.in right now.",
            "These usually sell out within minutes - buy now!",
            "",
            title,
            f"Price: {price}",
        ]
        if product.seller:
            lines.append(f"Seller: {product.seller}")
        lines += [
            "",
            f"Buy: {url}",
            f"Add to cart: {self.fetcher.cart_url(state.asin)}",
            "",
            f"Detected {now} ({product.reason}, {product.source} page).",
        ]
        return Message(
            subject=f"🎮 IN STOCK: {shorten(title, 60)} – {format_inr(product.price)}",
            body="\n".join(lines),
            summary=f"{shorten(title, 60)} is in stock on Amazon.in ({format_inr(product.price)}): {url}",
        )

    def _sold_out_message(self, state: ItemState, product: Product) -> Message:
        title = state.title or f"ASIN {state.asin}"
        reason = "now overpriced" if product.status is Status.OVERPRICED else "sold out"
        return Message(
            subject=f"Gone: {shorten(title, 60)} ({reason})",
            body=f"{title}\nis {reason} on Amazon.in ({product.reason}).\n{self.fetcher.product_url(state.asin)}",
            summary=f"{shorten(title, 60)} is {reason} on Amazon.in.",
            kind=INFO,
        )

    # -- health ------------------------------------------------------------------
    def _mark_success(self) -> None:
        self.last_success = self.clock()
        if self.health_alerted:
            self.health_alerted = False
            log.info("checks are working again")
            self.dispatcher.send(
                Message(
                    subject="✅ ScanAmzn is checking Amazon.in again",
                    body="The PS5 gift card monitor can reach Amazon.in again and is back to normal.",
                    summary="ScanAmzn can reach Amazon.in again.",
                    kind=INFO,
                )
            )

    def _check_health(self) -> None:
        limit = self.s.health_alert_after_minutes * 60
        down_for = self.clock() - self.last_success
        if limit <= 0 or self.health_alerted or down_for < limit:
            return
        self.health_alerted = True
        minutes = math.ceil(down_for / 60)
        log.error("no successful check for %d minutes (%s)", minutes, self.last_problem)
        self.dispatcher.send(
            Message(
                subject="⚠️ ScanAmzn cannot check Amazon.in",
                body=(
                    f"The PS5 gift card monitor has not completed a check for {minutes} minutes, "
                    "so it would miss a restock right now.\n\n"
                    f"Last problem: {self.last_problem or 'unknown'}\n\n"
                    "It keeps retrying automatically. If this persists, Amazon may be blocking "
                    "this machine's IP - try a longer monitor.interval_seconds or another network."
                ),
                summary=f"ScanAmzn has not reached Amazon.in for {minutes} minutes ({self.last_problem}).",
                kind=INFO,
            )
        )

    # -- heartbeat, daily status & status file ------------------------------------
    def _heartbeat(self) -> None:
        """Tell the outside "dead man's switch" service that the monitor is alive."""
        if not self.s.heartbeat_url or self.clock() - self.last_ping < HEARTBEAT_EVERY_SECONDS:
            return
        self.last_ping = self.clock()
        # In the background: a slow ping must never delay stock checks.
        threading.Thread(target=self.pinger, args=(self.s.heartbeat_url,), daemon=True).start()

    def _next_daily_status(self, after: datetime) -> datetime | None:
        hour = self.s.daily_status_hour
        if hour < 0:
            return None
        candidate = after.astimezone(self.s.tz).replace(hour=hour, minute=0, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)

    def _maybe_daily_status(self) -> None:
        now = self.wall()
        if self.next_status_at is None or now < self.next_status_at:
            return
        self.dispatcher.send(self.daily_status_message(now))
        self.stats = _Stats()
        self.stats_since = now
        self.next_status_at = self._next_daily_status(now)

    def daily_status_message(self, now: datetime) -> Message:
        st = self.stats
        watched = [self.items[a] for a in self.watch_list() if a in self.items]
        in_stock = [i for i in watched if i.in_stock]
        lines = [
            "Your Amazon.in PlayStation gift card monitor is running.",
            "",
            f"Since {self.stats_since:%d %b, %H:%M}: {st.cycles} check rounds, {st.pages} pages, "
            f"{st.alerts} in-stock alert(s), {st.blocked} captcha pause(s), {st.errors} error(s).",
        ]
        if self.health_alerted:
            lines.append(f"⚠️ Checks are currently failing: {self.last_problem}")
        lines += ["", f"Watching {len(watched)} listing(s):"]
        for item in watched[:15]:
            status = item.status or "not checked yet"
            lines.append(f"• {shorten(item.title or item.asin, 60)}: {status}")
        if len(watched) > 15:
            lines.append(f"• …and {len(watched) - 15} more")
        lines += ["", f"Next status message: {self._next_daily_status(now):%d %b, %H:%M}."]
        state = f"{len(in_stock)} in stock now" if in_stock else "nothing in stock right now"
        return Message(
            subject=f"✅ PS5 gift card monitor is running ({state})",
            body="\n".join(lines),
            summary=f"PS5 gift card monitor is running: {st.cycles} checks since {self.stats_since:%d %b %H:%M}, "
            f"{len(watched)} listings watched, {state}.",
            kind=INFO,
        )

    def snapshot(self, next_check_in: float | None = None) -> dict:
        """Current state as plain data (for the status file and summaries)."""
        items = []
        for asin in dict.fromkeys(self.watch_list() + list(self.items)):
            item = self.items.get(asin, ItemState(asin))
            items.append({
                "asin": asin,
                "title": item.title,
                "status": item.status.value if item.status else None,
                "price": item.price,
                "seller": item.seller,
                "in_stock": item.in_stock,
                "watched": asin in self.watch_list(),
            })
        return {
            "pid": os.getpid(),
            "updated_at": self.wall().isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "cycle": self.cycle,
            "interval_seconds": self.s.interval_seconds,
            "next_check_in_seconds": round(next_check_in) if next_check_in is not None else None,
            "blocked_streak": self.block_streak,
            "checks_failing": self.health_alerted,
            "last_problem": self.last_problem,
            "items": items,
        }

    def write_status_file(self, next_check_in: float | None = None) -> None:
        if not self.s.status_file:
            return
        path = Path(self.s.status_file)
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.snapshot(next_check_in), indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not write status file %s: %s", path, exc)

    def _dump(self, key: str, html: str) -> None:
        if not self.s.debug_dump_dir or key in self._dumped:
            return
        self._dumped.add(key)
        safe = "".join(c if c.isalnum() else "_" for c in key)[:60]
        path = Path(self.s.debug_dump_dir) / f"{self.wall():%Y%m%d_%H%M%S}_{safe}.html"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            log.info("saved unrecognised page to %s", path)
        except OSError as exc:
            log.warning("could not save debug page: %s", exc)
