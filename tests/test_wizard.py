import stat
import tomllib

from scanamzn.config import build_notifiers, load
from scanamzn.setup_wizard import Wizard, normalise_phone


def scripted(answers):
    answers = list(answers)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answers.pop(0)

    return ask, asked


def test_wizard_writes_working_config(tmp_path):
    path = tmp_path / "config.toml"
    ask, asked = scripted([
        "not-an-email", "me@gmail.com",       # retried after a bad answer
        "abcd efgh ijkl mnop",                # app password (secret prompt)
        "",                                   # send alerts to: default (me)
        "98765 43210",                        # Indian number without +91
        "123456",                             # CallMeBot key
        "b0abcdefgh",                         # ASINs
        "20", "60",                           # interval: too low, then ok
    ])
    Wizard(path, ask=ask, ask_secret=ask, out=lambda *a: None).run()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == "abcdefghijklmnop"
    assert data["whatsapp"]["callmebot"]["recipients"] == [{"phone": "+919876543210", "apikey": "123456"}]
    assert data["amazon"]["asins"] == ["B0ABCDEFGH"]

    s = load(path, environ={})
    assert s.interval_seconds == 60 and s.daily_status_hour == 9
    assert s.status_file.endswith(".scanamzn/status.json")
    assert [n.name.split("(")[0] for n in build_notifiers(s)] == ["email", "whatsapp/callmebot"]


def test_rerun_keeps_answers_and_can_drop_a_channel(tmp_path):
    path = tmp_path / "config.toml"
    first, _ = scripted(["me@gmail.com", "pw", "", "+919876543210", "key", "", "60"])
    Wizard(path, ask=first, ask_secret=first, out=lambda *a: None).run()
    # Enter keeps everything, "-" removes WhatsApp.
    second, _ = scripted(["", "", "", "-", "", ""])
    Wizard(path, ask=second, ask_secret=second, out=lambda *a: None).run()
    data = tomllib.loads(path.read_text())
    assert data["email"]["password"] == "pw"
    assert "whatsapp" not in data


def test_quote_characters_in_secrets_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    ask, _ = scripted(["me@gmail.com", 'p"w\\d', "", "", "", "60"])
    Wizard(path, ask=ask, ask_secret=ask, out=lambda *a: None).run()
    assert tomllib.loads(path.read_text())["email"]["password"] == 'p"w\\d'


def test_normalise_phone():
    assert normalise_phone("+91 98765-43210") == "+919876543210"
    assert normalise_phone("09876543210") == "+919876543210"
    assert normalise_phone("919876543210") == "+919876543210"
    assert normalise_phone("0044 7700 900123") == "+447700900123"
    assert normalise_phone("12345") is None
