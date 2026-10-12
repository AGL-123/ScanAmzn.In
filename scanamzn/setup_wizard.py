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

APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"
CALLMEBOT_URL = "https://www.callmebot.com/blog/free-api-whatsapp-messages/"
_ASIN = re.compile(r"^[A-Z0-9]{10}$")

Ask = Callable[[str], str]


def _toml(value: Any) -> str:
    # JSON strings and lists of strings are valid TOML basic strings/arrays.
    return json.dumps(value, ensure_ascii=False)


def normalise_phone(raw: str) -> str | None:
    digits = re.sub(r"[\s\-()]", "", raw)
    if re.fullmatch(r"[6-9]\d{9}", digits):  # 10-digit Indian mobile number
        digits = "+91" + digits
    elif re.fullmatch(r"0[6-9]\d{9}", digits):
        digits = "+91" + digits[1:]
    elif re.fullmatch(r"91[6-9]\d{9}", digits):
        digits = "+" + digits
    elif digits.startswith("00"):
        digits = "+" + digits[2:]
    return digits if re.fullmatch(r"\+\d{8,15}", digits) else None


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
                      secret: bool = False, optional: bool = False) -> str:
        while True:
            answer = self._prompt(question, default, secret)
            if optional and answer in ("", "-"):
                return ""
            value = check(answer) if answer else None
            if value is not None:
                return value
            self.out("  That doesn't look right, please try again.")

    def run(self) -> dict[str, Any]:
        out = self.out
        out("\n=== ScanAmzn setup: PS5 gift card alerts from Amazon.in ===")
        out(f"Settings will be saved to {self.path}")
        out("Press Enter to keep the value in [brackets]. Leave a channel empty (or type -) to skip it.\n")

        out("1) E-MAIL ALERTS (Gmail)")
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

        out("\n2) WHATSAPP ALERTS (free CallMeBot service)")
        out(f"   Get your API key first: {CALLMEBOT_URL}")
        out("   (save their number in your contacts and send it: I allow callmebot to send me messages)")
        old_recipient = (self._old("whatsapp", "callmebot", "recipients", default=[{}]) or [{}])[0]
        phone = self._prompt_until(
            "   Your WhatsApp number (e.g. +919876543210)", normalise_phone,
            default=old_recipient.get("phone", ""), optional=True,
        )
        whatsapp: dict[str, Any] = {}
        if phone:
            apikey = self._prompt_until(
                "   Your CallMeBot API key", lambda a: a or None, default=old_recipient.get("apikey", "")
            )
            whatsapp = {"phone": phone, "apikey": apikey}

        if not email and not whatsapp:
            raise SystemExit("No alert channel configured - nothing saved. Run setup again.")

        out("\n3) WHAT TO WATCH")
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

        settings = {
            "email": email,
            "whatsapp": whatsapp,
            "asins": [a.strip().upper() for a in asins.split(",") if a.strip()],
            "interval": int(interval),
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
            "# A short 'still running' message every day at 9:00 IST (-1 turns it off).",
            "daily_status_hour = 9",
            'status_file = "~/.scanamzn/status.json"',
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
