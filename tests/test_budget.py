import pytest

from scanamzn.budget import WINDOW, RequestBudget
from scanamzn.fetcher import FetchError, Fetcher

from .conftest import FakeClock


def test_allows_n_requests_a_minute_then_says_how_long_to_wait(tmp_path):
    clock = FakeClock(1000.0)
    budget = RequestBudget(tmp_path / "times", per_minute=3, clock=clock)
    assert [budget._reserve() for _ in range(3)] == [0.0, 0.0, 0.0]
    clock.advance(10)
    delay = budget._reserve()
    assert WINDOW - 10 <= delay <= WINDOW - 10 + 0.1
    clock.advance(delay)
    assert budget._reserve() == 0.0


def test_is_shared_between_processes_through_the_file(tmp_path):
    clock = FakeClock(1000.0)
    monitor = RequestBudget(tmp_path / "times", per_minute=4, clock=clock)
    selftest = RequestBudget(tmp_path / "times", per_minute=4, clock=clock)
    for _ in range(3):
        assert monitor._reserve() == 0.0
    assert selftest._reserve() == 0.0
    assert selftest._reserve() > 0 and monitor._reserve() > 0


def test_acquire_waits_and_can_be_cancelled(tmp_path):
    clock = FakeClock(1000.0)
    budget = RequestBudget(tmp_path / "times", per_minute=1, clock=clock)
    waits = []
    assert budget.acquire(lambda t: waits.append(t))
    assert budget.acquire(lambda t: (waits.append(t), clock.advance(t))[0]) and len(waits) == 1
    assert budget.acquire(lambda t: True) is False  # e.g. the monitor is stopping


def test_no_limit_and_unusable_file(tmp_path):
    assert RequestBudget(tmp_path / "times", per_minute=0)._reserve() == 0.0
    blocker = tmp_path / "file"
    blocker.write_text("x")
    budget = RequestBudget(blocker / "times", per_minute=1, clock=FakeClock())  # parent is a file
    assert budget._reserve() == 0.0 and budget._reserve() > 0  # falls back to counting in-process


def test_fetcher_waits_for_the_budget_before_any_request(tmp_path):
    clock = FakeClock(1000.0)
    budget = RequestBudget(tmp_path / "times", per_minute=1, clock=clock)
    fetcher = Fetcher(backend="requests", budget=budget)
    sent = []

    class Session:
        def get(self, url, **kw):
            sent.append(url)
            return type("R", (), {"url": url, "status_code": 200, "text": "ok"})()

        def close(self):
            pass

    fetcher._new_session = lambda profile: Session()
    fetcher.wait = lambda t: clock.advance(t)
    fetcher.get("https://www.amazon.in/dp/B0X")
    fetcher.get("https://www.amazon.in/dp/B0Y")
    assert sent == ["https://www.amazon.in/dp/B0X", "https://www.amazon.in/dp/B0Y"] and clock() >= 1000 + WINDOW
    fetcher.wait = lambda t: True  # stopping
    with pytest.raises(FetchError):
        fetcher.get("https://www.amazon.in/dp/B0Z")
    assert len(sent) == 2


def test_works_without_fcntl(monkeypatch, tmp_path):
    from scanamzn import budget as budget_mod

    monkeypatch.setattr(budget_mod, "fcntl", None)  # e.g. Windows
    b = budget_mod.RequestBudget(tmp_path / "times", per_minute=1, clock=FakeClock())
    assert b.path is None and b._reserve() == 0.0 and b._reserve() > 0


# --- request profiles --------------------------------------------------------------------


class _Session:
    """Stands in for requests.Session: answers from a script, records requests."""

    def __init__(self, answers, sent):
        self.answers, self.sent, self.headers, self.cookies = answers, sent, {}, _Jar()

    def get(self, url, **kw):
        self.sent.append((url, dict(self.headers)))
        status = self.answers(url, self.headers)
        text = "<html>challenge.js</html>" if status == 202 else '<span id="productTitle">x</span>'
        return type("R", (), {"url": url, "status_code": status, "text": text})()

    def close(self):
        pass


class _Jar:
    def set(self, *a, **k):
        pass


def _profiled_fetcher(monkeypatch, tmp_path, answers):
    import requests

    from scanamzn import fetcher as fetcher_mod

    sent = []
    monkeypatch.setattr(requests, "Session", lambda: _Session(answers, sent))
    monkeypatch.setattr(fetcher_mod.requests, "Session", lambda: _Session(answers, sent))
    f = fetcher_mod.Fetcher(backend="requests", profile_file=tmp_path / "request-profile")
    f.wait = lambda t: None
    return f, sent


def test_legacy_profile_is_the_old_fetcher_with_its_home_page_visit(monkeypatch, tmp_path):
    f, sent = _profiled_fetcher(monkeypatch, tmp_path, lambda url, h: 200)
    assert f.profile == "legacy"
    f.get("https://www.amazon.in/dp/B0X")
    assert [u for u, _ in sent] == ["https://www.amazon.in/", "https://www.amazon.in/dp/B0X"]
    headers = sent[-1][1]
    assert "Chrome/140.0.0.0" in headers["User-Agent"] and "sec-ch-ua" not in headers


def test_switches_request_style_after_two_bot_checks_and_remembers_what_works(monkeypatch, tmp_path):
    from scanamzn import fetcher as fetcher_mod

    # Amazon challenges the legacy and plain styles, and lets client hints through.
    f, sent = _profiled_fetcher(
        monkeypatch, tmp_path, lambda url, h: 200 if "sec-ch-ua" in h or url.endswith(".in/") else 202
    )
    seen = []
    for _ in range(6):
        page = f.get("https://www.amazon.in/dp/B0X")
        seen.append((f.profile, page.status_code))
        if page.status_code == 202:
            f.reset()  # what the monitor does after a bot check
    assert seen[:5] == [("legacy", 202), ("legacy", 202), ("plain", 202), ("plain", 202), ("client-hints", 200)]
    assert f.stats["client-hints"]["ok"] >= 1 and f.stats["legacy"]["blocked"] == 2
    assert (tmp_path / "request-profile").read_text().strip() == "client-hints"
    # A restart starts with the style that worked.
    again = fetcher_mod.Fetcher(backend="requests", profile_file=tmp_path / "request-profile")
    assert again.profile == "client-hints"


def test_one_bot_check_then_success_keeps_the_style(monkeypatch, tmp_path):
    answers = iter([202, 200, 200])  # e.g. the first request of a fresh session is challenged
    f, sent = _profiled_fetcher(monkeypatch, tmp_path, lambda url, h: 200 if url.endswith(".in/") else next(answers))
    f.get("https://www.amazon.in/dp/B0X")
    f.reset()
    f.get("https://www.amazon.in/dp/B0X")
    f.get("https://www.amazon.in/dp/B0X")
    assert f.profile == "legacy" and f.stats["legacy"] == {"ok": 2, "blocked": 1, "errors": 0}
