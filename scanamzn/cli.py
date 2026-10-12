"""Command line interface: ``python -m scanamzn <command>``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import re
import signal
import sys
from datetime import datetime, tzinfo
from pathlib import Path

from . import __version__
from .config import ConfigError, Settings, build_notifiers, load
from .fetcher import FetchError, Fetcher
from .monitor import Monitor, format_inr, shorten
from .notifiers import Dispatcher, Message, Notifier
from .parser import parse_product, parse_search
from .report import describe_status_file, text_table

log = logging.getLogger("scanamzn")


def _setup_logging(verbose: bool, log_file: str = "", tz: tzinfo | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                Path(log_file).expanduser(), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
        )
    # Log in the user's time zone (the configured one, else the device's).
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    if tz is not None:
        formatter.converter = lambda secs: datetime.fromtimestamp(secs, tz).timetuple()
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers, force=True)
    # Third-party libraries are noisy at DEBUG.
    for name in ("urllib3", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _fetcher(s: Settings) -> Fetcher:
    return Fetcher(s.domain, timeout=s.request_timeout, backend=s.http_backend, proxy=s.proxy)


def _zone_name(s: Settings) -> str:
    return s.timezone or str(getattr(s.tz, "key", "") or datetime.now(s.tz).strftime("%Z"))


def _mask_email(address: str) -> str:
    user, _, domain = address.partition("@")
    return f"{user[:2]}***@{domain}" if domain else "***"


def _channels(notifiers: list[Notifier]) -> list[str]:
    described = []
    for n in notifiers:
        if n.name == "email":
            described.append("e-mail to " + ", ".join(_mask_email(a) for a in getattr(n, "to_addrs", [])))
        else:
            described.append(n.name.replace("whatsapp/", "WhatsApp via "))
    return described


def cmd_check_config(args: argparse.Namespace, s: Settings) -> int:
    notifiers = build_notifiers(s)
    channels = _channels(notifiers)
    lines = [
        f"Watching {len(s.asins)} product(s) and {len(s.search_queries)} search(es) on {s.domain}, "
        f"every ~{s.interval_seconds:.0f}s.",
        "Alerts: " + ("; ".join(channels) if channels else "NONE - set up e-mail and/or WhatsApp"),
    ]
    if s.daily_status_hour >= 0:
        lines.append(f"Daily 'still running' message at {s.daily_status_hour:02d}:00 ({_zone_name(s)}).")
    for line in lines:
        print(line)
    if not notifiers:
        log.error("no notification channel is configured")
        return 2
    return 0


def cmd_run(args: argparse.Namespace, s: Settings) -> int:
    notifiers = [] if args.dry_run else build_notifiers(s)
    if not notifiers and not args.dry_run:
        log.error("no notification channel is enabled - set up e-mail and/or WhatsApp first")
        return 2
    log.info("alerts: %s", "; ".join(_channels(notifiers)) or "dry run (log only)")
    dispatcher = Dispatcher(notifiers, dry_run=args.dry_run)
    fetcher = _fetcher(s)
    monitor = Monitor(s, fetcher, dispatcher)
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop())
    try:
        monitor.run_forever(max_runtime_seconds=args.max_runtime * 60 if args.max_runtime else None)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        monitor.stop()
        fetcher.close()
        dispatcher.close()
        monitor.write_status_file()
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
    items = monitor.snapshot()["items"]
    print()
    print(text_table(items))
    note = ""
    if report.blocked:
        note = "Amazon answered with a captcha; try again in a few minutes."
    elif report.errors and not items:
        note = "Amazon could not be reached (see the log above)."
    if note:
        print("\n" + note)
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
        print("\nListings that match the filter:\n")
        for asin, title in matched.items():
            print(f"  {asin}  {shorten(title, 70)}")
        print("\nTo watch them directly, enter this line at the ASINs question of `setup`:\n")
        print("  " + ", ".join(matched))
    else:
        print("\nNo matching listings found. Adjust search_queries or [filter] in the config.")
    return 0


def cmd_test_notify(args: argparse.Namespace, s: Settings) -> int:
    notifiers = build_notifiers(s)
    if not notifiers:
        log.error("no notification channel is configured")
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
    for notifier, ok in results:
        print(f"{'✅ sent  ' if ok else '❌ FAILED'} {_channels([notifier])[0]}")
    if not all(ok for _, ok in results):
        print("\nA channel failed - check the settings (run setup again) and the messages above.")
    return 0 if all(ok for _, ok in results) else 1


def cmd_status(args: argparse.Namespace, s: Settings) -> int:
    if not s.status_file:
        print("No status_file is configured ([monitor] status_file), so there is nothing to show.")
        return 2
    print(describe_status_file(s.status_file))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from .setup_wizard import Wizard

    path = Path(args.config).expanduser()
    try:
        Wizard(path).run()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled; nothing saved.")
        return 1
    answer = input("\nSend a test alert now? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        settings = load(path)
        _setup_logging(args.verbose, "")
        return cmd_test_notify(args, settings)
    return 0


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


def _minutes(value: str) -> float:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m|min|h)?\s*", value)
    if not m:
        raise argparse.ArgumentTypeError("use minutes (e.g. 340) or hours (e.g. 5.5h)")
    number = float(m.group(1))
    return number * 60 if m.group(2) == "h" else number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scanamzn",
        description="Watch Amazon.in for Indian-rupee PlayStation (PS5) gift cards and alert by e-mail and WhatsApp.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", default="config.toml",
        help="config file (default: config.toml; optional when SCANAMZN_* environment variables are set)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="monitor continuously and send alerts")
    p.add_argument("--dry-run", action="store_true", help="log alerts instead of sending them")
    p.add_argument("--max-runtime", type=_minutes, metavar="MINUTES",
                   help="stop cleanly after this long (e.g. 340 or 5.5h)")
    p = sub.add_parser("once", help="run a single check cycle and print what was found")
    p.add_argument("--notify", action="store_true", help="really send alerts for in-stock items")
    sub.add_parser("discover", help="list gift-card listings found by the search queries")
    sub.add_parser("test-notify", help="send a test message on every enabled channel")
    sub.add_parser("check-config", help="validate the settings and show what will be watched")
    sub.add_parser("status", help="show what the running monitor is doing")
    sub.add_parser("setup", help="answer a few questions to create the config file")
    p = sub.add_parser("parse", help="parse a saved Amazon page (for debugging)")
    p.add_argument("file")
    p.add_argument("--search", action="store_true", help="the file is a search results page")
    p.add_argument("--asin", default="UNKNOWN", help="ASIN of the product page")

    args = parser.parse_args(argv)
    if args.command in ("parse", "setup"):
        _setup_logging(args.verbose)
        try:
            return cmd_parse(args) if args.command == "parse" else cmd_setup(args)
        except ConfigError as exc:
            log.error("%s", exc)
            return 2
    try:
        settings = load(args.config)
        _setup_logging(args.verbose, settings.log_file, settings.tz)
        commands = {
            "run": cmd_run,
            "once": cmd_once,
            "discover": cmd_discover,
            "test-notify": cmd_test_notify,
            "check-config": cmd_check_config,
            "status": cmd_status,
        }
        return commands[args.command](args, settings)
    except (ConfigError, FetchError) as exc:
        _setup_logging(args.verbose)
        log.error("%s", exc)
        return 2
