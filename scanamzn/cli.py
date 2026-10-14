"""Command line interface: ``python -m scanamzn <command>``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import random
import re
import signal
import sys
import time
from datetime import datetime, tzinfo
from pathlib import Path

from . import __version__
from .config import ConfigError, Settings, build_notifiers, load
from .fetcher import FetchError, Fetcher
from .monitor import Monitor, format_inr, shorten
from .notifiers import INFO, TEST, Dispatcher, Message, Notifier
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


def _fetcher(s: Settings, browser_dir: str = "browser") -> Fetcher:
    """The monitor's fetcher (and the self-test's, with its own browser
    profile folder: two browsers can't share one)."""
    from .budget import RequestBudget
    from .selftest import state_dir

    folder = state_dir(s)
    budget = RequestBudget(folder / "request-times", s.max_requests_per_minute)
    return Fetcher(s.domain, timeout=s.request_timeout, backend=s.http_backend, proxy=s.proxy, budget=budget,
                   profile_file=folder / "request-profile", ranking_file=folder / "request-ranking",
                   browser_path=s.browser_path, browser_dir=folder / browser_dir)


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
        elif n.name == "telegram":
            described.append("Telegram")
        else:
            described.append(n.name.replace("whatsapp/", "WhatsApp via "))
    return described


def cmd_check_config(args: argparse.Namespace, s: Settings) -> int:
    notifiers = build_notifiers(s)
    channels = _channels(notifiers)
    lines = [
        f"Watching {len(s.asins)} product(s) and {len(s.search_queries)} search(es) on {s.domain}, "
        f"every ~{s.interval_seconds:.0f}s.",
        "Alerts: " + ("; ".join(channels) if channels else "NONE - set up e-mail, Telegram or WhatsApp"),
    ]
    if s.daily_status_hour >= 0:
        lines.append(f"Daily 'still running' message at {s.daily_status_hour:02d}:00 ({_zone_name(s)}).")
    for line in lines:
        print(line)
    if not notifiers:
        log.error("no notification channel is configured")
        return 2
    return 0


def _single_instance_lock(s: Settings):
    """Hold a lock next to the status file so two monitors never run at once.

    Returns the open lock file (keep it), None when locking isn't possible
    here, or False when another monitor holds the lock.
    """
    if not s.status_file:
        return None
    try:
        import fcntl
    except ImportError:  # Windows
        return None
    path = Path(s.status_file).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")  # noqa: SIM115 - held for the life of the process
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    return handle


def cmd_run(args: argparse.Namespace, s: Settings) -> int:
    lock = _single_instance_lock(s)
    if lock is False:
        log.error("another monitor is already running with this status file - not starting a second one")
        return 3
    notifiers = [] if args.dry_run else build_notifiers(s)
    if not notifiers and not args.dry_run:
        log.error("no notification channel is enabled - set up e-mail, Telegram or WhatsApp first")
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
        kind=TEST,
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


def cmd_selftest(args: argparse.Namespace, s: Settings) -> int:
    from . import selftest

    folder = selftest.state_dir(s)
    debug = selftest.debug_dir(s)
    for old in debug.glob("selftest_*.html"):  # don't mix in pages from an earlier run
        old.unlink(missing_ok=True)
    saved: list[Path] = []

    def dump(key: str, html: str) -> None:
        path = debug / ("selftest_" + "".join(c if c.isalnum() else "_" for c in key)[:60] + ".html")
        try:
            debug.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            saved.append(path)
        except OSError as exc:
            log.warning("could not save %s: %s", path, exc)

    def pause() -> bool:
        time.sleep(rng.uniform(*s.request_delay_seconds))
        return False

    def pace(seconds: float) -> None:
        if seconds >= 5:
            print(f"(waiting {seconds:.0f} s, so Amazon doesn't see a burst of requests...)", flush=True)
        time.sleep(seconds)

    # Closing the window (SIGHUP) or a stop (SIGTERM) still runs the clean-up below.
    for name in ("SIGHUP", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda signum, _frame: sys.exit(128 + signum))
    rng = random.Random()
    fetcher = _fetcher(s, browser_dir="browser-check")
    fetcher.wait = pace
    try:
        with selftest.monitor_paused(s):  # a running monitor waits meanwhile
            result = selftest.run(
                s, fetcher, pause=pause, dump=dump,
                progress=lambda text: print(text, flush=True),
                wait=time.sleep,
            )
    finally:
        fetcher.close()
    text = result.summary()
    if saved:
        n = len(set(saved))
        text += (f"\n\nThe pages it read were saved; `ps5 export` copies them to Downloads "
                 f"({n} file{'' if n == 1 else 's'}).")
    print("\n" + text)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "selftest.txt").write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not save the report: %s", exc)

    icon, word = {"pass": ("✅", "passed"), "fail": ("❌", "found a problem"),
                  "inconclusive": ("⚠️", "was not conclusive")}[result.verdict]
    if args.send:
        _send_report(s, f"{icon} PS5 monitor self-test {word}", text, f"PS5 monitor self-test {word}")
    return {"pass": 0, "fail": 1, "inconclusive": 3}[result.verdict]


def _send_report(s: Settings, subject: str, body: str, summary: str) -> None:
    notifiers = build_notifiers(s)
    if not notifiers:
        return
    dispatcher = Dispatcher(notifiers, retries=3)  # mobile networks drop a request now and then
    try:
        dispatcher.send_and_wait(Message(subject=subject, body=body, summary=summary, kind=INFO))
    finally:
        dispatcher.close()


def cmd_probe(args: argparse.Namespace, s: Settings) -> int:
    """Try every request style on the watched cards and keep the order that works."""
    from . import probe, selftest

    folder = selftest.state_dir(s)
    debug = selftest.debug_dir(s)
    for old in debug.glob("probe_*.html"):
        old.unlink(missing_ok=True)

    def dump(key: str, html: str) -> None:
        try:
            debug.mkdir(parents=True, exist_ok=True)
            (debug / ("".join(c if c.isalnum() else "_" for c in key)[:60] + ".html")).write_text(html, encoding="utf-8")
        except OSError as exc:
            log.warning("could not save a page: %s", exc)

    def make_fetcher(name: str) -> Fetcher:
        # Just this style, no browser rescue, nothing remembered: a clean measurement.
        f = Fetcher(s.domain, timeout=s.request_timeout, backend=s.http_backend, proxy=s.proxy,
                    browser_path=s.browser_path, browser_dir=folder / "browser-check", rescue=False, only=name)
        f.wait = time.sleep
        return f

    for name in ("SIGHUP", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda signum, _frame: sys.exit(128 + signum))
    everything = Fetcher(s.domain, backend=s.http_backend, browser_path=s.browser_path)
    styles = list(args.style or everything.profiles)
    browser_missing = everything.browser_path is None and s.http_backend == "auto"
    asins = (s.focus_asins or s.asins)[:2]
    if not asins:
        print("No product pages are watched (only searches), so there is nothing to probe.")
        return 2
    urls = [(a, everything.product_url(a)) for a in asins]
    # The monitor's own pace: its interval spread over the cards it watches.
    interval = s.focus_interval_seconds if s.focus_asins else s.interval_seconds
    spacing = args.spacing or max(interval / len(asins), probe.MIN_SPACING_SECONDS)
    print(f"Testing {len(styles)} request styles, {args.tries} pages each, {spacing:.0f} s apart "
          f"(about {len(styles) * (args.tries * spacing + probe.BETWEEN_STYLES_SECONDS) / 60:.0f} min)."
          " The monitor waits meanwhile.", flush=True)
    with selftest.monitor_paused(s):
        results = probe.run(urls, styles, make_fetcher, wait=time.sleep,
                            progress=lambda text: print(text, flush=True),
                            spacing=spacing, tries=args.tries, dump=dump)
    ranking = probe.rank(results)
    best = next((r for r in results if r.name == ranking[0]), None)
    adopted = best is not None and best.read > 0
    if adopted:
        # Styles not tested this time (e.g. --style) keep their place after the tested ones.
        rest = [p for p in Fetcher(s.domain, backend=s.http_backend, browser_path=s.browser_path,
                                   ranking_file=folder / "request-ranking").profiles if p not in ranking]
        ranking += rest
        try:
            probe.save_ranking(folder / "request-ranking", folder / "request-profile", ranking)
        except OSError as exc:
            log.error("could not save the result: %s", exc)
            adopted = False
    text = probe.summary(results, ranking, adopted, args.tries, spacing, browser_missing)
    print("\n" + text)
    try:
        (folder / "probe.txt").write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    if args.send:
        icon = "✅" if adopted and best.read == best.tries else ("⚠️" if adopted else "❌")
        _send_report(s, f"{icon} PS5 monitor connection test", text, "PS5 monitor connection test")
    return 0 if adopted else 3


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
        _setup_logging(args.verbose, "", settings.tz)
        if cmd_test_notify(args, settings) != 0:
            # The settings are saved either way; a CallMeBot key can take a
            # few minutes to start working.
            print("The settings were saved. Fix the failed channel with setup, or test again later.")
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
    p = sub.add_parser("selftest", help="check against the live site that the bot reads Amazon.in correctly")
    p.add_argument("--send", action="store_true", help="also send the result by e-mail/Telegram")
    p = sub.add_parser("probe", help="find which request styles get Amazon's pages on this network")
    p.add_argument("--send", action="store_true", help="also send the result by e-mail/Telegram")
    p.add_argument("--tries", type=int, default=4, help="pages per style (default 4)")
    p.add_argument("--spacing", type=float, help="seconds between pages (default: the monitor's pace)")
    p.add_argument("--style", action="append", help="test only this style (repeatable)")
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
            "selftest": cmd_selftest,
            "probe": cmd_probe,
        }
        return commands[args.command](args, settings)
    except (ConfigError, FetchError) as exc:
        _setup_logging(args.verbose)
        log.error("%s", exc)
        return 2
