import pytest
import requests

from scanamzn.config import build_notifiers, from_dict, load
from scanamzn.envconfig import overlay
from scanamzn.notifiers import Message, NotifyError
from scanamzn.notifiers.telegram import TelegramNotifier, find_chat, valid_token

TOKEN = "123456789:AAH" + "x" * 32
MSG = Message(subject="🎮 IN STOCK: PSN ₹1000", body="Buy: https://www.amazon.in/dp/B0X", summary="s")


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data


class Session:
    def __init__(self, data=None, status=200, exc=None):
        self.data, self.status, self.exc, self.calls = data, status, exc, []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return Resp(self.data, self.status)


def test_send_message():
    session = Session({"ok": True, "result": {"message_id": 1}})
    TelegramNotifier(TOKEN, "987654321", session=session).send(MSG)
    url, payload = session.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert payload["chat_id"] == "987654321"
    assert payload["text"].startswith("🎮 IN STOCK") and "https://www.amazon.in/dp/B0X" in payload["text"]


def test_long_messages_are_cut_to_telegram_limit():
    session = Session({"ok": True, "result": {}})
    TelegramNotifier(TOKEN, "1", session=session).send(Message("s", "x" * 10000, "s"))
    assert len(session.calls[0][1]["text"]) == 4096


@pytest.mark.parametrize(
    "session, match",
    [
        (Session({"ok": False, "description": "Forbidden: bot was blocked by the user"}, 403), "blocked by the user"),
        (Session(None, 502), "HTTP 502"),
        (Session(exc=requests.ConnectionError(f"Max retries with url: /bot{TOKEN}/sendMessage")), "ConnectionError"),
    ],
)
def test_errors_are_clear_and_never_contain_the_token(session, match):
    with pytest.raises(NotifyError, match=match) as info:
        TelegramNotifier(TOKEN, "1", session=session).send(MSG)
    assert TOKEN not in str(info.value) and "AAH" not in str(info.value)


def test_find_chat_uses_latest_private_message():
    updates = [
        {"message": {"chat": {"id": 111, "type": "private", "first_name": "Old"}}},
        {"message": {"chat": {"id": -100, "type": "group"}}},
        {"message": {"chat": {"id": 222, "type": "private", "first_name": "Avi"}}},
        {"my_chat_member": {"chat": {"id": 333, "type": "private"}}},
    ]
    assert find_chat(TOKEN, Session({"ok": True, "result": updates})) == ("222", "Avi")
    assert find_chat(TOKEN, Session({"ok": True, "result": []})) is None


def test_valid_token():
    assert valid_token(TOKEN) and valid_token(f"  {TOKEN} ")
    assert not valid_token("123:short") and not valid_token("hello")


def test_config_and_env():
    s = from_dict({"telegram": {"enabled": True, "bot_token": TOKEN, "chat_id": 987654321}})
    (n,) = build_notifiers(s)
    assert n.name == "telegram" and n.chat_id == "987654321" and not n.rationed
    data = overlay({"SCANAMZN_TELEGRAM_TOKEN": TOKEN, "SCANAMZN_TELEGRAM_CHAT_ID": "42"})
    assert data["telegram"] == {"bot_token": TOKEN, "chat_id": "42", "enabled": True}


def test_env_only_telegram_needs_no_config_file(tmp_path):
    s = load(tmp_path / "missing.toml",
             environ={"SCANAMZN_TELEGRAM_TOKEN": TOKEN, "SCANAMZN_TELEGRAM_CHAT_ID": "42"})
    assert [n.name for n in build_notifiers(s)] == ["telegram"]


def test_missing_chat_id_is_a_config_error():
    from scanamzn.config import ConfigError

    with pytest.raises(ConfigError, match="chat_id"):
        build_notifiers(from_dict({"telegram": {"enabled": True, "bot_token": TOKEN}}))
