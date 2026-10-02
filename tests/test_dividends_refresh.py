"""The daily distribution refresh and what the signal job does with it.

The carry leg of the sleeve P&L reads the distribution cache. Before v9.9 nothing
refreshed that cache in the daily path, so it still ended 2026-06-15 and every live
carry was zero: a payout that landed after that date was simply absent, and because the
unadjusted close drops on the ex-date, the price leg booked the drop as a loss.

This file pins the three things that fix it: the fetch is bounded and all-or-nothing,
the refresh date is recorded so a stale cache can be told from a fresh one, and a
failed refresh refuses the session's sleeve P&L instead of producing a wrong number.
"""

from __future__ import annotations

import socket
from datetime import date

import pandas as pd
import pytest

import signals.dividends as div

TODAY = date.today().isoformat()
SERIES = pd.Series([0.5, 0.4], index=pd.DatetimeIndex(["2026-06-19", "2026-09-18"]))


# ------------------------------------------------------------------ the timeout guard


def test_the_fetch_is_bounded_at_the_socket_layer_and_restored(monkeypatch) -> None:
    """yfinance's dividends property takes no timeout argument in the installed version.

    So the bound is applied where it binds, the process-wide socket default, which is
    the same mechanism job_guard installs for the jobs. It has to be restored: leaving a
    global timeout set would silently bound every later call in the process, including
    the dashboard's if this ever ran there.
    """
    before = socket.getdefaulttimeout()
    seen: list[float | None] = []

    class _FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            seen.append(socket.getdefaulttimeout())
            return SERIES

    monkeypatch.setattr(div.yf, "Ticker", _FakeTicker)

    out = div.fetch_dividends(["SPY", "GLD"], timeout=7.5)

    assert seen == [7.5, 7.5], "the bound must be in force for every fetch"
    assert socket.getdefaulttimeout() == before, "and restored afterwards"
    assert set(out) == {"SPY", "GLD"}
    assert out["SPY"].index[0].date().isoformat() == "2026-06-19", "indexed by ex-date"


def test_the_socket_timeout_is_restored_when_the_fetch_raises(monkeypatch) -> None:
    """A raise is exactly when a leaked global timeout would be hardest to notice."""
    before = socket.getdefaulttimeout()

    class _BoomTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            raise RuntimeError("yahoo 429")

    monkeypatch.setattr(div.yf, "Ticker", _BoomTicker)

    with pytest.raises(RuntimeError):
        div.fetch_dividends(["SPY"], timeout=3.0)

    assert socket.getdefaulttimeout() == before


def test_one_failing_ticker_writes_nothing(monkeypatch, tmp_path) -> None:
    """All or nothing: a partial cache is worse than an old one.

    The tickers that did answer would look like the tickers that paid nothing, which is
    the exact confusion the refresh date exists to resolve.
    """
    calls: list[str] = []

    class _PartialTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            calls.append(self.ticker)
            if self.ticker == "HYG":
                raise RuntimeError("yahoo 429")
            return SERIES

    monkeypatch.setattr(div.yf, "Ticker", _PartialTicker)

    with pytest.raises(RuntimeError):
        div.ingest(["SPY", "HYG"], raw_dir=tmp_path)

    assert calls == ["SPY", "HYG"]
    assert not (tmp_path / "SPY_dividends.parquet").exists()
    assert div.dividends_as_of(tmp_path) is None


# ------------------------------------------------------------------ the refresh marker


def test_ingest_writes_the_cache_and_records_the_refresh_date(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        div, "fetch_dividends", lambda tickers, **kw: {t: SERIES for t in tickers}
    )

    report = div.ingest(["SPY", "GLD"], raw_dir=tmp_path)

    assert report == {
        "ok": True,
        "tickers": 2,
        "distributions": 4,
        "as_of": TODAY,
    }
    assert (tmp_path / "SPY_dividends.parquet").exists()
    assert (tmp_path / "GLD_dividends.parquet").exists()
    assert div.dividends_as_of(tmp_path) == TODAY


def test_a_failed_refresh_leaves_the_previous_cache_and_date_intact(
    monkeypatch, tmp_path
) -> None:
    """The marker is written last, and the files before it.

    A crash between the two leaves a stale date over new files, which reads as stale and
    is refused. The other order would leave a fresh date over old files, which reads as
    current and gets trusted: the wrong way round for a check whose whole job is to
    catch a missing payout.
    """
    monkeypatch.setattr(
        div, "fetch_dividends", lambda tickers, **kw: {t: SERIES for t in tickers}
    )
    div.ingest(["SPY"], raw_dir=tmp_path)
    files_before = (tmp_path / "SPY_dividends.parquet").read_bytes()
    as_of_before = div.dividends_as_of(tmp_path)

    def _boom(tickers, **kw):
        raise RuntimeError("yahoo 429")

    monkeypatch.setattr(div, "fetch_dividends", _boom)

    with pytest.raises(RuntimeError):
        div.ingest(["SPY", "HYG"], raw_dir=tmp_path)

    assert (tmp_path / "SPY_dividends.parquet").read_bytes() == files_before
    assert not (tmp_path / "HYG_dividends.parquet").exists()
    assert div.dividends_as_of(tmp_path) == as_of_before


def test_no_recorded_date_reads_as_never_refreshed(tmp_path) -> None:
    """A container that has never run the ingest must not be read as fresh.

    This is the state of a fresh Render container: the parquets are in the image, the
    marker is not, and the coverage of the committed cache is unknown.
    """
    assert div.dividends_as_of(tmp_path) is None
    assert div.dividends_as_of(tmp_path / "does-not-exist") is None


def test_an_unreadable_marker_is_not_a_date(tmp_path) -> None:
    (tmp_path / div.AS_OF_FILENAME).write_text("{not json")
    assert div.dividends_as_of(tmp_path) is None

    (tmp_path / div.AS_OF_FILENAME).write_text('{"tickers": 8}')
    assert div.dividends_as_of(tmp_path) is None


# ------------------------------------------------------------------ the job's wiring


def _drive_signal(monkeypatch, *, dividend_ingest, max_retry_secs=None):
    """Run the signal job end to end with everything but the dividend step stubbed.

    Returns (exit_code, what step 8 was asked to do, sleeve report).
    """
    import numpy as np

    import dashboard.supabase_client as sb
    import execution.calendar_utils as cal
    import execution.snapshot as snap
    import risk.sleeve_pnl as sp
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal

    idx = pd.date_range("2026-01-01", periods=200, freq="B")
    rng = np.random.default_rng(7)
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
    monkeypatch.setattr(
        snap, "refresh_snapshot",
        lambda **kw: {"written": True, "nav": 101_000.0, "tickers": 8,
                      "skipped": None, "failed": False},
    )
    monkeypatch.setattr(div, "ingest", dividend_ingest)

    # Step 8's own behaviour is tested in tests/test_sleeve_pnl.py; what matters here is
    # that it is reached, and with which session.
    sleeve_calls: list[dict] = []

    def _sleeve_spy(**kwargs):
        sleeve_calls.append(kwargs)
        return {
            "written": False, "status": "uncomparable", "reason": "stubbed",
            "trade_date": kwargs.get("as_of_date"), "prior_date": None,
        }

    monkeypatch.setattr(sp, "record_daily_sleeve_pnl", _sleeve_spy)
    monkeypatch.setattr(run_signal, "RETRY_INTERVAL_SECS", 0)
    if max_retry_secs is not None:
        monkeypatch.setattr(run_signal, "MAX_RETRY_SECS", max_retry_secs)

    code = run_signal.main(max_secs=120)
    return code, sleeve_calls


def test_a_transient_dividend_failure_retries_inside_the_job(
    monkeypatch, no_real_email
) -> None:
    """The same retry policy as the closes, not a separate one."""
    attempts: list[int] = []

    def _flaky(tickers, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("yahoo 429")
        return {"ok": True, "tickers": 8, "distributions": 12, "as_of": TODAY}

    code, sleeve_calls = _drive_signal(monkeypatch, dividend_ingest=_flaky)

    assert code == 0
    assert len(attempts) == 2, "a transient failure must be retried"
    assert len(sleeve_calls) == 1
    assert "Distributions not refreshed" not in no_real_email[0]["body"]


def test_a_persistent_dividend_failure_records_it_without_aborting(
    monkeypatch, no_real_email
) -> None:
    """The signal is the run's product; a dividend table is not.

    Refusing to write a signal because Yahoo would not serve distributions would let
    bookkeeping veto the one thing this job exists to do. The failure is recorded, the
    session is still handed to the sleeve step (which refuses it), and the email says
    why the sleeve line is missing.
    """
    def _always_fails(tickers, **kwargs):
        raise RuntimeError("yahoo 429")

    code, sleeve_calls = _drive_signal(
        monkeypatch, dividend_ingest=_always_fails, max_retry_secs=0
    )

    assert code == 0, "a distribution failure must not fail the run"
    assert len(sleeve_calls) == 1, "step 8 still runs, so the refusal is its decision"
    assert no_real_email[0]["subject"].startswith("[OK]")

    body = no_real_email[0]["body"]
    assert "Distributions not refreshed: RuntimeError: yahoo 429" in body
    assert "Sleeve P&L: not computed (" in body


def test_the_successful_refresh_is_logged_with_its_counts(
    monkeypatch, no_real_email, caplog
) -> None:
    """A refresh that worked says how much it got, so a silent empty one is visible."""
    import logging

    def _ok(tickers, **kwargs):
        return {"ok": True, "tickers": 8, "distributions": 1268, "as_of": TODAY}

    with caplog.at_level(logging.INFO):
        code, _ = _drive_signal(monkeypatch, dividend_ingest=_ok)

    assert code == 0
    assert "distributions refreshed on attempt 1: 1268 across 8 ticker(s)" in caplog.text
    assert "Distributions not refreshed" not in no_real_email[0]["body"]
