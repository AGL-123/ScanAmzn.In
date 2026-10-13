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

    fetcher._new_session = lambda: Session()
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
