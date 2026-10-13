"""A request budget shared by every bot process on the phone.

Amazon answers bursts with bot checks (seen on the phone: the 12th request
within about 40 seconds was challenged), while a steady few pages a minute go
through for hours. So the monitor, the self-test and the in-stock check all
draw from one budget - at most N requests in any 60 seconds - kept in a small
file, which holds even when two of them run at the same time.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

WINDOW = 60.0  # seconds


class RequestBudget:
    def __init__(self, path: str | Path | None, per_minute: int, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else None
        self.per_minute = per_minute
        self.clock = clock  # wall clock: shared between processes
        self._local: list[float] = []  # when the file can't be used

    def acquire(self, wait: Callable[[float], Any]) -> bool:
        """Block until a request may be sent, and record it. ``wait(seconds)``
        does the waiting; if it returns something true (e.g. a stop event
        was set), give up and return False."""
        while True:
            delay = self._reserve()
            if delay <= 0:
                return True
            log.debug("request budget: waiting %.1fs", delay)
            if wait(delay):
                return False

    def _reserve(self) -> float:
        """Record a request and return 0 if the budget allows one now, else
        the seconds until it will."""
        if self.per_minute <= 0:
            return 0.0
        if self.path is not None:
            try:
                return self._reserve_in_file()
            except OSError as exc:
                log.debug("request budget file unusable (%s); counting in this process only", exc)
                self.path = None
        self._local, delay = self._take(self._local)
        return delay

    def _take(self, times: list[float]) -> tuple[list[float], float]:
        now = self.clock()
        recent = sorted(t for t in times if now - WINDOW < t <= now + 1)
        if len(recent) >= self.per_minute:
            return recent, recent[-self.per_minute] + WINDOW - now + 0.05
        return recent + [now], 0.0

    def _reserve_in_file(self) -> float:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="ascii") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            times = []
            for line in fh.read().split():
                try:
                    times.append(float(line))
                except ValueError:
                    pass
            times, delay = self._take(times)
            fh.seek(0)
            fh.truncate()
            fh.write("".join(f"{t:.3f}\n" for t in times))
        return delay
