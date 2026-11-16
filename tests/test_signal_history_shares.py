"""The share-count line reads public.share_history, a daily series, with GAPS.

The panel used to read the positions table, which only started carrying a shares column
on 2026-09-28 -- so the line was blank before October while the weight above it started
in June. The replacement is a daily share history, and the rule that matters is that a
session it does not cover is UNKNOWN: the line breaks, it is never carried forward and
never zero-filled. A zero says the account held nothing, which is a claim nothing in the
data supports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard import signal_history


def _closes(periods: int = 300, tickers: tuple[str, ...] = ("SPY", "TLT")) -> pd.DataFrame:
    index = pd.bdate_range("2025-06-02", periods=periods)
    return pd.DataFrame({t: [100.0 + i for i in range(periods)] for t in tickers}, index=index)


def _weights(sessions: pd.DatetimeIndex, ticker: str = "SPY") -> pd.DataFrame:
    return pd.DataFrame(
        [{"trade_date": s, "ticker": ticker, "weight": 0.25} for s in sessions]
    )


def _share_history(dates, shares, ticker: str = "SPY") -> pd.DataFrame:
    return pd.DataFrame(
        [{"session_date": d, "ticker": ticker, "shares": v}
         for d, v in zip(dates, shares)]
    )


def test_a_covered_session_is_the_measured_value() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history(sessions, [100.0, 120.0, 120.0, 90.0, 90.0])

    series = signal_history.shares_series(frame, None, "SPY", sessions)

    assert series.tolist() == [100.0, 120.0, 120.0, 90.0, 90.0]


def test_a_session_with_no_row_is_a_gap_not_a_carry_forward_and_not_zero() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history(
        [sessions[0], sessions[1], sessions[3]], [100.0, 120.0, 90.0]
    )

    series = signal_history.shares_series(frame, None, "SPY", sessions)

    assert series.loc[sessions[0]] == pytest.approx(100.0)
    assert np.isnan(series.loc[sessions[2]]), "no row is unknown, not the previous level"
    assert series.loc[sessions[3]] == pytest.approx(90.0)


def test_dates_before_the_series_starts_are_gaps_too() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=5)
    frame = _share_history([sessions[3], sessions[4]], [50.0, 50.0])

    series = signal_history.shares_series(frame, None, "SPY", sessions)

    assert series.loc[sessions[:3]].isna().all(), (
        "the book had no position data before this point, and 0 would say it held nothing"
    )
    assert series.loc[sessions[3]] == pytest.approx(50.0)


def test_a_ticker_absent_from_the_history_omits_the_axis() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=4)
    frame = _share_history(sessions, [10.0, 10.0, 10.0, 10.0], ticker="TLT")

    assert signal_history.shares_series(frame, None, "SPY", sessions) is None
    assert signal_history.shares_series(
        pd.DataFrame(columns=["session_date", "ticker", "shares"]), None, "SPY", sessions
    ) is None


def test_the_figure_draws_the_gap_and_names_it() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    frame = _share_history(
        [window[5], window[6], window[-1]], [40.0, 45.0, 45.0]
    )

    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min(),
        share_history=frame,
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    shares = [t for t in fig.data if t.name == "shares held"][0]
    assert shares.connectgaps is False, "a gap must break the line, not be bridged"
    y = np.asarray(shares.y, dtype=float)
    assert np.isnan(y[:5]).all()
    assert not np.isnan(y[5])
    notes = " ".join(str(a.text) for a in fig.layout.annotations)
    assert "no position data" in notes


def test_a_chart_without_any_share_history_omits_the_axis() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())

    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    assert history["has_shares"] is False
    assert not [t for t in fig.data if t.name == "shares held"]
    assert "shares held" not in (fig.layout.yaxis4.title.text or "")


def test_the_upper_line_says_held_because_that_is_what_it_is() -> None:
    closes = _closes()
    positions = _weights(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )
    fig = signal_history.ticker_figure("SPY", history, window)

    names = [t.name for t in fig.data]
    assert "held weight" in names and "position weight" not in names
    assert fig.layout.yaxis2.title.text == "held weight"

# ------------------------------------------------------------------ after the rebuild

def _positions(sessions, shares, ticker: str = "SPY") -> pd.DataFrame:
    """A positions frame: the snapshot the execution job writes, date by date."""
    return pd.DataFrame(
        [{"trade_date": d, "ticker": ticker, "weight": 0.25, "shares": v}
         for d, v in zip(sessions, shares)]
    )


def test_the_line_keeps_going_after_the_rebuild_ends() -> None:
    """The rebuild is a one-off; the snapshot is written every day after it.

    The chart has to keep updating tomorrow, so after the cutover the line is read
    from positions.shares. A chart that only read share_history would freeze on the
    day the backfill ran, which is the same class of bug as the blank line it
    replaced.
    """
    sessions = pd.bdate_range("2026-09-25", periods=6)  # straddles the cutover
    history = _share_history([sessions[0], sessions[1]], [10.0, 12.0])  # stops early
    positions = _positions(sessions[3:], [12.0, 14.0, 14.0])

    series = signal_history.shares_series(history, positions, "SPY", sessions)

    assert series.loc[sessions[0]] == pytest.approx(10.0), "pre-cutover: from the rebuild"
    assert np.isnan(series.loc[sessions[2]]), "no data in either source yet"
    assert series.loc[sessions[3]] == pytest.approx(12.0), "post-cutover: from positions"
    assert series.loc[sessions[5]] == pytest.approx(14.0), "and it keeps going"


def test_after_the_cutover_the_other_source_fills_a_hole() -> None:
    sessions = pd.bdate_range("2026-09-28", periods=3)
    history = _share_history(sessions, [20.0, 21.0, 22.0])
    positions = _positions([sessions[0]], [20.0])  # only the first snapshot

    series = signal_history.shares_series(history, positions, "SPY", sessions)

    assert series.tolist() == [20.0, 21.0, 22.0], (
        "a gap only where BOTH sources are missing"
    )


def test_a_duplicate_date_never_disagrees_with_the_snapshot() -> None:
    """The overlap is 288 cells with a 0.000000 difference; positions still wins.

    It is the live source, so if the two ever do disagree the chart should show the
    snapshot the execution job actually wrote, not a reconstruction.
    """
    sessions = pd.bdate_range("2026-09-28", periods=2)
    history = _share_history(sessions, [20.0, 21.0])
    positions = _positions(sessions, [20.5, 21.5])

    series = signal_history.shares_series(history, positions, "SPY", sessions)

    assert series.tolist() == [20.5, 21.5]
