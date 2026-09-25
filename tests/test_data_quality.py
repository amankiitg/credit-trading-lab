"""Tests for signals/data_quality.py and the signal write gate -- sprint v9.4.

The gate exists because the 2026-09-24 signal run shipped a book built from a
close matrix where EFA, EEM, IEF, HYG and LQD were each missing the 2026-09-23
session, and nothing anywhere said so. See sprints/v9.3/notes.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals.data_quality import check_signal_ready, find_row_gaps

IDX = pd.date_range("2026-01-01", periods=6, freq="D")
TICKERS = ["SPY", "EFA", "EEM"]


def _frame(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols, index=IDX)


# ---------------------------------------------------------------- find_row_gaps

def test_leading_nan_is_not_a_gap() -> None:
    """Staggered inception is legitimate and must not block a signal."""
    close = _frame(
        SPY=[1.0] * 6,
        EFA=[np.nan, np.nan, 2.0, 2.1, 2.2, 2.3],  # starts two sessions late
        EEM=[np.nan, 3.0, 3.1, 3.2, 3.3, 3.4],
    )
    assert find_row_gaps(close) == []


def test_interior_hole_is_a_gap_with_its_date() -> None:
    """A NaN after a ticker's first observation is a hole, and is named."""
    close = _frame(
        SPY=[1.0] * 6,
        EFA=[np.nan, 2.0, np.nan, 2.1, 2.2, 2.3],
        EEM=[3.0] * 6,
    )
    gaps = find_row_gaps(close)
    assert len(gaps) == 1
    assert gaps[0]["ticker"] == "EFA"
    assert gaps[0]["date"] == IDX[2]


def test_trailing_hole_is_a_gap() -> None:
    """The live case: the missing session was the most recent one but one."""
    close = _frame(
        SPY=[1.0] * 6,
        EFA=[2.0, 2.0, 2.1, 2.1, np.nan, np.nan],
        EEM=[3.0] * 6,
    )
    gaps = find_row_gaps(close)
    assert [g["date"] for g in gaps] == [IDX[4], IDX[5]]


def test_empty_column_is_reported_with_no_date() -> None:
    close = _frame(SPY=[1.0] * 6, EFA=[np.nan] * 6, EEM=[3.0] * 6)
    gaps = find_row_gaps(close)
    assert gaps == [{"ticker": "EFA", "date": None}]


# ---------------------------------------------------------------- check_signal_ready

def test_clean_universe_is_ready() -> None:
    close = _frame(SPY=[1.0] * 6, EFA=[2.0] * 6, EEM=[3.0] * 6)
    sigma = pd.DataFrame(0.2, index=IDX, columns=TICKERS)
    assert check_signal_ready(close, sigma, IDX[-1]) == []


def test_hole_blocks_and_names_the_ticker_and_date() -> None:
    close = _frame(SPY=[1.0] * 6, EFA=[2.0, 2.0, np.nan, 2.1, 2.2, 2.3], EEM=[3.0] * 6)
    sigma = pd.DataFrame(0.2, index=IDX, columns=TICKERS)

    problems = check_signal_ready(close, sigma, IDX[-1])

    assert len(problems) == 1
    assert "EFA" in problems[0]
    assert str(IDX[2].date()) in problems[0]


def test_nan_sigma_on_as_of_date_blocks() -> None:
    """The direct symptom: a gapped name has no sigma on the as-of date."""
    close = _frame(SPY=[1.0] * 6, EFA=[2.0] * 6, EEM=[3.0] * 6)
    sigma = pd.DataFrame(0.2, index=IDX, columns=TICKERS)
    sigma.loc[IDX[-1], "EFA"] = np.nan

    problems = check_signal_ready(close, sigma, IDX[-1])

    assert len(problems) == 1
    assert "NaN sigma" in problems[0] and "EFA" in problems[0]


def test_as_of_date_missing_from_sigma_blocks() -> None:
    close = _frame(SPY=[1.0] * 6, EFA=[2.0] * 6, EEM=[3.0] * 6)
    sigma = pd.DataFrame(0.2, index=IDX, columns=TICKERS)
    problems = check_signal_ready(close, sigma, pd.Timestamp("2026-06-01"))
    assert len(problems) == 1
    assert "not in the sigma index" in problems[0]


# ---------------------------------------------------------------- the real shape

def test_the_2026_09_24_gap_shape_is_caught() -> None:
    """Reproduce the live failure in miniature: five names missing one session.

    The hole is what makes the 63 day rolling vol NaN, which is why the gate
    checks both the row gap and the sigma, not just one of them.
    """
    from signals.trend_signal import compute_trend

    n = 200
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    gapped = ["EFA", "EEM", "IEF", "HYG", "LQD"]
    rng = np.random.default_rng(7)
    close = pd.DataFrame(
        {t: 100 + np.cumsum(rng.normal(0, 0.5, n)) for t in ["SPY", "TLT", "GLD"] + gapped},
        index=idx,
    )
    # Mimic load_universe_close's outer join with one session missing for five names.
    close.loc[idx[-2], gapped] = np.nan

    tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    sigma = tidy.pivot(index="date", columns="ticker", values="sigma").sort_index()

    # The symptom the live run showed: sigma NaN from the missing session on.
    for t in gapped:
        assert pd.isna(sigma[t].loc[idx[-2]]), t
        assert pd.isna(sigma[t].loc[idx[-1]]), t

    problems = check_signal_ready(close, sigma, idx[-1])

    assert len(problems) == 10, problems  # 5 row gaps + 5 NaN sigmas
    assert sum("missing close" in p for p in problems) == 5
    assert sum("NaN sigma" in p for p in problems) == 5


# ---------------------------------------------------------------- job level

def test_signal_job_refuses_to_write_when_the_gate_fails(monkeypatch, caplog) -> None:
    """The whole point: a bad matrix must not reach Supabase.

    Task item 3: log which ticker and date, and exit non-zero.
    """
    import logging

    import execution.calendar_utils as cal
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal
    from dashboard import supabase_client as sb

    idx = pd.date_range("2026-01-01", periods=200, freq="B")
    gapped = ["EFA", "EEM", "IEF", "HYG", "LQD"]
    rng = np.random.default_rng(11)
    close = pd.DataFrame(
        {t: 100 + np.cumsum(rng.normal(0, 0.5, len(idx))) for t in etf.UNIVERSE},
        index=idx,
    )
    close.loc[idx[-2], gapped] = np.nan

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)

    written: list = []
    monkeypatch.setattr(sb, "get_setting", lambda k: None)
    monkeypatch.setattr(
        sb, "set_setting", lambda k, v: written.append((k, v)) or True
    )
    monkeypatch.setattr(sb, "write_decision", lambda d, dec: written.append((d, dec)) or True)

    with caplog.at_level(logging.ERROR):
        code = run_signal.main(max_secs=120)

    assert code != 0, "a failed gate must exit non-zero"
    assert written == [], f"nothing may be written, got {written}"

    errors = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "REFUSING to write a signal" in errors
    assert "EFA" in errors, "the offending ticker must be named"
    assert str(idx[-2].date()) in errors, "the offending date must be named"
