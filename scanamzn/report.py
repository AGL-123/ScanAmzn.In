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
    "not_found": "❌ page not found (removed, or a wrong ASIN)",
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
    elif not data.get("cycle"):
        state = "🟡 STARTING (first check in progress)"
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
    if data.get("mode") == "focused":
        lines.append(f"  Mode:        focused - only the main cards, every ~{data.get('interval_seconds', 30):.0f}s"
                     " (PS5 Extensive mode: all cards)")
    elif data.get("mode") == "extensive":
        lines.append("  Mode:        extensive - every card value plus searches (PS5 Extensive mode again: focused)")
    if data.get("data_saver"):
        lines.append("  Data saver:  ON - fewer pages, half as often (tap PS5 Data saver to switch off)")
    if data.get("blocked_streak"):
        lines.append(f"  Bot checks:  {data['blocked_streak']} in a row - retrying every 1-3 minutes,"
                     " switching request style")
    current = data.get("request_profile") or ""
    styles = data.get("request_stats") or {}
    used = [(name, st) for name, st in styles.items()
            if name == current or st.get("ok") or st.get("blocked") or st.get("errors")]
    used.sort(key=lambda item: item[0] != current)
    for n, (name, st) in enumerate(used):
        line = (f"{name}{' (in use)' if name == current and len(used) > 1 else ''}: {st.get('ok', 0)} pages read,"
                f" {st.get('blocked', 0)} bot check(s)")
        if st.get("errors"):
            line += f", {st['errors']} failed"
        lines.append(f"  {'Requests:' if n == 0 else '':<13}{'style ' if n == 0 else ''}{line}"
                     + (" since start" if n == 0 else ""))
    if data.get("browser_ready") is False and any(st.get("blocked") for st in styles.values()):
        lines.append("  Tip:         `ps5 browser` adds a real browser that reads pages Amazon blocks")
    if data.get("last_problem"):
        lines.append(f"  Last problem: {data['last_problem']}")
    control = data.get("selftest") or {}
    if control.get("ok") is not None:
        at = datetime.fromisoformat(control["at"])
        mark = "✅ passed" if control["ok"] else "❌ FAILED"
        lines.append(f"  In-stock test: {mark} {_ago((now - at).total_seconds())}")
        if not control["ok"]:
            lines.append(f"                {control.get('detail', '')}")
    in_stock = [i for i in data.get("items", []) if i.get("in_stock")]
    lines.append(f"  In stock now: {len(in_stock)}")
    lines += ["", text_table(data.get("items", []))]
    return "\n".join(lines)
