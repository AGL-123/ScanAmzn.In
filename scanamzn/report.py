"""Human-readable status output: the status table and the `status` command."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .monitor import format_inr, shorten

_STATUS_ICON = {
    "available": "🟢 in stock",
    "overpriced": "🟠 overpriced",
    "unavailable": "⚪ unavailable",
    None: "… not checked yet",
}


def _status_label(status: str | None) -> str:
    return _STATUS_ICON.get(status, status or "")


def _price(item: dict) -> str:
    return format_inr(item["price"]) if item.get("price") else "–"


def text_table(items: list[dict]) -> str:
    lines = [f"{'ASIN':<12} {'STATUS':<16} {'PRICE':>9}  TITLE"]
    for item in items:
        lines.append(
            f"{item['asin']:<12} {_status_label(item['status']):<16} {_price(item):>9}  "
            f"{shorten(item.get('title') or '', 70)}"
        )
    return "\n".join(lines)


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def _pid_alive(pid: int | None) -> bool | None:
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None  # exists but not ours, or not checkable here
    return True


def describe_status_file(path: str | Path, now: datetime | None = None) -> str:
    """Render the monitor's status file for `scanamzn status`."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "The monitor has not run yet (no status file). Start it first."
    except (OSError, ValueError) as exc:
        return f"Could not read the status file {path}: {exc}"

    now = now or datetime.now().astimezone()
    updated = datetime.fromisoformat(data["updated_at"])
    age = (now - updated).total_seconds()
    expected = (data.get("next_check_in_seconds") or data.get("interval_seconds") or 60) + 120
    alive = _pid_alive(data.get("pid"))
    if alive is False or age > max(expected, 15 * 60):
        state = "🔴 NOT RUNNING (last check " + _ago(age) + ")"
    elif data.get("checks_failing"):
        state = "🟠 RUNNING, but checks are failing"
    else:
        state = "🟢 RUNNING"

    started = datetime.fromisoformat(data["started_at"])
    lines = [
        "PS5 gift card monitor",
        f"  State:       {state}",
        f"  Last check:  {updated:%d %b %H:%M:%S} ({_ago(age)}), round {data.get('cycle', 0)}",
        f"  Running since {started:%d %b %H:%M}",
    ]
    if data.get("last_problem"):
        lines.append(f"  Last problem: {data['last_problem']}")
    in_stock = [i for i in data.get("items", []) if i.get("in_stock")]
    lines.append(f"  In stock now: {len(in_stock)}")
    lines += ["", text_table(data.get("items", []))]
    return "\n".join(lines)
