import stat
import tomllib

import pytest

from scanamzn.config import build_notifiers, load
from scanamzn.setup_wizard import Wizard, data_estimate, normalise_phone


def scripted(answers):
    answers = list(answers)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answers.pop(0)

    return ask, asked


def run(path, answers, out=None, telegram=None):
    ask, asked = scripted(answers)
    kwargs = {"telegram": telegram} if telegram else {}
    Wizard(path, ask=ask, ask_secret=ask, out=out if out is not None else (lambda *a: None), **kwargs).run()
    return asked


def test_wizard_writes_working_config(tmp_path):
    path = tmp_path / "config.toml"
    printed = []
    asked = run(path, [
        "Mars/Olympus", "Asia/Jerusalem",     # unknown zone is retried
        "not-an-email", "me@gmail.com",       # bad e-mail is retried
        "abcd efgh ijkl mnop",                # app password (secret prompt)
        "",                                   # send alerts to: default (me)
        "",                                   # Telegram bot token: skip
        "052-123-4567",                       # Israeli local mobile number
        "123456",                             # CallMeBot key
        "b0abcdefgh",                         # ASINs
        "5", "10",                            # focused interval: too low, then 10 s
        "20", "60",                           # extensive interval: too low, then ok
        "",                                   # daily status hour: default 9
        "hc-ping.com/x", "https://hc-ping.com/abc",  # safety net URL: needs https://
    ], out=printed.append)
    assert "052-123-4567" in "".join(asked)  # the example matches the user's country
    assert any("+972521234567" in line for line in printed)  # number shown back for checking
    assert any("Estimated data use" in line and "GB a day" in line for line in printed)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == "abcdefghijklmnop"
    assert data["whatsapp"]["callmebot"]["recipients"] == [{"phone": "+972521234567", "apikey": "123456"}]
    assert data["amazon"]["asins"] == ["B0ABCDEFGH"]
    assert data["monitor"]["timezone"] == "Asia/Jerusalem"
    assert data["monitor"]["heartbeat_url"] == "https://hc-ping.com/abc"

    s = load(path, environ={})
    assert s.interval_seconds == 60 and s.daily_status_hour == 9
    assert s.focus_interval_seconds == 10 and data["monitor"]["focus_interval_seconds"] == 10
    assert any("more bot checks" in line for line in printed)  # warned: checking this often
    assert str(s.tz) == "Asia/Jerusalem"
    assert s.status_file.endswith(".scanamzn/status.json")
    assert [n.name.split("(")[0] for n in build_notifiers(s)] == ["email", "whatsapp/callmebot"]


def test_rerun_keeps_answers_and_can_drop_a_channel(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "me@gmail.com", "pw", "", "", "+972521234567", "key", "", "", "60", "8", ""])
    # Enter keeps everything, "-" removes WhatsApp.
    run(path, ["", "", "", "", "", "-", "", "", "", "", ""])
    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == "pw"
    assert data["monitor"]["daily_status_hour"] == 8
    assert data["whatsapp"]["enabled"] is False
    assert [n.name for n in build_notifiers(load(path, environ={}))] == ["email"]


def test_quote_characters_in_secrets_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "me@gmail.com", 'p"w\\d', "", "", "", "", "", "60", "-1", ""])
    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == 'p"w\\d'
    assert data["monitor"]["daily_status_hour"] == -1


@pytest.mark.parametrize(
    "raw, code, expected",
    [
        ("052-123-4567", "972", "+972521234567"),      # Israeli local format
        ("0521234567", "972", "+972521234567"),
        ("+972 52 123 4567", "972", "+972521234567"),  # already international
        ("972521234567", "972", "+972521234567"),      # code typed without +
        ("00972521234567", None, "+972521234567"),     # 00 prefix works anywhere
        ("98765 43210", "91", "+919876543210"),        # Indian local format
        ("09876543210", "91", "+919876543210"),
        ("+44 7700 900123", "972", "+447700900123"),   # a foreign number stays as typed
        ("0521234567", None, None),                    # local number, unknown country
        ("12345", "972", None),
        ("+0521234567", None, None),
        ("+972 (0)52-123-4567", "972", "+972521234567"),  # common Israeli style
        ("+972 052-123-4567", "972", "+972521234567"),
        ("972-052-123-4567", "972", "+972521234567"),
        ("+972 (0)52-123-4567", None, "+972521234567"),
        ("+91 (0)98765 43210", "91", "+919876543210"),
    ],
)
def test_normalise_phone(raw, code, expected):
    assert normalise_phone(raw, code) == expected


def test_data_estimate_scales_with_interval():
    def high(text):
        return float(text.split("-")[1].split()[0])

    assert high(data_estimate(60, 0)) > high(data_estimate(300, 0))
    assert high(data_estimate(300, 12)) > high(data_estimate(300, 0))
    assert high(data_estimate(30, 12)) == high(data_estimate(30, 30))  # the request budget caps it


HAND_EDITED = """
[amazon]
asins = ["B0AAAAAAAA"]
search_queries = ["a", "b", "c"]

[monitor]
interval_seconds = 45
search_every_cycles = 1
max_discovered = 15
notify_when_sold_out = true
timezone = "Asia/Jerusalem"

[filter]
max_price = 1100
require_any = [["playstation"], ["gift card"]]

[email]
enabled = true
smtp_host = "smtp.office365.com"
smtp_port = 587
security = "starttls"
username = "old@outlook.com"
password = "pw"
to = ["old@outlook.com"]

[whatsapp]
enabled = true
provider = "callmebot"

[whatsapp.callmebot]
recipients = [{ phone = "+972521111111", apikey = "1" }, { phone = "+972522222222", apikey = "2" }]
"""


def test_rerun_keeps_settings_the_wizard_does_not_ask_about(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(HAND_EDITED)
    printed = []
    run(path, ["", "", "", "", "", "", "", "", "", "", "", ""], out=printed.append)  # Enter everywhere
    data = tomllib.loads(path.read_text())
    assert data["amazon"]["search_queries"] == ["a", "b", "c"]
    assert (data["monitor"]["search_every_cycles"], data["monitor"]["max_discovered"]) == (1, 15)
    assert data["monitor"]["notify_when_sold_out"] is True
    assert data["filter"] == {"max_price": 1100, "require_any": [["playstation"], ["gift card"]]}
    assert (data["email"]["smtp_host"], data["email"]["smtp_port"], data["email"]["security"]) == (
        "smtp.office365.com", 587, "starttls")
    assert [r["phone"] for r in data["whatsapp"]["callmebot"]["recipients"]] == ["+972521111111", "+972522222222"]
    assert any("other WhatsApp recipient" in line for line in printed)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    load(path, environ={})  # still a valid config


def test_new_gmail_address_becomes_the_default_recipient(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "old@gmail.com", "pw", "", "", "", "", "", "60", "9", ""])
    asked = run(path, ["", "new@gmail.com", "pw2", "", "", "", "", "", "", "", ""])
    assert any("[new@gmail.com]" in q for q in asked)
    assert tomllib.loads(path.read_text())["email"]["to"] == ["new@gmail.com"]


def test_dump_toml_round_trips_the_example_config():
    from pathlib import Path

    from scanamzn.setup_wizard import dump_toml

    example = tomllib.loads((Path(__file__).parent.parent / "config.example.toml").read_text())
    assert tomllib.loads(dump_toml(example)) == example


def test_data_estimate_counts_given_asins():
    def high(text):
        return float(text.split("-")[1].split()[0])

    assert high(data_estimate(300, 5)) > high(data_estimate(300, 0))


TOKEN = "123456789:AAH" + "x" * 32


class FakeTelegram:
    """Stands in for the Bot API: the user taps START after the first check."""

    def __init__(self, started_after=1):
        self.checks, self.started_after = 0, started_after

    @staticmethod
    def valid_token(token):
        from scanamzn.notifiers.telegram import valid_token

        return valid_token(token)

    def bot_username(self, token):
        return "ps5_alerts_bot"

    def find_chat(self, token):
        self.checks += 1
        return ("987654321", "Avi") if self.checks > self.started_after else None


def test_telegram_setup_finds_the_chat_after_start(tmp_path):
    path = tmp_path / "config.toml"
    printed, tg = [], FakeTelegram(started_after=1)
    run(path, [
        "Asia/Jerusalem", "",                 # time zone, no e-mail
        "not a token", TOKEN,                 # Telegram token (retried)
        "",                                   # "press Enter" after tapping START - not done yet
        "",                                   # check again
        "",                                   # "press Enter" after tapping START
        "", "", "", "60", "9", "",            # no WhatsApp, ASINs, focused + extensive interval, hour, ping
    ], out=printed.append, telegram=tg)
    assert any("https://t.me/ps5_alerts_bot" in line for line in printed)
    data = tomllib.loads(path.read_text())
    assert data["telegram"] == {"enabled": True, "bot_token": TOKEN, "chat_id": "987654321"}
    (notifier,) = build_notifiers(load(path, environ={}))
    assert notifier.name == "telegram" and notifier.chat_id == "987654321"

    # Re-running with the same token keeps the chat without asking for START again.
    run(path, ["", "", "", "", "", "", "", "", "", ""], telegram=FakeTelegram(started_after=99))
    assert tomllib.loads(path.read_text())["telegram"]["chat_id"] == "987654321"


def test_telegram_chat_id_can_be_typed_when_detection_fails(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "", TOKEN, "", "55555", "", "", "", "60", "9", ""],
        telegram=FakeTelegram(started_after=99))
    assert tomllib.loads(path.read_text())["telegram"]["chat_id"] == "55555"


class ScriptedTelegram(FakeTelegram):
    """FakeTelegram whose getMe/getUpdates can fail like the real Bot API."""

    def __init__(self, get_me_errors=(), started_after=0, find_errors=()):
        super().__init__(started_after)
        self.get_me_errors, self.find_errors = list(get_me_errors), list(find_errors)
        self.calls = []

    def bot_username(self, token):
        self.calls.append(("getMe", token))
        if self.get_me_errors:
            raise self.get_me_errors.pop(0)
        return "ps5_alerts_bot"

    def find_chat(self, token):
        self.calls.append(("getUpdates", token))
        if self.find_errors:
            raise self.find_errors.pop(0)
        return super().find_chat(token)


def _rejected(description, code):
    from scanamzn.notifiers.telegram import TelegramRejected

    return TelegramRejected(description, code)


def test_bad_token_asks_for_the_token_again(tmp_path):
    path, printed = tmp_path / "config.toml", []
    cut = TOKEN[:-3]  # still looks like a token, but Telegram says Unauthorized
    tg = ScriptedTelegram(get_me_errors=[_rejected("Unauthorized", 401)])
    run(path, ["Asia/Jerusalem", "", cut, TOKEN, "", "", "", "", "60", "9", ""], out=printed.append, telegram=tg)
    assert any("token is not valid" in line for line in printed)
    assert tomllib.loads(path.read_text())["telegram"]["bot_token"] == TOKEN


def test_bot_used_by_another_app_explains_what_to_do(tmp_path):
    path, printed = tmp_path / "config.toml", []
    tg = ScriptedTelegram(find_errors=[_rejected("Conflict: can't use getUpdates method while webhook is active", 409)])
    run(path, ["Asia/Jerusalem", "", TOKEN, "", TOKEN, "", "", "", "", "60", "9", ""], out=printed.append, telegram=tg)
    assert any("connected to another app" in line for line in printed)
    assert tomllib.loads(path.read_text())["telegram"]["chat_id"] == "987654321"


def test_unreachable_telegram_can_be_skipped_with_dash(tmp_path):
    from scanamzn.notifiers.telegram import TelegramUnreachable

    path, printed = tmp_path / "config.toml", []
    down = TelegramUnreachable("telegram request failed: ConnectionError")
    tg = ScriptedTelegram(get_me_errors=[down], find_errors=[down], started_after=99)
    run(path, ["Asia/Jerusalem", "me@gmail.com", "pw", "", TOKEN, "", "-", "", "", "", "60", "9", ""],
        out=printed.append, telegram=tg)
    assert any("Could not reach Telegram" in line for line in printed)
    assert "telegram" not in tomllib.loads(path.read_text())


def test_manual_chat_id_must_not_start_with_zero(tmp_path):
    path, printed = tmp_path / "config.toml", []
    run(path, ["Asia/Jerusalem", "", TOKEN, "", "0521234567", "", "4242", "", "", "", "60", "9", ""],
        out=printed.append, telegram=FakeTelegram(started_after=99))
    assert any("doesn't start with 0" in line for line in printed)
    assert tomllib.loads(path.read_text())["telegram"]["chat_id"] == "4242"


def test_pasting_the_same_token_links_the_chat_again(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "", TOKEN, "", "1111", "", "", "", "60", "9", ""], telegram=FakeTelegram(99))
    tg = ScriptedTelegram(started_after=0)
    run(path, ["", "", TOKEN, "", "", "", "", "", "", ""], telegram=tg)
    assert tomllib.loads(path.read_text())["telegram"]["chat_id"] == "987654321"
    assert ("getUpdates", TOKEN) in tg.calls


def test_enter_keeps_a_placeholder_token(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[telegram]\nenabled = true\nbot_token = "${SCANAMZN_TELEGRAM_TOKEN}"\nchat_id = "987654321"\n'
    )
    tg = ScriptedTelegram()
    run(path, ["Asia/Jerusalem", "", "", "", "", "", "60", "9", ""], telegram=tg)
    data = tomllib.loads(path.read_text())
    assert data["telegram"]["bot_token"] == "${SCANAMZN_TELEGRAM_TOKEN}" and tg.calls == []


def test_focused_interval_defaults_to_30_and_is_kept_on_rerun(tmp_path):
    path = tmp_path / "config.toml"
    asked = run(path, ["Asia/Jerusalem", "me@gmail.com", "pw", "", "", "", "", "", "60", "9", ""])
    assert any("In focused mode, check every how many seconds" in q and "[30]" in q for q in asked)
    assert tomllib.loads(path.read_text())["monitor"]["focus_interval_seconds"] == 30
    run(path, ["", "", "", "", "", "", "", "15", "", "", ""])
    run(path, ["", "", "", "", "", "", "", "", "", "", ""])  # Enter keeps 15
    assert load(path, environ={}).focus_interval_seconds == 15


def test_focused_warning_counts_requests_not_just_seconds(tmp_path):
    from scanamzn.monitor import FOCUS_BUDGET_CAP
    from scanamzn.setup_wizard import focused_rate

    assert focused_rate(0, 10) == 12 and focused_rate(0, 30) == 4
    assert focused_rate(4, 20) == FOCUS_BUDGET_CAP  # 6 cards every 20 s would be 18 a minute
    path = tmp_path / "config.toml"
    printed = []
    run(path, ["Asia/Jerusalem", "me@gmail.com", "pw", "", "", "",
               "B07K6RYVHR, B0B3DDW7TB, B07K6PVR8B, B0CYNZJLBC", "20", "60", "9", ""], out=printed.append)
    text = "\n".join(printed)
    assert "more bot checks" in text and f"at most {FOCUS_BUDGET_CAP} requests a minute" in text
