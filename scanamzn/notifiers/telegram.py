"""Telegram notifications through your own bot (official Bot API, free).

Setup takes two minutes on a phone: create a bot with @BotFather, which gives
a token, then open the bot and tap Start so it may message you. The chat id is
found automatically from that /start message (see ``find_chat``).
"""

from __future__ import annotations

import re

import requests

from . import Message, NotifyError

_API = "https://api.telegram.org/bot{token}/{method}"
_TOKEN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
_MAX_TEXT = 4096  # Telegram's message length limit


def valid_token(token: str) -> bool:
    return bool(_TOKEN.fullmatch(token.strip()))


def call(token: str, method: str, session: requests.Session | None = None, **payload):
    """Call a Bot API method and return its ``result``."""
    session = session or requests.Session()
    try:
        resp = session.post(_API.format(token=token, method=method), json=payload, timeout=30)
    except requests.RequestException as exc:
        # The URL, and so the exception text, contains the bot token.
        raise NotifyError(f"telegram request failed: {type(exc).__name__}") from None
    try:
        data = resp.json()
    except ValueError:
        raise NotifyError(f"telegram HTTP {resp.status_code}") from None
    if not data.get("ok"):
        raise NotifyError(f"telegram: {data.get('description', f'HTTP {resp.status_code}')}")
    return data.get("result")


def bot_username(token: str, session: requests.Session | None = None) -> str:
    return call(token, "getMe", session)["username"]


def find_chat(token: str, session: requests.Session | None = None) -> tuple[str, str] | None:
    """(chat id, first name) of the latest private chat that messaged the bot."""
    for update in reversed(call(token, "getUpdates", session) or []):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and "id" in chat:
            return str(chat["id"]), chat.get("first_name", "")
    return None


class TelegramNotifier:
    name = "telegram"
    rationed = False  # no meaningful quota for a few messages a day

    def __init__(self, bot_token: str, chat_id: str, session: requests.Session | None = None):
        if not (bot_token and chat_id):
            raise NotifyError("telegram: bot_token and chat_id are required")
        self.bot_token, self.chat_id = bot_token.strip(), str(chat_id).strip()
        self.session = session or requests.Session()

    def send(self, message: Message) -> None:
        text = f"{message.subject}\n\n{message.body}"
        if len(text) > _MAX_TEXT:
            text = text[: _MAX_TEXT - 1] + "…"
        call(self.bot_token, "sendMessage", self.session, chat_id=self.chat_id, text=text)
