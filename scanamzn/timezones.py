"""The user's time zone: alerts, the daily status message and logs use local time.

The bot runs wherever the user is (e.g. Israel), not necessarily in India, so
nothing is hard-coded to Indian time.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Country calling code to assume when someone types a local phone number.
_COUNTRY_CODES = {
    "Asia/Jerusalem": "972",
    "Asia/Tel_Aviv": "972",
    "Asia/Kolkata": "91",
    "Asia/Calcutta": "91",
    "Asia/Dubai": "971",
    "Europe/London": "44",
    "America/New_York": "1",
    "America/Chicago": "1",
    "America/Denver": "1",
    "America/Los_Angeles": "1",
    "America/Toronto": "1",
}


class TimezoneError(ValueError):
    pass


def valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def detect() -> str:
    """Best guess at the device's IANA time zone name, or "" if unknown."""
    candidates = []
    tz_env = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz_env:
        candidates.append(tz_env)
    try:  # Android (Termux): the phone's time zone setting
        prop = subprocess.run(
            ["getprop", "persist.sys.timezone"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
        candidates.append(prop)
    except (OSError, subprocess.SubprocessError):
        pass
    try:  # Linux: /etc/localtime -> /usr/share/zoneinfo/Area/City
        target = str(Path("/etc/localtime").resolve())
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    return next((c for c in candidates if c and valid(c)), "")


def resolve(name: str = "") -> tzinfo:
    """The named zone, or the device's own zone when ``name`` is empty."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise TimezoneError(
                f"unknown time zone {name!r}: use a name like Asia/Jerusalem or Asia/Kolkata "
                "(if names never work, run: pip install tzdata)"
            ) from exc
    detected = detect()
    if detected:
        return ZoneInfo(detected)
    # Fixed offset of the system clock; fine unless daylight saving changes mid-run.
    return datetime.now().astimezone().tzinfo


def country_code(zone_name: str) -> str | None:
    """Calling code (without +) for a time zone, when we know it."""
    return _COUNTRY_CODES.get(zone_name)
