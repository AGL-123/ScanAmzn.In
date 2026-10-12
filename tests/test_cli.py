import pytest

from scanamzn import cli
from scanamzn.cli import _minutes, main


class OkNotifier:
    def __init__(self, name, ok=True):
        self.name, self.ok, self.sent = name, ok, []
        self.to_addrs = ["someone@gmail.com"]

    def send(self, message):
        if not self.ok:
            raise RuntimeError("nope")
        self.sent.append(message)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no config.toml here: settings come from the environment
    for key, value in {
        "SCANAMZN_SMTP_USER": "someone@gmail.com",
        "SCANAMZN_SMTP_PASSWORD": "pw",
        "SCANAMZN_ASINS": "B0ABCDEFGH",
        "SCANAMZN_DAILY_STATUS_HOUR": "9",
        "SCANAMZN_TIMEZONE": "Asia/Jerusalem",
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def test_check_config(env, capsys):
    assert main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert "1 product(s)" in out and "e-mail to so***@gmail.com" in out and "09:00 (Asia/Jerusalem)" in out
    assert "someone@gmail.com" not in out  # addresses are masked in logs


def test_check_config_without_channels(env, monkeypatch):
    monkeypatch.delenv("SCANAMZN_SMTP_USER")
    monkeypatch.delenv("SCANAMZN_SMTP_PASSWORD")
    assert main(["check-config"]) == 2


def test_bad_settings_are_reported(env, monkeypatch, capsys):
    monkeypatch.setenv("SCANAMZN_INTERVAL_SECONDS", "soon")
    assert main(["check-config"]) == 2
    assert "SCANAMZN_INTERVAL_SECONDS must be a number" in capsys.readouterr().out


def test_test_notify(env, monkeypatch, capsys):
    good, bad = OkNotifier("email"), OkNotifier("whatsapp/callmebot(…3210)", ok=False)
    monkeypatch.setattr(cli, "build_notifiers", lambda s: [good, bad])
    monkeypatch.setattr(cli.Dispatcher, "__init__", _fast_dispatcher_init)
    assert main(["test-notify"]) == 1
    assert good.sent and "test notification" in good.sent[0].subject
    out = capsys.readouterr().out
    assert "✅ sent" in out and "❌ FAILED" in out and "WhatsApp via callmebot" in out


def _fast_dispatcher_init(self, notifiers, retries=3, retry_delay=3.0, dry_run=False):
    from concurrent.futures import ThreadPoolExecutor

    self.notifiers, self.retries, self.retry_delay, self.dry_run = notifiers, 1, 0, dry_run
    self._pool = ThreadPoolExecutor(max_workers=2)


def test_status_without_status_file(env):
    assert main(["status"]) == 2


@pytest.mark.parametrize("value, minutes", [("340", 340), ("5.5h", 330), ("90m", 90), (" 10 min ", 10)])
def test_max_runtime_parsing(value, minutes):
    assert _minutes(value) == minutes


def test_missing_config_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in [k for k in __import__("os").environ if k.startswith("SCANAMZN_")]:
        monkeypatch.delenv(key)
    assert main(["once"]) == 2
    assert "setup" in capsys.readouterr().out


def test_single_instance_lock(tmp_path):
    from scanamzn.cli import _single_instance_lock
    from scanamzn.config import from_dict

    s = from_dict({"monitor": {"status_file": str(tmp_path / "status.json")}})
    first = _single_instance_lock(s)
    assert first not in (None, False)
    # flock locks belong to the open file, so a second open in the same
    # process conflicts just like a second process would.
    assert _single_instance_lock(s) is False
    first.close()
    again = _single_instance_lock(s)
    assert again not in (None, False)
    again.close()
    assert _single_instance_lock(from_dict({})) is None  # no status file: no lock


def test_run_refuses_second_instance(env, monkeypatch, tmp_path):
    from scanamzn.cli import _single_instance_lock
    from scanamzn.config import from_dict

    status = tmp_path / "s" / "status.json"
    monkeypatch.setenv("SCANAMZN_STATUS_FILE", str(status))
    held = _single_instance_lock(from_dict({"monitor": {"status_file": str(status)}}))
    try:
        assert main(["run", "--dry-run"]) == 3
    finally:
        held.close()
