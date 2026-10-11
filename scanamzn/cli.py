"""Command line interface: ``python -m scanamzn <command>``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, Settings, build_notifiers, load
from .fetcher import FetchError, Fetcher
from .monitor import Monitor, shorten, format_inr
from .notifiers import Dispatcher, Message
from .parser import parse_product, parse_search

log = logging.getLogger("scanamzn")


def _setup_logging(verbose: bool, log_file: str = "") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # Third-party libraries are noisy at DEBUG.
    for name in ("urllib3", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _fetcher(s: Settings) -> Fetcher:
    return Fetcher(s.domain, timeout=s.request_timeout, backend=s.http_backend, proxy=s.proxy)


def cmd_run(args: argparse.Namespace, s: Settings) -> int:
    notifiers = [] if args.dry_run else build_notifiers(s)
    if not notifiers and not args.dry_run:
        log.error("no notification channel is enabled - enable [email] and/or [whatsapp] in the config")
        return 2
    log.info("notifications: %s", ", ".join(n.name for n in notifiers) or "dry run (log only)")
    dispatcher = Dispatcher(notifiers, dry_run=args.dry_run)
    fetcher = _fetcher(s)
    monitor = Monitor(s, fetcher, dispatcher)
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop())
    try:
        monitor.run_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        monitor.stop()
        fetcher.close()
        dispatcher.close()
        log.info("stopped")
    return 0


def cmd_once(args: argparse.Namespace, s: Settings) -> int:
    dispatcher = Dispatcher(build_notifiers(s) if args.notify else [], dry_run=not args.notify)
    fetcher = _fetcher(s)
    monitor = Monitor(s, fetcher, dispatcher)
    try:
        report = monitor.run_cycle()
    finally:
        fetcher.close()
        dispatcher.close()
    print()
    print(f"{'ASIN':<12} {'STATUS':<12} {'PRICE':>9}  TITLE")
    for asin in monitor.watch_list() + [a for a in monitor.items if a not in monitor.watch_list()]:
        st = monitor.items.get(asin)
        status = st.status if st and st.status else "-"
        price = format_inr(st.price) if st and st.price else "-"
        print(f"{asin:<12} {status:<12} {price:>9}  {shorten(st.title if st else '', 80)}")
    if report.blocked:
        print("\nAmazon answered with a captcha; try again in a few minutes.")
    return 1 if report.blocked else 0


def cmd_discover(args: argparse.Namespace, s: Settings) -> int:
    fetcher = _fetcher(s)
    matched: dict[str, str] = {}
    try:
        for query in s.search_queries:
            url = fetcher.search_url(query)
            print(f"\n=== {query}\n    {url}")
            try:
                page = fetcher.get(url)
            except FetchError as exc:
                print(f"    request failed: {exc}")
                continue
            result = parse_search(page.text, page.status_code)
            if result.blocked:
                print("    Amazon answered with a captcha; try again in a few minutes.")
                return 1
            if not result.products:
                print(f"    no results recognised (HTTP {page.status_code})")
            for p in result.products:
                why = s.title_filter.why_not(p.title)
                mark = "MATCH" if why is None else "skip "
                print(f"  {mark} {p.asin}  {p.status:<11} {format_inr(p.price):>9}  {shorten(p.title, 90)}")
                if why is None:
                    matched.setdefault(p.asin, p.title)
    finally:
        fetcher.close()
    if matched:
        print("\nASINs that match the filter (paste into [amazon] asins in your config):\n")
        print("asins = [")
        for asin, title in matched.items():
            print(f'    "{asin}",  # {shorten(title, 70)}')
        print("]")
    else:
        print("\nNo matching listings found. Adjust search_queries or [filter] in the config.")
    return 0


def cmd_test_notify(args: argparse.Namespace, s: Settings) -> int:
    notifiers = build_notifiers(s)
    if not notifiers:
        log.error("no notification channel is enabled in the config")
        return 2
    dispatcher = Dispatcher(notifiers, retries=1)
    message = Message(
        subject="🎮 ScanAmzn test notification",
        body=(
            "This is a test from your Amazon.in PS5 gift card monitor.\n"
            "If you can read this, alerts on this channel work.\n\n"
            f"Example product link: https://{s.domain}/dp/B000000000"
        ),
        summary="ScanAmzn test notification - alerts on this channel work.",
    )
    try:
        results = dispatcher.send_and_wait(message)
    finally:
        dispatcher.close()
    for name, ok in results.items():
        print(f"{'OK    ' if ok else 'FAILED'} {name}")
    return 0 if all(results.values()) else 1


def cmd_parse(args: argparse.Namespace) -> int:
    html = Path(args.file).read_text(encoding="utf-8", errors="replace")
    if args.search:
        page = parse_search(html)
        print(f"blocked={page.blocked} layout_ok={page.layout_ok} results={len(page.products)}")
        for p in page.products:
            print(f"  {p.asin}  {p.status:<11} {format_inr(p.price):>9}  {shorten(p.title, 90)}  [{p.reason}]")
    else:
        p = parse_product(html, args.asin)
        print(f"status: {p.status}\nreason: {p.reason}\ntitle:  {p.title}\nprice:  {format_inr(p.price)}\nseller: {p.seller}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scanamzn",
        description="Watch Amazon.in for Indian-rupee PlayStation (PS5) gift cards and alert by e-mail and WhatsApp.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", default="config.toml", help="config file (default: config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="monitor continuously and send alerts")
    p.add_argument("--dry-run", action="store_true", help="log alerts instead of sending them")
    p = sub.add_parser("once", help="run a single check cycle and print what was found")
    p.add_argument("--notify", action="store_true", help="really send alerts for in-stock items")
    sub.add_parser("discover", help="list gift-card listings found by the search queries")
    sub.add_parser("test-notify", help="send a test message on every enabled channel")
    p = sub.add_parser("parse", help="parse a saved Amazon page (for debugging)")
    p.add_argument("file")
    p.add_argument("--search", action="store_true", help="the file is a search results page")
    p.add_argument("--asin", default="UNKNOWN", help="ASIN of the product page")

    args = parser.parse_args(argv)
    if args.command == "parse":
        _setup_logging(args.verbose)
        return cmd_parse(args)
    try:
        settings = load(args.config)
        _setup_logging(args.verbose, settings.log_file)
        commands = {"run": cmd_run, "once": cmd_once, "discover": cmd_discover, "test-notify": cmd_test_notify}
        return commands[args.command](args, settings)
    except (ConfigError, FetchError) as exc:
        _setup_logging(args.verbose)
        log.error("%s", exc)
        return 2
