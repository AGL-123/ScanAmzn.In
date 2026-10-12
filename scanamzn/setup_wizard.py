"""Interactive `scanamzn setup`: asks a few questions and writes config.toml.

Meant for setting the bot up on a phone (Termux), where editing a TOML file by
hand is awkward. Running it again offers the current values as defaults.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import timezones

APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"
CALLMEBOT_URL = "https://www.callmebot.com/blog/free-api-whatsapp-messages/"
_ASIN = re.compile(r"^[A-Z0-9]{10}$")

Ask = Callable[[str], str]


def _toml(value: Any) -> str:
    # JSON strings and lists of strings are valid TOML basic strings/arrays.
    return json.dumps(value, ensure_ascii=False)


def normalise_phone(raw: str, country_code: str | None = None) -> str | None:
    """International format (+<country code><number>), or None if unclear.

    A local number (e.g. Israeli 052-123-4567) is converted with ``country_code``;
    without one, the number must already include its country code.
    """
    number = re.sub(r"[\s\-().]", "", raw)
    if number.startswith("00"):
        number = "+" + number[2:]
    if not number.startswith("+"):
        if not (number.isdigit() and country_code):
            return None
        if number.startswith(country_code) and len(number) > 10:  # code typed without "+"
            number = "+" + number
        else:
            local = number[1:] if number.startswith("0") else number  # 052... -> 52...
            if len(local) < 7:
                return None
            number = "+" + country_code + local
    return number if re.fullmatch(r"\+[1-9]\d{7,14}", number) else None


def phone_example(country_code: str | None) -> str:
    return {"972": "052-123-4567", "91": "98765 43210"}.get(country_code or "", "+972 52 123 4567")


def data_estimate(interval: int, asins: int, queries: int = 2, search_every: int = 3,
                  max_discovered: int = 8) -> str:
    """Rough daily mobile-data use for the settings the wizard writes."""
    # Watched listings: the ASINs given, plus what the searches find (capped).
    pages_per_round = max(asins, min(max_discovered, 6)) + queries / search_every
    rounds_per_day = 86400 / (interval + 15)  # ~15 s of fetching per round
    low, high = (pages_per_round * rounds_per_day * mb / 1024 for mb in (0.15, 0.3))
    return f"Estimated data use: about {low:.1f}-{high:.1f} GB a day. Use Wi-Fi if you can."


class Wizard:
    def __init__(self, path: Path, ask: Ask = input, ask_secret: Ask = getpass.getpass, out=print):
        self.path = path
        self.ask = ask
        self.ask_secret = ask_secret
        self.out = out
        self.old: dict[str, Any] = {}
        if path.exists():
            try:
                self.old = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                self.old = {}

    def _old(self, *keys: str, default: Any = "") -> Any:
        node: Any = self.old
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _prompt(self, question: str, default: str = "", secret: bool = False) -> str:
        shown = "(keep current)" if secret and default else default
        suffix = f" [{shown}]" if shown else ""
        reader = self.ask_secret if secret else self.ask
        answer = reader(f"{question}{suffix}: ").strip()
        return answer or default

    def _prompt_until(self, question: str, check: Callable[[str], str | None], default: str = "",
                      secret: bool = False, optional: bool = False, hint: str = "") -> str:
        while True:
            answer = self._prompt(question, default, secret)
            if optional and answer in ("", "-"):
                return ""
            value = check(answer) if answer else None
            if value is not None:
                return value
            self.out("  That doesn't look right, please try again." + (f" {hint}" if hint else ""))

    def run(self) -> dict[str, Any]:
        out = self.out
        out("\n=== ScanAmzn setup: PS5 gift card alerts from Amazon.in ===")
        out(f"Settings will be saved to {self.path}")
        out("Press Enter to keep the value in [brackets]. Leave a channel empty (or type -) to skip it.\n")

        out("1) YOUR TIME ZONE (for message times and the daily status message)")
        zone = self._prompt_until(
            "   Time zone", lambda a: a if timezones.valid(a) else None,
            default=self._old("monitor", "timezone") or timezones.detect(),
            hint="Use a name like Asia/Jerusalem or Asia/Kolkata.",
        )
        country_code = timezones.country_code(zone)

        out("\n2) E-MAIL ALERTS (Gmail)")
        out(f"   You need a Gmail 'app password' (needs 2-Step Verification): {APP_PASSWORD_URL}")
        gmail = self._prompt_until(
            "   Your Gmail address",
            lambda a: a if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a) else None,
            default=self._old("email", "username"), optional=True,
        )
        email: dict[str, Any] = {}
        if gmail:
            password = self._prompt_until(
                "   Gmail app password (16 letters, typing is hidden)",
                lambda a: a.replace(" ", "") or None,
                default=self._old("email", "password"), secret=True,
            )
            old_to = ", ".join(self._old("email", "to", default=[]) or [gmail])
            to = self._prompt("   Send alerts to (comma-separated)", old_to)
            email = {"username": gmail, "password": password,
                     "to": [t.strip() for t in to.split(",") if t.strip()] or [gmail]}

        out("\n3) WHATSAPP ALERTS (free CallMeBot service)")
        out(f"   Get your API key first: {CALLMEBOT_URL}")
        out("   (save their number in your contacts and send it: I allow callmebot to send me messages)")
        old_recipient = (self._old("whatsapp", "callmebot", "recipients", default=[{}]) or [{}])[0]
        phone = self._prompt_until(
            f"   Your WhatsApp number (e.g. {phone_example(country_code)})",
            lambda a: normalise_phone(a, country_code),
            default=old_recipient.get("phone", ""), optional=True,
            hint="Include your country code, e.g. +972 52 123 4567.",
        )
        whatsapp: dict[str, Any] = {}
        if phone:
            out(f"   WhatsApp alerts will go to {phone}")
            apikey = self._prompt_until(
                "   Your CallMeBot API key", lambda a: a or None, default=old_recipient.get("apikey", "")
            )
            whatsapp = {"phone": phone, "apikey": apikey}

        if not email and not whatsapp:
            raise SystemExit("No alert channel configured - nothing saved. Run setup again.")

        out("\n4) WHAT TO WATCH")
        out("   Searches find PlayStation gift cards automatically. Optionally add product IDs (ASINs,")
        out("   the 10 characters after /dp/ in an Amazon link), separated by commas.")
        asins = self._prompt_until(
            "   ASINs", self._check_asins, default=", ".join(self._old("amazon", "asins", default=[])),
            optional=True,
        )
        interval = self._prompt_until(
            "   Check every how many seconds (30 or more)",
            lambda a: a if a.isdigit() and int(a) >= 30 else None,
            default=str(int(self._old("monitor", "interval_seconds", default=60))),
        )

        out("   " + data_estimate(int(interval), len([a for a in asins.split(",") if a.strip()])))
        out("   Every day the bot can send a short 'still running' message (by e-mail if you set it up).")
        daily = self._prompt_until(
            "   At what hour (0-23, or -1 for none)",
            lambda a: a if re.fullmatch(r"-1|1?\d|2[0-3]", a) else None,
            default=str(self._old("monitor", "daily_status_hour", default=9)),
        )

        out("\n5) SAFETY NET (optional)")
        out("   If Android ever closes Termux, the bot can't warn you itself. A free service such as")
        out("   https://healthchecks.io can e-mail you when the bot stops checking in. Paste its ping")
        out("   URL here, or press Enter to skip.")
        heartbeat = self._prompt_until(
            "   Ping URL", lambda a: a if re.fullmatch(r"https?://\S+", a) else None,
            default=self._old("monitor", "heartbeat_url"), optional=True,
            hint="It should start with https://",
        )

        settings = {
            "email": email,
            "whatsapp": whatsapp,
            "heartbeat_url": heartbeat,
            "asins": [a.strip().upper() for a in asins.split(",") if a.strip()],
            "interval": int(interval),
            "timezone": zone,
            "daily_status_hour": int(daily),
        }
        self.write(settings)
        out(f"\nSaved {self.path}")
        return settings

    @staticmethod
    def _check_asins(answer: str) -> str | None:
        asins = [a.strip().upper() for a in answer.split(",") if a.strip()]
        return ", ".join(asins) if all(_ASIN.match(a) for a in asins) else None

    def write(self, s: dict[str, Any]) -> None:
        lines = [
            f"# Written by `python -m scanamzn setup` on {datetime.now():%Y-%m-%d %H:%M}.",
            "# Run setup again to change these answers. All options: config.example.toml",
            "",
            "[amazon]",
            f"asins = {_toml(s['asins'])}",
            'search_queries = ["playstation gift card", "psn wallet top up card"]',
            "",
            "[monitor]",
            f"interval_seconds = {s['interval']}",
            "# Searches cost the most data; run them every 3rd round.",
            "search_every_cycles = 3",
            "# Each watched listing is one page download per round.",
            "max_discovered = 8",
            f"timezone = {_toml(s['timezone'])}",
            "# A short 'still running' message every day at this hour (-1 turns it off).",
            f"daily_status_hour = {s['daily_status_hour']}",
            'status_file = "~/.scanamzn/status.json"',
            f"heartbeat_url = {_toml(s['heartbeat_url'])}",
            "",
        ]
        if s["email"]:
            e = s["email"]
            lines += [
                "[email]",
                "enabled = true",
                'smtp_host = "smtp.gmail.com"',
                "smtp_port = 465",
                'security = "ssl"',
                f"username = {_toml(e['username'])}",
                f"password = {_toml(e['password'])}",
                f"to = {_toml(e['to'])}",
                "",
            ]
        if s["whatsapp"]:
            w = s["whatsapp"]
            lines += [
                "[whatsapp]",
                "enabled = true",
                'provider = "callmebot"',
                "",
                "[whatsapp.callmebot]",
                f"recipients = [{{ phone = {_toml(w['phone'])}, apikey = {_toml(w['apikey'])} }}]",
                "",
            ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # The file holds passwords: readable by this user only.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        os.replace(tmp, self.path)
