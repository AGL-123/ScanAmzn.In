import json

import pytest

from scanamzn.notifiers import Dispatcher, Message, NotifyError
from scanamzn.notifiers import email as email_mod
from scanamzn.notifiers.email import EmailNotifier
from scanamzn.notifiers.whatsapp import CallMeBotNotifier, MetaCloudNotifier, TwilioNotifier

MSG = Message(subject="🎮 IN STOCK: PSN ₹1000", body="Buy: https://www.amazon.in/dp/B0X", summary="PSN in stock")


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code, self.text = status_code, text


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, mail):
        self.sent.append(mail)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)


def test_email_ssl():
    n = EmailNotifier("smtp.gmail.com", 465, "me@gmail.com", "app-pass", "", ["me@gmail.com", "b@x.in"])
    n.send(MSG)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.started_tls) == ("smtp.gmail.com", 465, False)
    assert smtp.logged_in == ("me@gmail.com", "app-pass")
    mail = smtp.sent[0]
    assert mail["Subject"] == MSG.subject
    assert mail["From"] == "me@gmail.com"
    assert mail["To"] == "me@gmail.com, b@x.in"
    assert mail["X-Priority"] == "1"
    assert "https://www.amazon.in/dp/B0X" in mail.get_content()


def test_email_starttls():
    EmailNotifier("smtp.office365.com", 587, "u", "p", "u@x.in", ["a@x.in"], security="starttls").send(MSG)
    assert FakeSMTP.instances[0].started_tls


def test_email_validation():
    with pytest.raises(NotifyError):
        EmailNotifier("h", 465, "u", "p", "f", [], security="ssl")
    with pytest.raises(NotifyError):
        EmailNotifier("h", 465, "u", "p", "f", ["a@x"], security="tls1.3")


def test_callmebot():
    session = FakeSession(FakeResponse(200, "<html><p>Message queued. You will receive it in a few seconds.</p></html>"))
    CallMeBotNotifier("+919876543210", "123456", session=session).send(MSG)
    method, url, kwargs = session.calls[0]
    assert method == "GET" and url == "https://api.callmebot.com/whatsapp.php"
    assert kwargs["params"]["phone"] == "+919876543210"
    assert kwargs["params"]["apikey"] == "123456"
    assert kwargs["params"]["text"].startswith("*🎮 IN STOCK")


@pytest.mark.parametrize(
    "response",
    [FakeResponse(200, "<p>APIKey is invalid. Please check it.</p>"), FakeResponse(500, "oops")],
)
def test_callmebot_failures(response):
    with pytest.raises(NotifyError):
        CallMeBotNotifier("+919876543210", "bad", session=FakeSession(response)).send(MSG)


def test_twilio_text_and_template():
    session = FakeSession(FakeResponse(201, "{}"))
    TwilioNotifier("AC123", "tok", "+14155238886", "+919876543210", session=session).send(MSG)
    _, url, kwargs = session.calls[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert kwargs["auth"] == ("AC123", "tok")
    assert kwargs["data"]["From"] == "whatsapp:+14155238886"
    assert kwargs["data"]["To"] == "whatsapp:+919876543210"
    assert "Buy:" in kwargs["data"]["Body"]

    session = FakeSession(FakeResponse(201, "{}"))
    TwilioNotifier("AC123", "tok", "whatsapp:+1415", "+91987", content_sid="HX1", session=session).send(MSG)
    data = session.calls[0][2]["data"]
    assert data["ContentSid"] == "HX1" and json.loads(data["ContentVariables"]) == {"1": "PSN in stock"}
    assert "Body" not in data


def test_twilio_error():
    with pytest.raises(NotifyError, match="HTTP 401"):
        TwilioNotifier("AC1", "t", "+1", "+91", session=FakeSession(FakeResponse(401, "bad auth"))).send(MSG)


def test_meta_text_and_template():
    session = FakeSession()
    MetaCloudNotifier("TOKEN", "1234", "+91 98765 43210", session=session).send(MSG)
    _, url, kwargs = session.calls[0]
    assert url == "https://graph.facebook.com/v25.0/1234/messages"
    assert kwargs["headers"]["Authorization"] == "Bearer TOKEN"
    assert kwargs["json"]["to"] == "919876543210"
    assert kwargs["json"]["type"] == "text"

    n = MetaCloudNotifier("T", "1", "91987", template_name="stock_alert", template_language="en")
    payload = n.payload(MSG)
    assert payload["type"] == "template"
    assert payload["template"]["components"][0]["parameters"][0]["text"] == "PSN in stock"

    n = MetaCloudNotifier("T", "1", "91987", template_name="hello_world", template_body_param=False)
    assert "components" not in n.payload(MSG)["template"]


class Flaky:
    name = "flaky"

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def send(self, message):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary")


def test_dispatcher_retries_then_succeeds():
    flaky, broken = Flaky(1), Flaky(99)
    broken.name = "broken"
    d = Dispatcher([flaky, broken], retries=3, retry_delay=0)
    assert [(n.name, ok) for n, ok in d.send_and_wait(MSG)] == [("flaky", True), ("broken", False)]
    d.close()
    # flaky: one failure + one success, then the "broken could not deliver" notice.
    assert flaky.calls == 3 and broken.calls == 3


def test_dispatcher_dry_run_sends_nothing():
    flaky = Flaky(0)
    d = Dispatcher([flaky], dry_run=True)
    assert d.send(MSG) == []
    d.close()
    assert flaky.calls == 0


def test_callmebot_error_does_not_leak_apikey():
    import requests

    class Exploding(FakeSession):
        def get(self, url, **kwargs):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?apikey=SECRET123")

    with pytest.raises(NotifyError) as info:
        CallMeBotNotifier("+919876543210", "SECRET123", session=Exploding()).send(MSG)
    assert "SECRET123" not in str(info.value)


@pytest.mark.parametrize(
    "reply, meaning",
    [
        ("<p>Your Account is Paused due to technical issues. Please send the word \"resume\" to the bot</p>",
         "send the word resume"),
        ("<p>You have reached the limit of 50 messages per 240 minutes</p>", "limit is used up"),
        ("<p>Something new and unexpected</p>", "unexpected CallMeBot reply"),
    ],
)
def test_callmebot_problems_are_not_mistaken_for_success(reply, meaning):
    with pytest.raises(NotifyError, match=meaning):
        CallMeBotNotifier("+972521234567", "1", session=FakeSession(FakeResponse(200, reply))).send(MSG)


class Named:
    def __init__(self, name, rationed, fail=False):
        self.name, self.rationed, self.fail, self.sent = name, rationed, fail, []

    def send(self, message):
        if self.fail:
            raise NotifyError("account is paused")
        self.sent.append(message)


def test_routine_messages_spare_rationed_channels():
    from scanamzn.notifiers import INFO

    email, whatsapp = Named("email", False), Named("whatsapp/callmebot", True)
    d = Dispatcher([email, whatsapp], retry_delay=0)
    d.send_and_wait(Message("daily", "b", "s", kind=INFO))
    d.send_and_wait(Message("IN STOCK", "b", "s"))
    d.close()
    assert [m.subject for m in email.sent] == ["daily", "IN STOCK"]
    assert [m.subject for m in whatsapp.sent] == ["IN STOCK"]

    # With WhatsApp as the only channel, routine messages still go out.
    only = Named("whatsapp/callmebot", True)
    d = Dispatcher([only], retry_delay=0)
    d.send_and_wait(Message("daily", "b", "s", kind=INFO))
    d.close()
    assert [m.subject for m in only.sent] == ["daily"]


def test_failed_alert_is_reported_on_the_other_channels():
    email, whatsapp = Named("email", False), Named("whatsapp/callmebot", True, fail=True)
    d = Dispatcher([email, whatsapp], retries=2, retry_delay=0)
    d.send_and_wait(Message("🎮 IN STOCK: PSN ₹1000", "Buy: link", "s"))
    d.close()  # waits for the follow-up notice
    subjects = [m.subject for m in email.sent]
    assert subjects[0] == "🎮 IN STOCK: PSN ₹1000"
    assert subjects[1] == "⚠️ whatsapp/callmebot could not deliver an alert"
    assert "account is paused" in email.sent[1].body and "Buy: link" in email.sent[1].body
