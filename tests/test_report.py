import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scanamzn.report import describe_status_file, text_table

IL = ZoneInfo("Asia/Jerusalem")

ITEMS = [
    {"asin": "B0PSN01000", "title": "PSN Wallet ₹1000 | Digital", "status": "available", "price": 1000.0,
     "in_stock": True},
    {"asin": "B0PSN02000", "title": "PSN Wallet ₹2000", "status": "unavailable", "price": None, "in_stock": False},
    {"asin": "B0PSN03000", "title": "", "status": None, "price": None, "in_stock": False},
]


def test_table():
    text = text_table(ITEMS)
    assert "🟢 in stock" in text and "₹1,000" in text and "not checked yet" in text


def _status(tmp_path, **overrides):
    now = datetime(2026, 10, 12, 10, 0, tzinfo=IL)
    data = {
        "pid": os.getpid(),
        "updated_at": (now - timedelta(seconds=40)).isoformat(),
        "started_at": (now - timedelta(hours=3)).isoformat(),
        "cycle": 120, "interval_seconds": 60, "next_check_in_seconds": 55,
        "checks_failing": False, "last_problem": "", "items": ITEMS,
    } | overrides
    path = tmp_path / "status.json"
    path.write_text(json.dumps(data))
    return describe_status_file(path, now=now)


def test_status_running(tmp_path):
    out = _status(tmp_path)
    assert "🟢 RUNNING" in out and "40 s ago" in out and "In stock now: 1" in out


def test_status_stale_or_dead(tmp_path):
    assert "NOT RUNNING" in _status(tmp_path, updated_at=datetime(2026, 10, 12, 8, 0, tzinfo=IL).isoformat())
    assert "NOT RUNNING" in _status(tmp_path, pid=2**22 + 12345)


def test_status_failing(tmp_path):
    out = _status(tmp_path, checks_failing=True, last_problem="captcha/robot check")
    assert "checks are failing" in out and "captcha" in out


def test_status_missing_file(tmp_path):
    assert "has not run yet" in describe_status_file(tmp_path / "nope.json")


def test_status_starting(tmp_path):
    assert "STARTING" in _status(tmp_path, cycle=0)


def test_not_found_label():
    assert "page not found" in text_table([{"asin": "B0X", "status": "not_found", "price": None}])
