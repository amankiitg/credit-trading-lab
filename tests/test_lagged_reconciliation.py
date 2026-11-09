"""Lagged reconciliation: the evening email reports the PREVIOUS session's residual.

The portfolio-history endpoint labels each close with the FOLLOWING calendar day.
At 17:30 ET on session D the point labelled D+1 does not exist, so D's residual is
never measurable by the run that measures D. The point labelled D is D-1's close,
and it is published by then. Tonight's residual therefore belongs to D-1, and the
email says which session it belongs to.

What is reconciled for D-1 is what was PUBLISHED for it: its book total comes from
the stored sleeve rows, and only its traded part is read again (from the fills).
"""

from __future__ import annotations

import pandas as pd
import pytest

import execution.alpaca_paper as ap
import risk.execution_pnl as ep
import risk.sleeve_pnl as sp
from execution.daily_summary import RunSummary, body_for

THU, FRI, MON, TUE, WED = (
    "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11",
)
SESSIONS = [THU, FRI, MON]


class _NoBrokerCalls:
    def __getattr__(self, name):  # pragma: no cover - failing loudly is the point
        raise AssertionError(f"the test tried to call the broker: {name}")


def _equity(**by_stamp) -> dict[str, float]:
    return {stamp.replace("_", "-"): float(value) for stamp, value in by_stamp.items()}


def _fills(monkeypatch, rows: list[dict] | None = None) -> None:
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, session: rows or [])


# ------------------------------------------------------------------ Mon -> Fri

def test_monday_evening_reconciles_friday(monkeypatch) -> None:
    _fills(monkeypatch)
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{"2026-11-06": 100_000.0, "2026-11-07": 101_000.0}),
    )

    report = ep.record_prior_reconciliation(
        session=FRI,
        close={"SPY": 100.0},
        sessions=SESSIONS,
        book_total=190.53,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["session"] == FRI
    assert report["reason"] is None
    assert report["reconciliation"]["nav_change"] == pytest.approx(1_000.0)
    assert report["reconciliation"]["expected"] == pytest.approx(190.53)
    assert report["reconciliation"]["gap"] == pytest.approx(809.47)
    assert report["reconciliation"]["warn"] is True


# ------------------------------------------------------------------ Tue -> Mon

def test_tuesday_evening_reconciles_monday(monkeypatch) -> None:
    _fills(monkeypatch)
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{"2026-11-07": 100_000.0, "2026-11-10": 102_000.0}),
    )

    report = ep.record_prior_reconciliation(
        session=MON,
        close={"SPY": 100.0},
        sessions=[FRI, MON, TUE],
        book_total=50.0,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["session"] == MON
    assert report["reconciliation"]["nav_change"] == pytest.approx(2_000.0)
    assert report["reconciliation"]["gap"] == pytest.approx(1_950.0)


# ------------------------------------------------------------------ holiday gap

def test_a_holiday_gap_reconciles_the_last_real_session(monkeypatch) -> None:
    """Wed 2026-11-11 is a market holiday; the prior session is Mon 11-09."""
    holiday_sessions = [FRI, MON, WED]

    assert sp.session_before(holiday_sessions, WED) == MON
    assert TUE not in holiday_sessions
    # calendar check, not memory: 11-11 is a Wednesday, 11-10 a Tuesday
    assert pd.Timestamp(WED).weekday() == 2
    assert pd.Timestamp(TUE).weekday() == 1

    _fills(monkeypatch)
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{"2026-11-07": 100_000.0, "2026-11-10": 101_500.0}),
    )

    report = ep.record_prior_reconciliation(
        session=MON,
        close={"SPY": 100.0},
        sessions=holiday_sessions,
        book_total=10.0,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["session"] == MON
    assert report["reconciliation"]["nav_change"] == pytest.approx(1_500.0)
    assert report["reconciliation"]["gap"] == pytest.approx(1_490.0)


def test_session_before_walks_the_calendar_list_not_calendar_days(monkeypatch) -> None:
    assert sp.session_before([FRI, MON], MON) == FRI
    assert sp.session_before([MON], MON) is None
    assert sp.session_before([FRI], MON) is None


# ------------------------------------------------------------------ the email line

def test_the_email_dates_the_residual_to_the_session_it_belongs_to(monkeypatch) -> None:
    _fills(monkeypatch)
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{"2026-11-06": 100_000.0, "2026-11-07": 100_210.16}),
    )
    report = ep.record_prior_reconciliation(
        session=FRI,
        close={"SPY": 100.0},
        sessions=[THU, FRI, MON],
        book_total=190.53,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    summary = RunSummary(job="run_signal", run_date=MON)
    summary.record_prior_reconciliation(report)

    body = body_for(summary)
    assert "Reconciliation (2026-11-06): residual $19.63" in body, body
    # The run date is not the session whose residual this is; it must not be implied.
    assert "Reconciliation: residual not available" not in body, body


def test_an_unmeasurable_prior_residual_names_its_date() -> None:
    summary = RunSummary(job="run_signal", run_date=MON)
    summary.record_prior_reconciliation(
        {
            "session": FRI,
            "reconciliation": None,
            "reason": f"no stored sleeve P&L for {FRI}",
        }
    )

    body = body_for(summary)
    assert (
        f"Reconciliation ({FRI}): residual not available "
        f"(no stored sleeve P&L for {FRI})"
    ) in body, body


def test_the_current_session_still_says_its_close_is_not_published(monkeypatch) -> None:
    """The 'not yet published' handling for D is kept, not bypassed."""
    _fills(monkeypatch)
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, session: _equity(**{"2026-11-07": 100_000.0}),
    )

    report = ep.record_execution_component(
        session=MON,
        close_now={"SPY": 100.0},
        sessions=[FRI, MON],
        carry_total=0.0,
        book_total=0.0,
        carry_short=0.0,
        client=_NoBrokerCalls(),
    )

    assert report["reconciliation"] is None
    assert report["reconciliation_reason"] == "equity close not yet published"


# ------------------------------------------------------------------ step 8 wiring

def _step_eight(monkeypatch, *, prior_rows):
    """Step 8 with every reader stubbed; the prior block is left real."""
    import dashboard.supabase_client as sb
    import signals.dividends as div_mod
    import signals.etf_universe as etf

    trade_date, prior_date = MON, FRI
    index = pd.DatetimeIndex([prior_date, trade_date])
    close = pd.DataFrame({"TLT": [81.84, 81.73]}, index=index)
    written: list[list[dict]] = []

    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(
        div_mod, "load_dividend_matrix",
        lambda tickers, close_index, **k: pd.DataFrame({"TLT": [0.0, 0.0]}, index=index),
    )
    monkeypatch.setattr(div_mod, "dividends_as_of", lambda raw_dir=None: trade_date)
    monkeypatch.setattr(
        sb, "fetch_position_snapshot",
        lambda d, **k: [{"ticker": "TLT", "shares": -330.0}],
    )
    monkeypatch.setattr(sb, "write_daily_sleeve_pnl", lambda rows: written.append(rows) or True)
    monkeypatch.setattr(sb, "execution_column_exists", lambda: False)
    monkeypatch.setattr(sb, "fetch_daily_sleeve_pnl", prior_rows)
    monkeypatch.setattr(
        sp, "record_execution_component",
        lambda **kw: {"status": "ok", "total": 0.0, "by_sleeve": {}, "fill_count": 0,
                      "reconciliation": None, "reconciliation_reason": "not a real day"},
    )
    return written, trade_date, prior_date


def test_step_eight_uses_the_published_book_and_prior_close(monkeypatch) -> None:
    stored = [
        {"trade_date": FRI, "sleeve": "rates", "book_total": 190.53, "carry_pnl": -12.5,
         "price_pnl": 203.03},
        {"trade_date": FRI, "sleeve": "credit", "book_total": 190.53, "carry_pnl": 0.0,
         "price_pnl": 0.0},
    ]
    written, trade_date, prior_date = _step_eight(
        monkeypatch, prior_rows=lambda limit=2000, **k: list(stored)
    )

    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return {"session": kwargs["session"], "reconciliation": {"gap": 1.0},
                "reason": None}

    monkeypatch.setattr(sp, "record_prior_reconciliation", _spy)

    report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["written"] is True
    assert report["prior_reconciliation"]["session"] == prior_date
    assert captured["session"] == prior_date
    assert captured["book_total"] == pytest.approx(190.53)
    # The stored carry's negative part is the conservative short-leg add-back.
    assert captured["carry_short"] == pytest.approx(-12.5)
    assert captured["close"] == {"TLT": pytest.approx(81.84)}


def test_a_missing_stored_row_is_named_not_invented(monkeypatch) -> None:
    _written, trade_date, prior_date = _step_eight(
        monkeypatch, prior_rows=lambda limit=2000, **k: []
    )
    monkeypatch.setattr(
        sp, "record_prior_reconciliation",
        lambda **kw: pytest.fail("nothing stored means nothing to reconcile"),
    )

    report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["prior_reconciliation"]["reconciliation"] is None
    assert report["prior_reconciliation"]["reason"] == (
        f"no stored sleeve P&L for {prior_date}"
    )


def test_a_stored_row_read_failure_is_named(monkeypatch) -> None:
    def _boom(limit=2000, **k):
        raise RuntimeError("Supabase read failed: ConnectionError: timed out")

    _written, trade_date, _prior = _step_eight(monkeypatch, prior_rows=_boom)
    monkeypatch.setattr(
        sp, "record_prior_reconciliation",
        lambda **kw: pytest.fail("a failed read is not an empty table"),
    )

    report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["prior_reconciliation"]["reconciliation"] is None
    assert "could not be read" in report["prior_reconciliation"]["reason"]
    assert "ConnectionError" in report["prior_reconciliation"]["reason"]
