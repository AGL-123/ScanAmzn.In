"""Notification channels and a dispatcher that fans a message out to all of them."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# Message kinds.
ALERT = "alert"    # stock alerts: every channel
TEST = "test"      # test messages: every channel, failures only reported
INFO = "info"      # daily status, health warnings: spares rationed channels
NOTICE = "notice"  # "channel X failed": never triggers further notices


class NotifyError(Exception):
    pass


@dataclass
class Message:
    subject: str
    body: str
    # One line without newlines, for WhatsApp template parameters.
    summary: str
    kind: str = ALERT


class Notifier(Protocol):
    name: str
    # True for channels with a small message quota or a per-message cost
    # (WhatsApp gateways). They are kept for stock alerts when another channel
    # can carry routine messages.
    rationed: bool

    def send(self, message: Message) -> None:
        """Deliver the message or raise."""


class Dispatcher:
    """Sends each message to its channels in parallel, with retries, so a slow
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

    def targets(self, message: Message) -> list[Notifier]:
        if message.kind in (ALERT, TEST):
            return list(self.notifiers)
        plentiful = [n for n in self.notifiers if not getattr(n, "rationed", False)]
        return plentiful or list(self.notifiers)

    def _deliver(self, notifier: Notifier, message: Message) -> bool:
        error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                notifier.send(message)
                log.info("notification sent via %s: %s", notifier.name, message.subject)
                return True
            except Exception as exc:  # noqa: BLE001 - every failure is retried
                error = exc
                log.warning(
                    "%s notification failed (attempt %d/%d): %s",
                    notifier.name, attempt, self.retries, exc,
                )
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)
        log.error("giving up on %s notification: %s", notifier.name, message.subject)
        if message.kind == ALERT:
            self._report_failure(notifier, message, error)
        return False

    def _report_failure(self, failed: Notifier, message: Message, error: Exception | None) -> None:
        """Tell the user on the other channels that one channel lost an alert."""
        others = [n for n in self.notifiers if n is not failed]
        if not others:
            return
        notice = Message(
            subject=f"⚠️ {failed.name} could not deliver an alert",
            body=(
                f"This alert could not be sent via {failed.name}:\n\n{message.subject}\n\n"
                f"Reason: {error}\n\nThe original alert follows:\n\n{message.body}"
            ),
            summary=f"{failed.name} failed to deliver: {message.subject}",
            kind=NOTICE,
        )
        try:
            for n in others:
                self._pool.submit(self._deliver, n, notice)
        except RuntimeError:  # pool already shut down
            pass

    def send(self, message: Message) -> list[Future]:
        """Queue the message on its channels and return immediately."""
        targets = self.targets(message)
        if self.dry_run or not targets:
            log.warning("[dry run] would notify: %s\n%s", message.subject, message.body)
            return []
        return [self._pool.submit(self._deliver, n, message) for n in targets]

    def send_and_wait(self, message: Message) -> list[tuple[Notifier, bool]]:
        targets = self.targets(message)
        futures = self.send(message)
        return [(n, f.result()) for n, f in zip(targets, futures)]

    def close(self) -> None:
        self._pool.shutdown(wait=True)
