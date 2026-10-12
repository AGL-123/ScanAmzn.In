"""Interactive `scanamzn setup`: asks a few questions and writes config.toml.

Meant for setting the bot up on a phone (Termux), where editing a TOML file by
hand is awkward. Running it again offers the current values as defaults.
"""

from __future__ import annotations

import copy
import getpass
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import timezones
from .notifiers import NotifyError
from .notifiers import telegram as telegram_api

APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"
CALLMEBOT_URL = "https://www.callmebot.com/blog/free-api-whatsapp-messages/"
_ASIN = re.compile(r"^[A-Z0-9]{10}$")

Ask = Callable[[str], str]


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # JSON string escapes are valid in TOML basic strings.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def dump_toml(data: dict[str, Any]) -> str:
    """Serialise a parsed config (tables, scalars, arrays, inline tables) back to TOML."""
    lines: list[str] = []

    def table(prefix: str, node: dict[str, Any]) -> None:
        scalars = {k: v for k, v in node.items() if not isinstance(v, dict)}
        tables = {k: v for k, v in node.items() if isinstance(v, dict)}
        if prefix and (scalars or not tables):
            lines.append(f"[{prefix}]")
        lines.extend(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in scalars.items())
        if scalars or (prefix and not tables):
            lines.append("")
        for key, sub in tables.items():
            table(f"{prefix}.{_toml_key(key)}" if prefix else _toml_key(key), sub)

    table("", data)
    return "\n".join(lines)


def normalise_phone(raw: str, country_code: str | None = None) -> str | None:
    """International format (+<country code><number>), or None if unclear.

    A local number (e.g. Israeli 052-123-4567) is converted with ``country_code``;
    without one, the number must already include its country code.
    """
    number = re.sub(r"[\s\-.]", "", raw.replace("(0)", ""))  # "+972 (0)52-..." style
    number = re.sub(r"[()]", "", number)
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
    if country_code and number.startswith("+" + country_code + "0"):
        # Trunk 0 kept after the country code: +972 052... -> +97252...
        number = "+" + country_code + number[len(country_code) + 2:]
    return number if re.fullmatch(r"\+[1-9]\d{7,14}", number) else None


def phone_example(country_code: str | None) -> str:
    return {"972": "052-123-4567", "91": "98765 43210"}.get(country_code or "", "+972 52 123 4567")


def data_estimate(interval: int, asins: int, queries: int = 2, search_every: int = 3,
                  max_discovered: int = 8) -> str:
    """Rough daily mobile-data use for the settings the wizard writes."""
    # Each round fetches every given ASIN plus listings the searches found
    # (typically ~6 in all, at most max_discovered more), and some searches.
    searches = queries / search_every
    rounds_per_day = 86400 / (interval + 15)  # ~15 s of fetching per round
    low = (max(asins, 6) + searches) * rounds_per_day * 0.15 / 1024
    high = (asins + max_discovered + searches) * rounds_per_day * 0.3 / 1024
    return f"Estimated data use: about {low:.1f}-{high:.1f} GB a day. Use Wi-Fi if you can."


class Wizard:
    def __init__(self, path: Path, ask: Ask = input, ask_secret: Ask = getpass.getpass, out=print,
                 telegram=telegram_api):
        self.path = path
        self.telegram = telegram  # Bot API helpers (replaceable in tests)
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
            old_user = self._old("email", "username")
            old_to = self._old("email", "to", default=[]) or []
            if not old_to or old_to == [old_user] or old_user != gmail:
                old_to = [gmail]  # the default was "yourself": follow the new address
            old_to = ", ".join(old_to)
            to = self._prompt("   Send alerts to (comma-separated)", old_to)
            email = {"username": gmail, "password": password,
                     "to": [t.strip() for t in to.split(",") if t.strip()] or [gmail]}

        telegram = self._telegram()

        out("\n4) WHATSAPP ALERTS (optional, free CallMeBot service)")
        out("   CallMeBot is unreliable (often no reply, paused accounts); Telegram is recommended.")
        out(f"   Get an API key first: {CALLMEBOT_URL}")
        out("   (save their number in your contacts and send it: I allow callmebot to send me messages)")
        old_recipients = self._old("whatsapp", "callmebot", "recipients", default=[]) or []
        old_recipient = old_recipients[0] if old_recipients and isinstance(old_recipients[0], dict) else {}
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

        if not email and not telegram and not whatsapp:
            raise SystemExit("No alert channel configured - nothing saved. Run setup again.")

        out("\n5) WHAT TO WATCH")
        out("   The known Sony PlayStation Store gift card listings (Rs.1000, 4000, 9000) are watched")
        out("   automatically, and searches find others. Optionally add more product IDs (ASINs, the")
        out("   10 characters after /dp/ in an Amazon link), separated by commas.")
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
        out("   Every day the bot can send a short 'still running' message"
            " (by e-mail/Telegram when set up, to spare WhatsApp's small allowance).")
        daily = self._prompt_until(
            "   At what hour (0-23, or -1 for none)",
            lambda a: a if re.fullmatch(r"-1|1?\d|2[0-3]", a) else None,
            default=str(self._old("monitor", "daily_status_hour", default=9)),
        )

        out("\n6) SAFETY NET (optional)")
        out("   If Android ever closes Termux, the bot can't warn you itself. A free service such as")
        out("   https://healthchecks.io can e-mail you when the bot stops checking in. Paste its ping")
        out("   URL here, or press Enter to skip.")
        heartbeat = self._prompt_until(
            "   Ping URL", lambda a: a if re.fullmatch(r"https?://\S+", a) else None,
            default=self._old("monitor", "heartbeat_url"), optional=True,
            hint="It should start with https://",
        )

        data = self.merge({
            "email": email,
            "telegram": telegram,
            "whatsapp": whatsapp,
            "heartbeat_url": heartbeat,
            "asins": [a.strip().upper() for a in asins.split(",") if a.strip()],
            "interval": int(interval),
            "timezone": zone,
            "daily_status_hour": int(daily),
        })
        if whatsapp and len(old_recipients) > 1:
            out(f"   (The {len(old_recipients) - 1} other WhatsApp recipient(s) in the file are kept.)")
        self.write(data)
        out(f"\nSaved {self.path}")
        return data

    def _telegram(self) -> dict[str, Any]:
        out = self.out
        out("\n3) TELEGRAM ALERTS (recommended: free, reliable, rings like a chat message)")
        out("   In Telegram, open @BotFather, send /newbot, pick any name and a username ending in 'bot'.")
        out("   BotFather replies with a token like 123456789:AAH... - copy it and paste it here.")
        old_token = str(self._old("telegram", "bot_token"))
        old_chat = str(self._old("telegram", "chat_id"))
        while True:
            # Pressing Enter keeps the saved bot as it is; pasting a token (even
            # the same one) links the chat again.
            raw = self.ask("   Bot token" + (" [keep current]" if old_token else "") + ": ").strip()
            if raw == "-" or (not raw and not old_token):
                return {}
            if not raw and old_chat:
                return {"bot_token": old_token, "chat_id": old_chat}
            token = raw or old_token
            if not self.telegram.valid_token(token):
                out("  That doesn't look right. Copy the whole token from BotFather's message"
                    " (numbers, a colon, then about 35 letters and digits).")
                continue
            try:
                username = self.telegram.bot_username(token)
            except telegram_api.TelegramRejected as exc:
                out(self._telegram_problem(exc))
                continue
            except NotifyError as exc:
                out(f"   Could not reach Telegram to check the token ({exc}). Is the internet on?")
                username = ""
            chat = self._link_telegram_chat(token, username)
            if chat is None:
                continue  # problem with the token: ask for it again
            return {"bot_token": token, "chat_id": chat} if chat else {}

    @staticmethod
    def _telegram_problem(exc: Exception) -> str:
        if getattr(exc, "used_elsewhere", False):
            return ("   This bot is already connected to another app or service. In @BotFather, send"
                    " /newbot to create a new bot just for these alerts, and paste its token.")
        if getattr(exc, "bad_token", False):
            return "   Telegram says this token is not valid. Copy the whole token from BotFather again."
        return f"   Telegram refused the token ({exc})."

    def _link_telegram_chat(self, token: str, username: str) -> str | None:
        """The chat ID; "" to skip Telegram; None to ask for the token again."""
        out = self.out
        while True:
            where = f"https://t.me/{username}" if username else "your new bot in Telegram"
            out(f"   Now open {where} and tap START (no Start button? send it any message).")
            self.ask("   Then press Enter here: ")
            try:
                found = self.telegram.find_chat(token)
            except telegram_api.TelegramRejected as exc:
                out(self._telegram_problem(exc))
                return None
            except NotifyError as exc:
                out(f"   Could not reach Telegram ({exc}). Is the internet on?")
                found = None
            if found:
                chat_id, name = found
                out(f"   Found your chat{f' ({name})' if name else ''}. Telegram alerts will go there.")
                return chat_id
            answer = self._prompt(
                "   Not found yet. Press Enter to check again, type your chat ID, or - to skip Telegram", ""
            )
            if answer == "-":
                return ""
            if re.fullmatch(r"-?[1-9]\d{2,}", answer):
                return answer
            if answer:
                out("  A chat ID is a number that doesn't start with 0.")

    @staticmethod
    def _check_asins(answer: str) -> str | None:
        asins = [a.strip().upper() for a in answer.split(",") if a.strip()]
        return ", ".join(asins) if all(_ASIN.match(a) for a in asins) else None

    def merge(self, answers: dict[str, Any]) -> dict[str, Any]:
        """The existing config with the wizard's answers applied.

        Settings the wizard doesn't ask about (filters, other mail servers,
        extra WhatsApp recipients, ...) are kept as they are.
        """
        data = copy.deepcopy(self.old)
        amazon = data.setdefault("amazon", {})
        amazon["asins"] = answers["asins"]
        amazon.setdefault("search_queries", ["playstation gift card", "psn wallet top up card"])

        mon = data.setdefault("monitor", {})
        mon["interval_seconds"] = answers["interval"]
        mon.setdefault("search_every_cycles", 3)  # searches cost the most data
        mon.setdefault("max_discovered", 8)  # each listing is a page per round
        mon["timezone"] = answers["timezone"]
        mon["daily_status_hour"] = answers["daily_status_hour"]
        mon.setdefault("status_file", "~/.scanamzn/status.json")
        mon.setdefault("debug_dump_dir", "~/.scanamzn/debug")  # pages the bot couldn't read
        mon["heartbeat_url"] = answers["heartbeat_url"]

        if answers["email"]:
            email = data.setdefault("email", {})
            email.update(enabled=True, **answers["email"])
            email.setdefault("smtp_host", "smtp.gmail.com")
            email.setdefault("smtp_port", 465)
            email.setdefault("security", "ssl")
        elif isinstance(data.get("email"), dict):
            data["email"]["enabled"] = False

        if answers["telegram"]:
            data.setdefault("telegram", {}).update(enabled=True, **answers["telegram"])
        elif isinstance(data.get("telegram"), dict):
            data["telegram"]["enabled"] = False

        if answers["whatsapp"]:
            whatsapp = data.setdefault("whatsapp", {})
            whatsapp.update(enabled=True, provider="callmebot")
            callmebot = whatsapp.setdefault("callmebot", {})
            recipients = list(callmebot.get("recipients") or [])
            if recipients:
                recipients[0] = answers["whatsapp"]
            else:
                recipients = [answers["whatsapp"]]
            callmebot["recipients"] = recipients
        elif isinstance(data.get("whatsapp"), dict):
            data["whatsapp"]["enabled"] = False
        return data

    def write(self, data: dict[str, Any]) -> None:
        text = (
            f"# Written by `python -m scanamzn setup` on {datetime.now():%Y-%m-%d %H:%M}.\n"
            "# Run setup again to change these answers (comments here are not kept).\n"
            "# All options are explained in config.example.toml.\n\n" + dump_toml(data)
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # The file holds passwords: readable by this user only.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)  # also if the .tmp file already existed
        os.replace(tmp, self.path)
