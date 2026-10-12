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


def run(path, answers, out=None):
    ask, asked = scripted(answers)
    Wizard(path, ask=ask, ask_secret=ask, out=out if out is not None else (lambda *a: None)).run()
    return asked


def test_wizard_writes_working_config(tmp_path):
    path = tmp_path / "config.toml"
    printed = []
    asked = run(path, [
        "Mars/Olympus", "Asia/Jerusalem",     # unknown zone is retried
        "not-an-email", "me@gmail.com",       # bad e-mail is retried
        "abcd efgh ijkl mnop",                # app password (secret prompt)
        "",                                   # send alerts to: default (me)
        "052-123-4567",                       # Israeli local mobile number
        "123456",                             # CallMeBot key
        "b0abcdefgh",                         # ASINs
        "20", "60",                           # interval: too low, then ok
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
    assert str(s.tz) == "Asia/Jerusalem"
    assert s.status_file.endswith(".scanamzn/status.json")
    assert [n.name.split("(")[0] for n in build_notifiers(s)] == ["email", "whatsapp/callmebot"]


def test_rerun_keeps_answers_and_can_drop_a_channel(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "me@gmail.com", "pw", "", "+972521234567", "key", "", "60", "8", ""])
    # Enter keeps everything, "-" removes WhatsApp.
    run(path, ["", "", "", "", "-", "", "", "", ""])
    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == "pw"
    assert data["monitor"]["daily_status_hour"] == 8
    assert data["whatsapp"]["enabled"] is False
    assert [n.name for n in build_notifiers(load(path, environ={}))] == ["email"]


def test_quote_characters_in_secrets_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    run(path, ["Asia/Jerusalem", "me@gmail.com", 'p"w\\d', "", "", "", "60", "-1", ""])
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
    assert high(data_estimate(60, 12)) > high(data_estimate(60, 0))


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
    run(path, ["", "", "", "", "", "", "", "", "", ""], out=printed.append)  # Enter everywhere
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
    run(path, ["Asia/Jerusalem", "old@gmail.com", "pw", "", "", "", "60", "9", ""])
    asked = run(path, ["", "new@gmail.com", "pw2", "", "", "", "", "", ""])
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

    assert high(data_estimate(60, 5)) > high(data_estimate(60, 0))
