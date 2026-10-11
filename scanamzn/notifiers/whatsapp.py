"""WhatsApp notifications.

Three providers are supported:

* ``callmebot`` - free, personal-use gateway; you activate it once from your own
  WhatsApp and get an API key. Easiest to set up.
* ``twilio`` - Twilio's WhatsApp API (sandbox or an approved sender).
* ``meta`` - Meta's official WhatsApp Business Cloud API.

Twilio and Meta only allow free-form text inside the 24-hour window after the
recipient last messaged your business number; outside it they require a
pre-approved template (``content_sid`` / ``template_name``).

Each notifier delivers to a single recipient, so the dispatcher's retries never
re-send to someone who already got the message.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from . import Message, NotifyError

log = logging.getLogger(__name__)

_TIMEOUT = 30
_CALLMEBOT_REJECTED = re.compile(r"invalid.{0,30}api ?key|api ?key.{0,30}invalid|^\s*error", re.I)


def _text(message: Message) -> str:
    return f"*{message.subject}*\n\n{message.body}"


def _mask(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return f"…{digits[-4:]}" if len(digits) > 4 else digits


def _check(resp: requests.Response, provider: str) -> None:
    if resp.status_code >= 300:
        raise NotifyError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")


class CallMeBotNotifier:
    url = "https://api.callmebot.com/whatsapp.php"

    def __init__(self, phone: str, apikey: str, session: requests.Session | None = None):
        if not (phone and apikey):
            raise NotifyError("callmebot: every recipient needs 'phone' and 'apikey'")
        self.phone, self.apikey = phone, apikey
        self.name = f"whatsapp/callmebot({_mask(phone)})"
        self.session = session or requests.Session()

    def send(self, message: Message) -> None:
        params = {"phone": self.phone, "text": _text(message), "apikey": self.apikey}
        try:
            resp = self.session.get(self.url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            # The exception text contains the request URL, i.e. the API key.
            raise NotifyError(f"callmebot request failed: {type(exc).__name__}") from None
        _check(resp, "callmebot")
        # CallMeBot answers HTTP 200 with an HTML page even when it rejects a call.
        body = re.sub(r"<[^>]+>", " ", resp.text)
        if _CALLMEBOT_REJECTED.search(body):
            raise NotifyError(f"callmebot rejected the request: {' '.join(body.split())[:200]}")
        log.debug("callmebot response: %s", " ".join(body.split())[:200])


class TwilioNotifier:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        content_sid: str = "",
        session: requests.Session | None = None,
    ):
        if not (account_sid and auth_token and from_number and to_number):
            raise NotifyError("twilio: account_sid, auth_token, from_number and to are required")
        self.account_sid, self.auth_token = account_sid, auth_token
        self.from_number = self._wa(from_number)
        self.to_number = self._wa(to_number)
        self.content_sid = content_sid
        self.name = f"whatsapp/twilio({_mask(to_number)})"
        self.session = session or requests.Session()

    @staticmethod
    def _wa(number: str) -> str:
        number = number.strip()
        return number if number.startswith("whatsapp:") else f"whatsapp:{number}"

    def send(self, message: Message) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = {"From": self.from_number, "To": self.to_number}
        if self.content_sid:
            data["ContentSid"] = self.content_sid
            data["ContentVariables"] = json.dumps({"1": message.summary})
        else:
            data["Body"] = _text(message)
        resp = self.session.post(url, data=data, auth=(self.account_sid, self.auth_token), timeout=_TIMEOUT)
        _check(resp, "twilio")


class MetaCloudNotifier:
    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        to_number: str,
        api_version: str = "v25.0",
        template_name: str = "",
        template_language: str = "en_US",
        template_body_param: bool = True,
        session: requests.Session | None = None,
    ):
        if not (access_token and phone_number_id and to_number):
            raise NotifyError("meta: access_token, phone_number_id and to are required")
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self.access_token = access_token
        # The Cloud API wants the number in international format without "+".
        self.to_number = re.sub(r"\D", "", to_number)
        self.template_name = template_name
        self.template_language = template_language
        self.template_body_param = template_body_param
        self.name = f"whatsapp/meta({_mask(to_number)})"
        self.session = session or requests.Session()

    def payload(self, message: Message) -> dict:
        payload: dict = {"messaging_product": "whatsapp", "to": self.to_number}
        if self.template_name:
            template: dict = {"name": self.template_name, "language": {"code": self.template_language}}
            if self.template_body_param:
                template["components"] = [
                    {"type": "body", "parameters": [{"type": "text", "text": message.summary}]}
                ]
            payload.update(type="template", template=template)
        else:
            payload.update(type="text", text={"preview_url": True, "body": _text(message)})
        return payload

    def send(self, message: Message) -> None:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        resp = self.session.post(self.url, json=self.payload(message), headers=headers, timeout=_TIMEOUT)
        _check(resp, "meta")
