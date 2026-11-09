"""Weekend pairing, and the 17:30 ET run whose close stamp does not exist yet.

The equity history labels each close with the FOLLOWING calendar day. Friday's close
is stamped Saturday, Monday's close is stamped Tuesday. Two consequences, and this
file pins both:

  (a) Friday-to-Monday. The pair must be Monday's close minus Friday's close. A
      weekend date must never be treated as a session: asking for "the first stamp
      after Saturday" returns Monday's close, so a weekend key silently pairs the
      wrong days and produces a one-day move where a three-day one belongs.

  (b) 17:30 ET on the session being measured. run_signal fires at 21:30 UTC, which
      is 17:30 EDT / 16:30 EST -- after the 16:00 close, but the close of day D is
      stamped D+1, and at 17:30 on D that point does not exist. The reconciliation
      must say "residual not available (equity close not yet published)" and must not
      fall back to an earlier stamp: the earlier stamp is yesterday's close, and
      subtracting it from nothing is how a wrong-day pair gets invented.

The tests drive the real code paths (equity_pair_reason, session_equity_change,
record_execution_component, body_for); only the broker reads are replaced, because
there is no reason to call Alpaca to test arithmetic on a mapping.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import execution.alpaca_paper as ap
import risk.execution_pnl as ep
from execution.daily_summary import RunSummary, body_for

NEW_YORK = ZoneInfo("America/New_York")

# Real NYSE sessions around the weekend in question: Thu, Fri, Mon, Tue.
THU, FRI, MON, TUE = "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"
SESSIONS = [THU, FRI, MON, TUE]

# The stamps the endpoint actually writes: one calendar day after each session.
FRI_CLOSE_STAMP = "2026-11-07"
MON_CLOSE_STAMP = "2026-11-10"


class _NoBrokerCalls:
    """A client that must not be used: every read in these tests is injected."""

    def __getattr__(self, name):  # pragma: no cover - failing loudly is the point
        raise AssertionError(f"the test tried to call the broker: {name}")


def _equity(**by_stamp) -> dict[str, float]:
    return {stamp.replace("_", "-"): float(value) for stamp, value in by_stamp.items()}


# ------------------------------------------------------------------ (a) Fri -> Mon


def test_a_friday_to_monday_pair_uses_the_two_real_closes() -> None:
    """Friday 100,000 -> Monday 101,000 is +1,000 over the weekend, not over one day."""
    equity = _equity(**{FRI_CLOSE_STAMP: 100_000.0, MON_CLOSE_STAMP: 101_000.0})

    # Each close is the first stamp strictly after its session, which is the
    # following calendar day -- Saturday for Friday, Tuesday for Monday.
    assert ep.close_equity(equity, FRI) == 100_000.0
    assert ep.close_equity(equity, MON) == 101_000.0

    assert ep.session_equity_change(equity, SESSIONS, MON) == pytest.approx(1_000.0)
    assert ep.equity_pair_reason(equity, SESSIONS, MON) is None


def test_a_weekend_date_is_refused_rather_than_paired_with_mondays_close() -> None:
    """The trap: "first stamp after Saturday" IS Monday's close.

    close_equity() is arithmetic on the series and will happily return it. The reason
    helper is what stops a weekend key from being measured at all, which is why the
    run path asks it before it believes a change.
    """
    equity = _equity(**{FRI_CLOSE_STAMP: 100_000.0, MON_CLOSE_STAMP: 101_000.0})

    assert ep.close_equity(equity, "2026-11-07") == 101_000.0  # would be wrong to use
    assert ep.session_equity_change(equity, SESSIONS, "2026-11-07") is None
    assert ep.equity_pair_reason(equity, SESSIONS, "2026-11-07") == (
        "2026-11-07 is not a trading session in the calendar"
    )


def test_the_weekend_dates_really_are_a_weekend() -> None:
    """The fixture above is checked against the calendar, not against my memory.

    Weekday numbers rather than exchange_calendars: the suite must not need a heavy
    calendar package to assert that Saturday is Saturday, and the session list the
    run uses comes from the close cache anyway. The holiday test belongs to the
    calendar module, not here.
    """
    day = lambda text: datetime.fromisoformat(text).weekday()  # noqa: E731

    assert day(THU) == 3 and day(FRI) == 4  # Thursday, Friday
    assert day("2026-11-07") == 5           # Saturday
    assert day("2026-11-08") == 6           # Sunday
    assert day(MON) == 0                    # Monday
    # Friday to Monday is one NYSE session apart in the list the run is given, which
    # is what the email means by "the previous session".
    assert SESSIONS[SESSIONS.index(MON) - 1] == FRI


# ------------------------------------------------------------------ (b) 17:30 ET


def test_at_1730_et_todays_close_stamp_does_not_exist_yet() -> None:
    """The timing, stated as a fact about the calendar rather than a comment.

    21:30 UTC is 17:30 New York in November. The session is today, and its close is
    stamped tomorrow, so the map cannot hold it: there is nothing to subtract yet.
    """
    now = datetime(2026, 11, 9, 17, 30, tzinfo=NEW_YORK)
    assert now.hour == 17 and now.minute == 30
    session = now.date().isoformat()
    assert session == MON
    # The stamp the close of this session will carry, one calendar day later.
    assert (now.date() + timedelta(days=1)).isoformat() == TUE

    # What the broker has written by this time: Friday's close (stamped Saturday).
    equity = _equity(**{FRI_CLOSE_STAMP: 100_000.0})
    assert ep.close_equity(equity, MON) is None
    assert ep.session_equity_change(equity, SESSIONS, MON) is None
    assert ep.equity_pair_reason(equity, SESSIONS, MON) == "equity close not yet published"


def test_a_same_day_stamp_is_not_used_as_todays_close() -> None:
    """Even if a point labelled 11-09 exists, it is not this session's close.

    Under the shifted convention a stamp on D is the close of D-1. Reading the stamp
    on D as D's close is the naive pairing the whole module exists to avoid, so the
    run refuses to measure rather than pair it.
    """
    equity = _equity(**{FRI_CLOSE_STAMP: 100_000.0, MON: 99_999.0})

    assert ep.close_equity(equity, MON) is None
    assert ep.equity_pair_reason(equity, SESSIONS, MON) == "equity close not yet published"


def test_the_email_says_residual_not_available_for_an_unpublished_close(monkeypatch) -> None:
    """End to end through the report and the renderer, on the 17:30 ET shape."""
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, session: [])
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{FRI_CLOSE_STAMP: 100_000.0}),
    )

    report = ep.record_execution_component(
        session=MON,
        close_now={"SPY": 752.0},
        sessions=SESSIONS,
        carry_total=0.0,
        book_total=190.53,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["status"] == "ok"  # the execution side was measured
    assert report["total"] == pytest.approx(0.0)
    assert report["reconciliation"] is None
    assert report["reconciliation_reason"] == "equity close not yet published"

    summary = RunSummary(job="run_signal", run_date=MON)
    summary.record_sleeve_pnl({"execution": report, "status": "ok", "written": True})
    body = body_for(summary)

    assert "residual not available (equity close not yet published)" in body, body
    # And nothing pretends to have paired two days.
    assert "vs equity change" not in body, body


def test_a_friday_to_monday_reconciliation_is_measured_on_the_real_pair(monkeypatch) -> None:
    """The weekend case end to end: +1,000 against a pipeline of +190.53 warns."""
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, session: [])
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(
            **{FRI_CLOSE_STAMP: 100_000.0, MON_CLOSE_STAMP: 101_000.0}
        ),
    )

    report = ep.record_execution_component(
        session=MON,
        close_now={"SPY": 752.0},
        sessions=SESSIONS,
        carry_total=0.0,
        book_total=190.53,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["reconciliation"] is not None
    assert report["reconciliation"]["nav_change"] == pytest.approx(1_000.0)
    assert report["reconciliation"]["expected"] == pytest.approx(190.53)
    assert report["reconciliation"]["gap"] == pytest.approx(809.47)
    assert report["reconciliation"]["warn"] is True


def test_a_failed_equity_read_is_named_not_reported_as_a_missing_close(monkeypatch) -> None:
    """The broad-except fix: a broker fault is not a calendar fact."""
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, session: [])

    def _unreadable(client, session):
        raise ap.EquityHistoryReadError(
            "equity history read failed: ConnectionError: read timed out"
        )

    monkeypatch.setattr(ap, "fetch_equity_history", _unreadable)

    report = ep.record_execution_component(
        session=MON,
        close_now={"SPY": 752.0},
        sessions=SESSIONS,
        carry_total=0.0,
        book_total=190.53,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["status"] == "ok"
    assert report["reconciliation"] is None
    assert report["reconciliation_reason"].startswith("equity history read failed:")

    summary = RunSummary(job="run_signal", run_date=MON)
    summary.record_execution_pnl(report)
    body = body_for(summary)
    assert "residual not available (equity history read failed: ConnectionError" in body, body
    assert "not yet published" not in body, body
