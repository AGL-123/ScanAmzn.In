"""E-mail notifications over SMTP (e.g. Gmail with an app password)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import Message, NotifyError


class EmailNotifier:
    name = "email"
    rationed = False

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
        security: str = "ssl",
        timeout: float = 20.0,
    ):
        if security not in ("ssl", "starttls", "none"):
            raise NotifyError(f"email security must be ssl, starttls or none, not {security!r}")
        if not to_addrs:
            raise NotifyError("email: no recipients configured")
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.from_addr = from_addr or username
        self.to_addrs = to_addrs
        self.security = security
        self.timeout = timeout

    def _build(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.from_addr
        mail["To"] = ", ".join(self.to_addrs)
        mail["Date"] = formatdate(localtime=True)
        mail["Message-ID"] = make_msgid(domain="scanamzn.local")
        # Ask mail clients to flag it as urgent.
        mail["X-Priority"] = "1"
        mail["Importance"] = "High"
        mail.set_content(message.body)
        return mail

    def send(self, message: Message) -> None:
        mail = self._build(message)
        context = ssl.create_default_context()
        if self.security == "ssl":
            smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        with smtp:
            if self.security == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(mail)
