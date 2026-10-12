"""Notification channels and a dispatcher that fans a message out to all of them."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class NotifyError(Exception):
    pass


@dataclass
class Message:
    subject: str
    body: str
    # One line without newlines, for WhatsApp template parameters.
    summary: str


class Notifier(Protocol):
    name: str

    def send(self, message: Message) -> None:
        """Deliver the message or raise."""


class Dispatcher:
    """Sends each message to every notifier in parallel, with retries, so a slow
    SMTP server never delays the WhatsApp message (or the next stock check)."""

    def __init__(
        self,
        notifiers: list[Notifier],
        retries: int = 3,
        retry_delay: float = 3.0,
        dry_run: bool = False,
    ):
        self.notifiers = notifiers
        self.retries = max(1, retries)
        self.retry_delay = retry_delay
        self.dry_run = dry_run
        self._pool = ThreadPoolExecutor(max_workers=max(2, len(notifiers) * 2), thread_name_prefix="notify")

    def _deliver(self, notifier: Notifier, message: Message) -> bool:
        for attempt in range(1, self.retries + 1):
            try:
                notifier.send(message)
                log.info("notification sent via %s: %s", notifier.name, message.subject)
                return True
            except Exception as exc:  # noqa: BLE001 - every failure is retried
                log.warning(
                    "%s notification failed (attempt %d/%d): %s",
                    notifier.name, attempt, self.retries, exc,
                )
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)
        log.error("giving up on %s notification: %s", notifier.name, message.subject)
        return False

    def send(self, message: Message) -> list[Future]:
        """Queue the message on every channel and return immediately."""
        if self.dry_run or not self.notifiers:
            log.warning("[dry run] would notify: %s\n%s", message.subject, message.body)
            return []
        return [self._pool.submit(self._deliver, n, message) for n in self.notifiers]

    def send_and_wait(self, message: Message) -> list[tuple[Notifier, bool]]:
        futures = self.send(message)
        return [(n, f.result()) for n, f in zip(self.notifiers, futures)]

    def close(self) -> None:
        self._pool.shutdown(wait=True)
