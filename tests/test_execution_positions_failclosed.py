"""Fail-closed position reads in the trading job.

The cached Supabase snapshot is the sizing basis. Before this change
dashboard.supabase_client.fetch_positions returned [] on any error, and step 6 read
[] as "first ever run" -- so a failed read became an empty account and the next step
sized the entire target book as orders. A read failure is not an empty book.

These tests drive the real _run(), stopping at the order submission boundary, so
"no orders placed" is asserted on the submit_orders call itself rather than inferred
from a later state write.
"""

from __future__ import annotations

from datetime import date as _date

import pytest

import execution.alpaca_paper as ap
import dashboard.supabase_client as sb
import execution.calendar_utils as cal
import scripts.run_execution as run_mod
from execution.daily_summary import FAIL, RunSummary, body_for, subject_for

RUN_DATE = _date(2026, 11, 10)


class _Today(_date):
    @classmethod
    def today(cls):
        return RUN_DATE


class _StopAfterSubmit(Exception):
    """Ends _run at submit_orders, which is the boundary these tests watch."""


class _Order:
    """Just enough of an OrderSpec for the guard-count line before submission."""

    guard_status = "PENDING"


def _settings():
    return {
        "signal_as_of_date": RUN_DATE.isoformat(),
        "signal_target_weights": '{"SPY": 1.0}',
        "signal_close_prices": '{"SPY": 100.0}',
        "live_nav": None,
    }


@pytest.fixture
def harness(monkeypatch):
    """Everything before step 6 stubbed; the position read is left to the test."""
    submitted: list[tuple] = []

    monkeypatch.setattr(run_mod, "date", _Today)
    monkeypatch.setattr(cal, "is_trading_day", lambda day: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, day: False)
    monkeypatch.setattr(cal, "trading_days_elapsed", lambda a, b: 0)

    settings = _settings()
    monkeypatch.setattr(sb, "get_setting", lambda key: settings.get(key))
    monkeypatch.setattr(sb, "fetch_decision_for_date", lambda day: "approve")
    monkeypatch.setattr(sb, "get_auto_approve", lambda: True)

    monkeypatch.setattr(ap, "DRY_RUN_DEFAULT", False)
    monkeypatch.setattr(ap, "connect", lambda dry_run=False: object())
    monkeypatch.setattr(ap, "get_live_nav", lambda client: 100_000.0)
    monkeypatch.setattr(ap, "check_position_drift", lambda *a, **k: {})
    monkeypatch.setattr(ap, "shorts_requiring_check", lambda orders: [])
    monkeypatch.setattr(ap, "compute_delta_orders", lambda *a, **k: [_Order()])
    monkeypatch.setattr(ap, "apply_guards", lambda orders, **k: orders)

    def _submit(client, orders, close_prices=None):
        submitted.append((orders, close_prices))
        raise _StopAfterSubmit()

    monkeypatch.setattr(ap, "submit_orders", _submit)
    monkeypatch.delenv(run_mod.FIRST_RUN_ENV, raising=False)
    return submitted


def _summary() -> RunSummary:
    return RunSummary(job="run_execution", run_date=RUN_DATE.isoformat())


def test_a_positions_read_error_places_no_orders(harness, monkeypatch) -> None:
    def _unreadable(latest_only=True, **kwargs):
        assert kwargs.get("raise_on_error") is True, (
            "the run must ask for the raising read, or a fault looks like an empty book"
        )
        raise RuntimeError("Supabase read failed: ConnectionError: timed out")

    monkeypatch.setattr(sb, "fetch_positions", _unreadable)
    summary = _summary()

    code = run_mod._run(summary)

    assert code == 1
    assert harness == [], "a failed read must not reach submit_orders"
    assert summary.status == FAIL
    assert summary.failure == (
        "positions read failed: no orders placed "
        "(RuntimeError: Supabase read failed: ConnectionError: timed out)"
    )

    body = body_for(summary)
    assert "[FAILED] positions read failed: no orders placed" in body, body
    assert subject_for(summary).startswith("[FAIL]"), subject_for(summary)


def test_an_empty_book_without_the_flag_is_refused(harness, monkeypatch) -> None:
    monkeypatch.setattr(sb, "fetch_positions", lambda latest_only=True, **k: [])
    monkeypatch.setattr(
        ap, "get_current_positions",
        lambda client, dry_run=False: pytest.fail("an empty read must not fall back"),
    )
    summary = _summary()

    code = run_mod._run(summary)

    assert code == 1
    assert harness == [], "an unannounced empty snapshot must place no orders"
    assert run_mod.FIRST_RUN_ENV in summary.failure
    assert summary.status == FAIL


def test_an_empty_book_with_the_explicit_flag_proceeds(harness, monkeypatch) -> None:
    monkeypatch.setattr(sb, "fetch_positions", lambda latest_only=True, **k: [])
    monkeypatch.setattr(
        ap, "get_current_positions", lambda client, dry_run=False: {"SPY": 1_000.0}
    )
    monkeypatch.setenv(run_mod.FIRST_RUN_ENV, "1")
    summary = _summary()

    with pytest.raises(_StopAfterSubmit):
        run_mod._run(summary)

    assert len(harness) == 1, "the explicit first run reached order submission"
    assert len(harness[0][0]) == 1


def test_a_failed_live_read_on_a_first_run_still_places_no_orders(
    harness, monkeypatch
) -> None:
    """The flag authorises an empty snapshot, not an unreadable broker."""
    monkeypatch.setattr(sb, "fetch_positions", lambda latest_only=True, **k: [])

    def _unreadable(client, dry_run=False):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(ap, "get_current_positions", _unreadable)
    monkeypatch.setenv(run_mod.FIRST_RUN_ENV, "1")
    summary = _summary()

    code = run_mod._run(summary)

    assert code == 1
    assert harness == []
    assert "live position read failed: ConnectionError" in summary.failure


def test_get_live_book_does_not_turn_a_broker_error_into_an_empty_book() -> None:
    class _Broken:
        def get_all_positions(self):
            raise ConnectionError("read timed out")

    with pytest.raises(ConnectionError):
        ap.get_current_positions(_Broken(), dry_run=False)


def test_the_drift_check_also_propagates_a_broker_error() -> None:
    """A second read on the order path must not read as 'no drift, empty book'."""
    class _Broken:
        def get_all_positions(self):
            raise ConnectionError("read timed out")

    with pytest.raises(ConnectionError):
        ap.check_position_drift(_Broken(), {"SPY": 1.0}, dry_run=False)


def test_fetch_positions_can_still_answer_an_empty_table() -> None:
    """The raising read still returns [] for a table with no rows: empty is not error."""
    class _Resp:
        data = []

    class _Query:
        def select(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            return _Resp()

    class _Table:
        def select(self, *a, **k):
            return _Query()

    class _Client:
        def table(self, name):
            return _Table()

    import dashboard.supabase_client as mod

    previous = mod._client
    mod._client = _Client()
    try:
        assert mod.fetch_positions(latest_only=True, raise_on_error=True) == []
    finally:
        mod._client = previous

