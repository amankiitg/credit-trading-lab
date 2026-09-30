"""The approve/reject window: three states, gated on the recorded execution run.

The pending-review finding was that the cutoff was invisible. The panel showed live
Approve and Reject buttons at every hour, including after the day's run had already
executed, so an approval written at 4pm read as success on screen and changed nothing.

The gate here is the run_execution row in cron_runs, never the clock: a cron can fire
late, and a scheduled time that has passed says nothing about whether the run has
happened.

`decision_window` is pure so the three states can be pinned directly, and the reader
that feeds it is tested against a stubbed PostgREST chain.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from dashboard.views.operational import (
    DECISION_STATE_LOCKED,
    DECISION_STATE_NO_SIGNAL,
    DECISION_STATE_OPEN,
    _today_utc,
    decision_window,
)

TODAY = "2026-09-30"
YESTERDAY = "2026-09-29"


def _state(
    *,
    today: str = TODAY,
    as_of: str | None = YESTERDAY,
    has_proposal: bool = True,
    last_run: str | None = YESTERDAY,
) -> str:
    """The state, asserting the reason is always populated: the panel renders it."""
    state, reason = decision_window(
        today=today,
        as_of_date=as_of,
        has_proposal=has_proposal,
        last_execution_run_date=last_run,
    )
    assert reason, "every state must explain itself, because the panel shows the reason"
    return state


# ------------------------------------------------------------------ open


def test_a_fresh_proposal_with_no_run_since_is_open() -> None:
    """The normal morning: yesterday's signal, and yesterday's own run is the newest.

    Yesterday's 14:30 run acted on the signal from the day before, so a last run date
    equal to the as-of date means this proposal is still waiting. Open is the only
    state that shows the buttons.
    """
    assert _state() == DECISION_STATE_OPEN


def test_a_weekend_stays_open() -> None:
    """Friday's signal is acted on by Monday's run, so Saturday and Sunday are open.

    A gate built on calendar days, or on "has any run happened since the signal", would
    close the window two days early and take away the only chance to reject it.
    """
    assert _state(today="2026-10-03", as_of="2026-10-02", last_run="2026-10-02") == DECISION_STATE_OPEN
    assert _state(today="2026-10-04", as_of="2026-10-02", last_run="2026-10-02") == DECISION_STATE_OPEN


def test_no_recorded_run_at_all_leaves_the_window_open() -> None:
    """An empty cron_runs is not a locked day; the decision is still the operator's."""
    assert _state(last_run=None) == DECISION_STATE_OPEN


def test_the_clock_alone_never_locks_the_window() -> None:
    """The scheduled time has passed and no run is recorded, so the window is open.

    This is the late-cron case the whole gate exists for.
    """
    assert _state(today=TODAY, as_of=YESTERDAY, last_run=YESTERDAY) == DECISION_STATE_OPEN


# ------------------------------------------------------------------ locked


def test_a_run_recorded_for_today_locks_the_window() -> None:
    assert _state(today=TODAY, as_of=YESTERDAY, last_run=TODAY) == DECISION_STATE_LOCKED


def test_locked_wins_over_a_lagged_signal() -> None:
    """Once today's run has happened, today is settled whatever the signal on file is.

    Seen with a stale signal and two runs behind it: yesterday's run acted on it and
    today's has gone too. The state that matters to the operator is that today is done.
    """
    assert _state(today=TODAY, as_of="2026-09-28", last_run=TODAY) == DECISION_STATE_LOCKED


def test_a_run_recorded_for_today_locks_even_when_the_signal_is_todays() -> None:
    """Same-day signal and run: the run has gone, so the decision is settled."""
    assert _state(today=TODAY, as_of=TODAY, last_run=TODAY) == DECISION_STATE_LOCKED


# ------------------------------------------------------------------ no signal


def test_no_proposal_means_no_signal() -> None:
    assert _state(has_proposal=False, last_run=None) == DECISION_STATE_NO_SIGNAL


def test_the_dash_sentinel_is_not_a_date() -> None:
    """Panel H uses an em dash when the signal cron has not written an as-of date."""
    assert _state(as_of="—") == DECISION_STATE_NO_SIGNAL
    assert _state(as_of=None) == DECISION_STATE_NO_SIGNAL


def test_an_already_acted_on_signal_is_not_fresh() -> None:
    """A run newer than the proposal, but not today's, means the proposal has gone.

    Without this the buttons would come back for a proposal that was executed
    yesterday, and an approval would report success while changing nothing.
    """
    assert _state(today="2026-10-01", as_of=YESTERDAY, last_run=TODAY) == DECISION_STATE_NO_SIGNAL


def test_the_same_proposal_flips_open_then_locked_as_the_run_lands() -> None:
    """The whole point in one sequence, with the proposal unchanged between the two."""
    before = _state(today=TODAY, as_of=YESTERDAY, last_run=YESTERDAY)
    after = _state(today=TODAY, as_of=YESTERDAY, last_run=TODAY)
    assert (before, after) == (DECISION_STATE_OPEN, DECISION_STATE_LOCKED)


# ------------------------------------------------------------------ the cron gate's reader


class _FakeTable:
    """The slice of the PostgREST chain fetch_last_cron_run uses, recording calls."""

    def __init__(self, rows: list[dict], calls: list) -> None:
        self._rows = rows
        self._calls = calls

    def select(self, *cols):
        self._calls.append(("select", *cols))
        return self

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        return self

    def order(self, col, desc=False):
        self._calls.append(("order", col, desc))
        return self

    def limit(self, n):
        self._calls.append(("limit", n))
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))


class _FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.calls: list = []
        self._rows = rows

    def table(self, name):
        self.calls.append(("table", name))
        return _FakeTable(self._rows, self.calls)


def _patch_client(monkeypatch, rows: list[dict]) -> _FakeClient:
    import dashboard.supabase_client as sb

    client = _FakeClient(rows)
    monkeypatch.setattr(sb, "get_supabase_client", lambda: client)
    return client


def test_the_gate_reads_the_newest_execution_run_only(monkeypatch) -> None:
    """It asks cron_runs for run_execution, newest first, and takes one row.

    Filtering on the job matters: run_signal records into this same table, so a signal
    row for today would otherwise read as "the execution run has happened" and would
    lock the panel on the evening before the run.
    """
    import dashboard.supabase_client as sb

    client = _patch_client(monkeypatch, [{"run_date": TODAY, "completed_at": "2026-09-30T14:31:10Z"}])

    row = sb.fetch_last_cron_run("run_execution")

    assert row == {"run_date": TODAY, "completed_at": "2026-09-30T14:31:10Z"}
    assert ("table", "cron_runs") in client.calls
    assert ("eq", "job_name", "run_execution") in client.calls
    assert ("order", "run_date", True) in client.calls
    assert ("limit", 1) in client.calls


def test_no_run_recorded_returns_none(monkeypatch) -> None:
    import dashboard.supabase_client as sb

    _patch_client(monkeypatch, [])

    assert sb.fetch_last_cron_run("run_execution") is None


def test_an_unreadable_gate_is_none_not_a_locked_day(monkeypatch) -> None:
    """Supabase down must not read as "already ran".

    That would hide the buttons on a day when the decision is still the operator's,
    which is worse than showing them for a run that has already gone: a hidden
    decision cannot be made, a late one can still be corrected.
    """
    import dashboard.supabase_client as sb

    monkeypatch.setattr(sb, "get_supabase_client", lambda: None)

    assert sb.fetch_last_cron_run("run_execution") is None


def test_a_failing_read_is_none_and_is_logged(monkeypatch, caplog) -> None:
    import dashboard.supabase_client as sb

    class _Boom:
        def table(self, name):
            raise ConnectionError("supabase unreachable")

    monkeypatch.setattr(sb, "get_supabase_client", lambda: _Boom())

    with caplog.at_level(logging.ERROR):
        assert sb.fetch_last_cron_run("run_execution") is None
    assert "fetch_last_cron_run" in caplog.text


# ------------------------------------------------------------------ the date basis


def test_today_is_the_utc_date(monkeypatch) -> None:
    """The run stamps its container's UTC date, so the gate compares on that basis.

    In a zone ahead of UTC the local date rolls over first. A gate built on the local
    date would close the window a day early for part of every day and open it a day
    late on the other side of the rollover.
    """
    utc_date = datetime.now(timezone.utc).date().isoformat()

    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14
    time.tzset()
    try:
        assert _today_utc() == utc_date
        local_date = datetime.now().date().isoformat()
        # Where the two dates differ, the function must not have used the local one.
        assert local_date == utc_date or _today_utc() != local_date
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_the_window_does_not_depend_on_the_calendar(monkeypatch) -> None:
    """Two days that are both weekends behave identically, with no trading calendar.

    The state machine takes dates it is given and compares them; whether a date is a
    session is the execution layer's business, not the panel's.
    """
    saturday = date.fromisoformat("2026-10-03")
    sunday = saturday + timedelta(days=1)
    friday = (saturday - timedelta(days=1)).isoformat()

    for day in (saturday, sunday):
        assert _state(today=day.isoformat(), as_of=friday, last_run=friday) == DECISION_STATE_OPEN
