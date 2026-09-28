"""Tests for the post-close basis refresh and the summary P&L line -- sprint v9.6.

Two behaviours are pinned here:

  - the cached position snapshot and live_nav are refreshed from Alpaca on paths that
    do not trade, so the proposal and the next morning's execution are sized from a
    basis at most one session old;
  - the summary email reports the book's P&L as the move in live Alpaca NAV since the
    previous run, with turnover cost on its own line rather than folded into it.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from execution.daily_summary import RunSummary, body_for
from execution.snapshot import refresh_snapshot
from tests.test_paper_execution import _run_execution_with_stub

TODAY = date.today().isoformat()

# What the last patched refresh recorded. A module-level dict rather than a closure
# list so the assertion reads the same way from every test.
_LAST_REFRESH: dict = {"calls": 0}


def _capture():
    """A stand-in refresh_snapshot that records how many times it was called."""
    def _spy(**kw):
        _LAST_REFRESH["calls"] += 1
        _LAST_REFRESH["kwargs"] = kw
        return {"written": True, "nav": 101_000.0, "tickers": 8, "skipped": None}

    _LAST_REFRESH["calls"] = 0
    return _spy


def _stub_alpaca(monkeypatch, *, nav=101_000.0, book=None, connect_returns=None):
    """Point the snapshot refresh at a fake broker."""
    import execution.alpaca_paper as ap

    if book is None:
        book = {
            "SPY": {"notional": 6_600.0, "shares": 10.0},
            "LQD": {"notional": -2_200.0, "shares": -20.0},
        }
    client = MagicMock() if connect_returns is None else connect_returns
    calls: dict = {}

    def _connect(*args, **kwargs):
        calls["connect_kwargs"] = kwargs
        return client

    monkeypatch.setattr(ap, "connect", _connect)
    monkeypatch.setattr(ap, "get_live_nav", lambda c: nav)
    monkeypatch.setattr(ap, "get_live_book", lambda c, dry_run=False: dict(book))
    return calls


def _stub_supabase(monkeypatch):
    """Capture what the refresh writes."""
    import dashboard.supabase_client as sb

    written: dict = {"settings": {}, "positions": []}
    monkeypatch.setattr(
        sb, "set_setting",
        lambda k, v: written["settings"].__setitem__(k, v) or True,
    )
    monkeypatch.setattr(
        sb, "write_positions",
        lambda rows: written["positions"].extend(rows) or True,
    )
    return written


# ------------------------------------------------------------------ fix 2: the refresh

def test_the_refresh_writes_live_nav_and_share_counts(monkeypatch) -> None:
    _stub_alpaca(monkeypatch, nav=101_234.56)
    written = _stub_supabase(monkeypatch)

    report = refresh_snapshot(run_date=TODAY)

    assert report["skipped"] is None and report["written"] is True
    assert report["nav"] == pytest.approx(101_234.56)
    assert report["tickers"] == 2
    assert written["settings"]["live_nav"] == "101234.56"

    by_ticker = {r["ticker"]: r for r in written["positions"]}
    assert set(by_ticker) == {"SPY", "LQD"}
    # Share counts are the whole point of the refresh: the drift check compares them.
    assert by_ticker["SPY"]["shares"] == 10.0
    assert by_ticker["LQD"]["shares"] == -20.0
    assert by_ticker["LQD"]["signed_notional"] == -2_200.0
    assert by_ticker["LQD"]["side"] == "short"
    assert all(r["trade_date"] == TODAY for r in written["positions"])
    # Weights are against the NAV read at the same moment as the positions.
    assert by_ticker["SPY"]["weight"] == pytest.approx(6_600.0 / 101_234.56)


def test_the_refresh_does_not_inherit_the_dry_run_default(monkeypatch) -> None:
    """connect() returns None in dry-run mode, and DRY_RUN_DEFAULT defaults to on.

    If the refresh went through the default it would return no client and this whole
    feature would be a silent no-op on the signal cron.
    """
    calls = _stub_alpaca(monkeypatch)
    _stub_supabase(monkeypatch)
    monkeypatch.setenv("DRY_RUN_DEFAULT", "true")

    report = refresh_snapshot(run_date=TODAY)

    assert calls["connect_kwargs"] == {"dry_run": False}
    assert report["written"] is True, "an env dry-run default must not disable the refresh"


def test_a_dry_run_refresh_writes_nothing_and_reads_no_broker(monkeypatch) -> None:
    calls = _stub_alpaca(monkeypatch)
    written = _stub_supabase(monkeypatch)

    report = refresh_snapshot(run_date=TODAY, dry_run=True)

    assert report["written"] is False
    assert report["skipped"] == "dry run"
    assert calls == {}, "a dry run must not even build a client"
    assert written["settings"] == {} and written["positions"] == []


def test_a_failed_refresh_is_swallowed_and_reported(monkeypatch) -> None:
    """Bookkeeping must never be able to fail a run whose real work already finished."""
    import execution.alpaca_paper as ap

    def _boom(*a, **k):
        raise ConnectionError("alpaca unreachable")

    monkeypatch.setattr(ap, "connect", _boom)
    _stub_supabase(monkeypatch)

    report = refresh_snapshot(run_date=TODAY)

    assert report["written"] is False
    assert "ConnectionError" in report["skipped"]


def test_an_empty_book_still_writes_live_nav(monkeypatch) -> None:
    """A flat account is legitimate; the NAV move is still worth recording."""
    _stub_alpaca(monkeypatch, nav=100_000.0, book={})
    written = _stub_supabase(monkeypatch)

    report = refresh_snapshot(run_date=TODAY)

    # str(round(x, 2)) trims a trailing zero, which is the format the execution job
    # already uses for live_nav. Recorded here so the two writers stay identical.
    assert written["settings"]["live_nav"] == "100000.0"
    assert written["positions"] == []
    assert report["tickers"] == 0


# ------------------------------------------------------------------ fix 2: the wiring

def test_run_signal_refreshes_the_basis_after_writing_the_signal(monkeypatch) -> None:
    """The post-close refresh is what keeps the proposal's basis at most a day old.

    Drives a clean, unblocked signal run all the way to the end. The gate passes, so
    the run reaches step 7, which is the refresh.
    """
    import numpy as np
    import pandas as pd

    import dashboard.supabase_client as sb
    import execution.calendar_utils as cal
    import execution.snapshot as snap
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal

    idx = pd.date_range("2026-01-01", periods=200, freq="B")
    rng = np.random.default_rng(5)
    close = pd.DataFrame(
        {t: 100 + np.cumsum(rng.normal(0, 0.5, len(idx))) for t in etf.UNIVERSE},
        index=idx,
    )

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(cal, "record_run", lambda job, d: None)
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(sb, "get_setting", lambda k: None)
    monkeypatch.setattr(sb, "set_setting", lambda k, v: True)
    monkeypatch.setattr(sb, "write_decision", lambda d, dec: True)

    called: list = []
    monkeypatch.setattr(
        snap, "refresh_snapshot",
        lambda **kw: called.append(kw) or {
            "written": True, "nav": 101_000.0, "tickers": 8, "skipped": None
        },
    )

    code = run_signal.main(max_secs=120)

    assert code == 0, "a clean signal run completes"
    assert len(called) == 1, "the run must refresh the basis exactly once"
    assert called[0]["run_date"] == TODAY


def test_run_execution_refreshes_the_basis_when_the_day_is_rejected(monkeypatch) -> None:
    """A rejected day must not leave the snapshot to age."""
    import execution.snapshot as snap

    monkeypatch.setattr(snap, "refresh_snapshot", _capture())

    result = _run_execution_with_stub(
        Path(tempfile.mkdtemp()), monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        decision="reject",
    )

    assert result["exit_code"] == 0, "a reject is still a clean exit"
    assert _LAST_REFRESH["calls"] == 1, "the rejected path must refresh the basis once"
    assert result["emails"][0]["subject"] == f"[SKIP] run_execution {TODAY}"
    assert "Snapshot refreshed from Alpaca" in result["emails"][0]["body"]
    assert result["submit_calls"] == 0, "a rejected day must not trade"


def test_run_execution_refreshes_the_basis_when_the_signal_is_stale(monkeypatch) -> None:
    """The stale refusal returns before the broker is connected, so it needs its own refresh."""
    import execution.snapshot as snap

    from tests.test_paper_execution import _signal_date_sessions_ago

    monkeypatch.setattr(snap, "refresh_snapshot", _capture())

    result = _run_execution_with_stub(
        Path(tempfile.mkdtemp()), monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        signal_as_of=_signal_date_sessions_ago(3),
    )

    assert result["exit_code"] == 4
    assert _LAST_REFRESH["calls"] == 1, "a stale skip must still advance the basis"
    assert result["submit_calls"] == 0


# ------------------------------------------------------------------ fix 3: the P&L line

def test_the_summary_reports_book_pnl_as_the_nav_move() -> None:
    s = RunSummary(job="run_execution", run_date=TODAY,
                   nav_frozen=100_000.0, nav_live=101_250.0,
                   previous_live_nav=100_400.0, nav_change=850.0,
                   turnover_cost=12.5)

    body = body_for(s)

    assert "Book P&L (live NAV move since previous run): $+850.00" in body
    # Cost is a drag on the result, not the result, so it gets its own line.
    assert "Turnover cost today: $12.50" in body
    assert "Book P&L (live NAV move since previous run): $+850.00\n" in body
    assert "\nTurnover cost today: $12.50" in body


def test_a_losing_day_is_signed_and_not_hidden() -> None:
    s = RunSummary(job="run_execution", run_date=TODAY,
                   nav_live=99_000.0, previous_live_nav=100_000.0, nav_change=-1_000.0)
    assert "Book P&L (live NAV move since previous run): $-1,000.00" in body_for(s)


def test_the_summary_says_so_when_there_is_no_previous_nav() -> None:
    """First run with no stored NAV: say it is unknown rather than reporting zero."""
    s = RunSummary(job="run_execution", run_date=TODAY,
                   nav_live=100_000.0, previous_live_nav=None, nav_change=None)

    body = body_for(s)

    assert "no previous live NAV" in body
    assert "Book P&L (live NAV move since previous run): $+0.00" not in body


def test_the_cost_is_never_reported_as_the_book_pnl() -> None:
    """The old behaviour: the day's simulated costs were billed as 'Day P&L'."""
    s = RunSummary(job="run_execution", run_date=TODAY,
                   nav_live=101_250.0, previous_live_nav=100_400.0, nav_change=850.0,
                   turnover_cost=12.5)

    body = body_for(s)

    assert "Day P&L" not in body
    assert "$+850.00" in body, "the NAV move is the book's P&L"
    assert "$12.50" in body and "Turnover cost today" in body


def test_run_execution_bases_the_nav_move_on_the_stored_nav(monkeypatch) -> None:
    """End to end: previous live NAV comes from the settings row, not from today."""
    import pathlib
    import tempfile

    from tests.test_paper_execution import _run_execution_with_stub

    result = _run_execution_with_stub(
        pathlib.Path(tempfile.mkdtemp()), monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
    )

    body = result["emails"][0]["body"]
    # The harness stores live_nav = 100000 and reports the Alpaca equity as 100000.
    assert "NAV frozen for sizing: $100,000.00" in body
    assert "Book P&L (live NAV move since previous run): $+0.00" in body
    assert "Turnover cost today: $" in body
