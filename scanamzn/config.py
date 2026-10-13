"""Load config.toml into typed settings.

String values may reference environment variables as ``${NAME}`` so that
passwords and API keys can stay out of the file. Variables are only resolved
for settings that are actually used, so placeholders for a disabled WhatsApp
provider do not need to be set.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path
from typing import Any

from . import envconfig, timezones
from .filters import TitleFilter
from .selftest import CONTROL_QUERIES
from .notifiers import Notifier, NotifyError
from .notifiers.email import EmailNotifier
from .notifiers.telegram import TelegramNotifier
from .notifiers.whatsapp import CallMeBotNotifier, MetaCloudNotifier, TwilioNotifier

log = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ASIN = re.compile(r"^[A-Z0-9]{10}$")

# PlayStation Store gift card listings on Amazon.in (Sony "Email Delivery"
# digital codes), found in October 2026. Amazon hides out-of-stock items from
# search results, so these are watched directly unless
# [amazon] watch_known_listings = false. These are checked every round:
KNOWN_LISTINGS = {
    "B07K6RYVHR": "Rs.1000 Sony PlayStation Store Gift Card",
    "B093QF35KZ": "Rs.2000 Sony PlayStation Store Gift Card",  # seen back in stock, October 2026
    "B07K6RYVJ5": "Rs.4000 Sony PlayStation Store Gift Card",
    "B0CYNZJLBC": "Rs.9000 Sony PlayStation Store Gift Card",
}
# ...and these, the other values, a few per round in turn (all at once when
# any card comes back in stock), so covering them costs little extra data.
MORE_LISTINGS = {
    "B0B3DDW7TB": "Rs.5000 Sony PlayStation Store Gift Card",
    "B07K6RK5KR": "Rs.4500 Sony PlayStation Network Wallet Top-Up",
    "B0CYPG8RYH": "Rs.7000 Sony PlayStation Store Gift Card",
    "B0CYP5X177": "Rs.8000 Sony PlayStation Store Gift Card",
    "B0CYP97QKQ": "Rs.12000 Sony PlayStation Network Wallet Top-Up",
    "B07K6PVXJZ": "Rs.500 Sony PlayStation Network Wallet Top-Up",
    "B0B3D62Q66": "Rs.1200 Sony PlayStation Store Gift Card",
    "B07K6PVR8B": "Rs.3000 Sony PlayStation Network Wallet Top-Up",
    "B07K6QB4NR": "Rs.2500 Sony PlayStation Network Wallet Top-Up",
    "B083V34M47": "Rs.3500 Sony PlayStation Network Wallet Top-Up (Pay On Delivery)",
    "B0B4B88S9V": "PlayStation Network Wallet Top-Up 4500",
}

# The two values seen back in stock far more often than the others (October
# 2026). By default the bot watches only these, more often ("focused mode");
# `ps5 extensive` switches to all the listings above plus searches.
FOCUS_LISTINGS = {
    "B07K6RYVJ5": "Rs.4000 Sony PlayStation Store Gift Card",
    "B093QF35KZ": "Rs.2000 Sony PlayStation Store Gift Card",
}

DEFAULT_QUERIES = [
    "playstation gift card",
    "psn wallet top up card",
    "playstation store gift card india",
]
DEFAULT_REQUIRE_ANY = [
    ["playstation", "psn", "ps5", "ps4", "ps store", "playstation network", "playstation store"],
    ["gift card", "giftcard", "wallet", "top up", "topup", "voucher", "cash card", "network card", "psn card"],
]
DEFAULT_EXCLUDE = [
    # other currencies / regions
    "$", "usd", "usa", "us account", "us store", "us region", "united states",
    "uae", "aed", "saudi", "ksa", "sar", "gbp", "£", "uk account", "€", "eur", "euro",
    "japan", "jpy", "hong kong", "hkd", "indonesia", "turkey", "brazil",
    # other platforms and hardware
    "xbox", "steam", "nintendo", "google play", "itunes", "apple",
    "controller", "console", "headset", "skin", "sticker", "charging", "cover",
]


class ConfigError(Exception):
    pass


class _Section:
    """Typed, env-expanding accessor over one TOML table."""

    def __init__(self, data: dict[str, Any] | None, path: str):
        self.data = data or {}
        self.path = path

    def section(self, key: str) -> _Section:
        value = self.data.get(key, {})
        if not isinstance(value, dict):
            raise ConfigError(f"[{self.path}.{key}] must be a table")
        return _Section(value, f"{self.path}.{key}" if self.path else key)

    def _expand(self, key: str, value: Any) -> Any:
        if isinstance(value, str):
            def sub(m: re.Match[str]) -> str:
                name = m.group(1)
                if name not in os.environ:
                    raise ConfigError(f"{self.path}.{key} references ${{{name}}}, which is not set")
                return os.environ[name]

            return _ENV_REF.sub(sub, value)
        if isinstance(value, list):
            return [self._expand(key, v) for v in value]
        if isinstance(value, dict):
            return {k: self._expand(f"{key}.{k}", v) for k, v in value.items()}
        return value

    def get(self, key: str, default: Any, kind: type | tuple[type, ...]) -> Any:
        value = self._expand(key, self.data.get(key, default))
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            secret = re.search(r"password|apikey|token|secret", key, re.I)
            shown = "(hidden)" if secret else repr(value)
            raise ConfigError(f"{self.path}.{key} has the wrong type: {shown}")
        return value

    def str(self, key: str, default: str = "") -> str:
        return self.get(key, default, str).strip()

    def int(self, key: str, default: int) -> int:
        return self.get(key, default, int)

    def float(self, key: str, default: float) -> float:
        return self.get(key, default, float)

    def bool(self, key: str, default: bool) -> bool:
        return self.get(key, default, bool)

    def list(self, key: str, default: list) -> list:
        return self.get(key, default, list)

    def has(self, key: str) -> bool:
        return key in self.data


@dataclass
class Settings:
    # [amazon]
    domain: str = "www.amazon.in"
    asins: list[str] = field(default_factory=list)
    rotating_asins: list[str] = field(default_factory=list)  # checked one per round
    # Focused mode (the default when set): only these, every focus_interval_seconds.
    # Empty = always the extensive mode.
    focus_asins: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=lambda: list(DEFAULT_QUERIES))
    http_backend: str = "auto"
    proxy: str = ""
    request_timeout: float = 20.0
    # [monitor]
    interval_seconds: float = 45.0  # extensive mode
    focus_interval_seconds: float = 30.0  # focused mode: few pages, so more often
    jitter: float = 0.25
    request_delay_seconds: tuple[float, float] = (1.0, 3.0)
    search_every_cycles: int = 1
    other_listings_per_round: int = 2  # of the values checked in turn (MORE_LISTINGS)
    # Amazon answers bursts with bot checks; shared by all bot processes. 0 = no limit.
    max_requests_per_minute: int = 8
    max_discovered: int = 15
    realert_cooldown_minutes: float = 10.0
    notify_when_sold_out: bool = False
    health_alert_after_minutes: float = 10.0
    # After a bot check: retry after 60 s, 120 s, ... at most this long. Short:
    # a single retry request costs nothing, and every minute unchecked counts.
    max_backoff_seconds: float = 180.0
    debug_dump_dir: str = ""
    log_file: str = ""
    # Hour of day (0-23, local time) for a daily "still running" message; -1 disables.
    daily_status_hour: int = -1
    # IANA time zone name (e.g. "Asia/Jerusalem"); empty = the device's own zone.
    timezone: str = ""
    tz: tzinfo = field(default_factory=timezones.resolve)
    # JSON file updated after every cycle, read by `scanamzn status`.
    status_file: str = ""
    # "Dead man's switch" URL (e.g. healthchecks.io) pinged every few minutes
    # while the monitor runs, so an outside service can warn if it stops.
    heartbeat_url: str = ""
    # How often the running monitor re-checks that it still recognises an
    # in-stock product (see selftest.py); 0 disables.
    selftest_every_hours: float = 6.0
    # Searches for everyday products used as that in-stock reference.
    control_queries: list[str] = field(default_factory=lambda: list(CONTROL_QUERIES))
    # [filter]
    title_filter: TitleFilter = field(
        default_factory=lambda: TitleFilter(DEFAULT_REQUIRE_ANY, DEFAULT_EXCLUDE)
    )
    max_markup_percent: float | None = 10.0
    max_price: float | None = None
    # [email] / [whatsapp] - built lazily so `discover` needs no credentials.
    raw: _Section = field(default_factory=lambda: _Section({}, ""))


def load(path: str | Path, environ: Mapping[str, str] | None = None) -> Settings:
    """Read the config file, overlaid with any SCANAMZN_* environment variables.

    The file is optional when the environment provides the settings.
    """
    environ = os.environ if environ is None else environ
    path = Path(path).expanduser()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    missing = (
        f"config file {path} not found: copy config.example.toml to {path.name}, "
        "run `python -m scanamzn setup`, or set SCANAMZN_* environment variables"
    )
    try:
        env = envconfig.overlay(environ, data)
    except envconfig.EnvConfigError as exc:
        if not path.exists():
            raise ConfigError(f"{missing} (the environment variables set are incomplete: {exc})") from exc
        raise ConfigError(str(exc)) from exc
    if not path.exists():
        # Without a file, the environment must describe at least one alert
        # channel; otherwise a mistyped -c path would silently run on defaults.
        email = env.get("email", {})
        telegram = env.get("telegram", {})
        if not (
            (email.get("username") and email.get("password"))
            or (telegram.get("bot_token") and telegram.get("chat_id"))
            or "whatsapp" in env
        ):
            raise ConfigError(missing)
        log.debug("no config file at %s; using SCANAMZN_* environment variables", path)
    return from_dict(envconfig.merge(data, env))


def from_dict(data: dict[str, Any]) -> Settings:
    root = _Section(data, "")
    s = Settings(raw=root)

    amazon = root.section("amazon")
    s.domain = amazon.str("domain", s.domain)
    s.asins = [a.strip().upper() for a in amazon.list("asins", [])]
    known = amazon.bool("watch_known_listings", True)
    focus = [a.strip().upper() for a in amazon.list("focus_asins", list(FOCUS_LISTINGS) if known else [])]
    for name, asins in (("asins", s.asins), ("focus_asins", focus)):
        for asin in asins:
            if not _ASIN.match(asin):
                raise ConfigError(f"amazon.{name}: {asin!r} is not a 10-character ASIN")
    # ASINs the user added are watched in both modes.
    s.focus_asins = list(dict.fromkeys(focus + s.asins)) if focus else []
    if known:
        s.asins += list(KNOWN_LISTINGS)
        s.rotating_asins = [a for a in MORE_LISTINGS if a not in s.asins]
    s.asins = list(dict.fromkeys(s.asins))
    s.search_queries = [q for q in amazon.list("search_queries", s.search_queries) if q.strip()]
    s.http_backend = amazon.str("http_backend", s.http_backend)
    s.proxy = amazon.str("proxy", s.proxy)
    s.request_timeout = amazon.float("request_timeout", s.request_timeout)
    if not s.asins and not s.search_queries:
        raise ConfigError("nothing to watch: set amazon.asins and/or amazon.search_queries")

    mon = root.section("monitor")
    s.interval_seconds = mon.float("interval_seconds", s.interval_seconds)
    s.focus_interval_seconds = mon.float("focus_interval_seconds", s.focus_interval_seconds)
    if min(s.interval_seconds, s.focus_interval_seconds) < 10:
        raise ConfigError("monitor.interval_seconds and focus_interval_seconds must be at least 10"
                          " (Amazon will block faster polling)")
    s.jitter = min(max(mon.float("jitter", s.jitter), 0.0), 0.9)
    delay = mon.list("request_delay_seconds", list(s.request_delay_seconds))
    if len(delay) != 2 or not all(isinstance(d, (int, float)) for d in delay) or delay[0] > delay[1]:
        raise ConfigError("monitor.request_delay_seconds must be [min, max]")
    s.request_delay_seconds = (float(delay[0]), float(delay[1]))
    s.search_every_cycles = max(1, mon.int("search_every_cycles", s.search_every_cycles))
    s.other_listings_per_round = max(0, mon.int("other_listings_per_round", s.other_listings_per_round))
    s.max_requests_per_minute = max(0, mon.int("max_requests_per_minute", s.max_requests_per_minute))
    s.max_discovered = max(0, mon.int("max_discovered", s.max_discovered))
    s.realert_cooldown_minutes = mon.float("realert_cooldown_minutes", s.realert_cooldown_minutes)
    s.notify_when_sold_out = mon.bool("notify_when_sold_out", s.notify_when_sold_out)
    s.health_alert_after_minutes = mon.float("health_alert_after_minutes", s.health_alert_after_minutes)
    s.max_backoff_seconds = mon.float("max_backoff_seconds", s.max_backoff_seconds)
    debug_dir = mon.str("debug_dump_dir", s.debug_dump_dir)
    s.debug_dump_dir = str(Path(debug_dir).expanduser()) if debug_dir else ""
    s.log_file = mon.str("log_file", s.log_file)
    s.daily_status_hour = mon.int("daily_status_hour", s.daily_status_hour)
    if not -1 <= s.daily_status_hour <= 23:
        raise ConfigError("monitor.daily_status_hour must be an hour 0-23 (local time), or -1 to disable")
    s.timezone = mon.str("timezone", "")
    try:
        s.tz = timezones.resolve(s.timezone)
    except timezones.TimezoneError as exc:
        raise ConfigError(f"monitor.timezone: {exc}") from exc
    s.selftest_every_hours = max(0.0, mon.float("selftest_every_hours", s.selftest_every_hours))
    s.control_queries = [q for q in amazon.list("control_queries", s.control_queries) if q.strip()] \
        or list(CONTROL_QUERIES)
    s.heartbeat_url = mon.str("heartbeat_url", s.heartbeat_url)
    if s.heartbeat_url and not s.heartbeat_url.startswith(("https://", "http://")):
        raise ConfigError("monitor.heartbeat_url must be a web address starting with https://")
    status_file = mon.str("status_file", s.status_file)
    s.status_file = str(Path(status_file).expanduser()) if status_file else ""

    flt = root.section("filter")
    require_any = flt.list("require_any", DEFAULT_REQUIRE_ANY)
    if not all(isinstance(g, list) for g in require_any):
        raise ConfigError("filter.require_any must be a list of lists of words")
    s.title_filter = TitleFilter(require_any, flt.list("exclude", DEFAULT_EXCLUDE))
    # A negative value disables the markup check.
    markup = flt.float("max_markup_percent", s.max_markup_percent)
    s.max_markup_percent = markup if markup >= 0 else None
    max_price = flt.float("max_price", 0.0)
    s.max_price = max_price if max_price > 0 else None
    return s


def build_notifiers(s: Settings) -> list[Notifier]:
    """Create the enabled notification channels, validating their settings."""
    notifiers: list[Notifier] = []
    try:
        email = s.raw.section("email")
        if email.bool("enabled", False):
            username = email.str("username")
            notifiers.append(
                EmailNotifier(
                    host=email.str("smtp_host", "smtp.gmail.com"),
                    port=email.int("smtp_port", 465),
                    security=email.str("security", "ssl"),
                    username=username,
                    password=email.str("password"),
                    from_addr=email.str("from_addr"),
                    # Default: send the alerts to yourself.
                    to_addrs=email.list("to", []) or ([username] if username else []),
                )
            )

        tg = s.raw.section("telegram")
        if tg.bool("enabled", False):
            chat_id = tg.get("chat_id", "", (str, int))
            notifiers.append(TelegramNotifier(tg.str("bot_token"), str(chat_id).strip()))

        wa = s.raw.section("whatsapp")
        if wa.bool("enabled", False):
            provider = wa.str("provider", "callmebot")
            if provider == "callmebot":
                cmb = wa.section("callmebot")
                recipients = cmb.list("recipients", [])
                if not recipients:
                    raise ConfigError("whatsapp.callmebot.recipients is empty")
                for r in recipients:
                    if not isinstance(r, dict):
                        raise ConfigError("whatsapp.callmebot.recipients entries must be {phone=..., apikey=...}")
                    notifiers.append(CallMeBotNotifier(str(r.get("phone", "")), str(r.get("apikey", ""))))
            elif provider == "twilio":
                tw = wa.section("twilio")
                for to in tw.list("to", []):
                    notifiers.append(
                        TwilioNotifier(
                            account_sid=tw.str("account_sid"),
                            auth_token=tw.str("auth_token"),
                            from_number=tw.str("from_number"),
                            to_number=to,
                            content_sid=tw.str("content_sid"),
                        )
                    )
            elif provider == "meta":
                meta = wa.section("meta")
                for to in meta.list("to", []):
                    notifiers.append(
                        MetaCloudNotifier(
                            access_token=meta.str("access_token"),
                            phone_number_id=meta.str("phone_number_id"),
                            to_number=to,
                            api_version=meta.str("api_version", "v25.0"),
                            template_name=meta.str("template_name"),
                            template_language=meta.str("template_language", "en_US"),
                            template_body_param=meta.bool("template_body_param", True),
                        )
                    )
            else:
                raise ConfigError(f"whatsapp.provider must be callmebot, twilio or meta, not {provider!r}")
            if not any(n.name.startswith("whatsapp/") for n in notifiers):
                raise ConfigError(f"whatsapp is enabled but whatsapp.{provider}.to has no numbers")
    except NotifyError as exc:
        raise ConfigError(str(exc)) from exc
    return notifiers
