"""The share-count axis on the signal-history lower panel.

The panel already carries the trend score against the dead-zone band. The share count
answers the question the score cannot: did the position move because the signal flipped,
or because the rebalance band let it drift? These tests pin the three things that would
make the line quietly wrong -- a fabricated zero where there is no snapshot, a line that
draws a different book from the weight above it, and an axis that appears when the data
does not have it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard import signal_history


def _closes(periods: int = 300, tickers: tuple[str, ...] = ("SPY", "TLT")) -> pd.DataFrame:
    index = pd.bdate_range("2025-06-02", periods=periods)
    return pd.DataFrame({t: [100.0 + i for i in range(periods)] for t in tickers}, index=index)


def _positions(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for i, session in enumerate(sessions):
        rows.append({"trade_date": session, "ticker": "SPY",
                     "weight": 0.25, "shares": 30.0 if i < 5 else 35.460991972})
        if i == 3:
            rows.append({"trade_date": session, "ticker": "TLT",
                         "weight": -0.25, "shares": -330.0})
    return pd.DataFrame(rows)


def test_shares_are_zero_before_the_first_live_session_and_held_after() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=10)
    first_live = sessions[5]
    positions = pd.DataFrame(
        [
            {"trade_date": sessions[5], "ticker": "SPY", "weight": 0.2, "shares": 35.0},
            {"trade_date": sessions[7], "ticker": "SPY", "weight": 0.2, "shares": 36.5},
        ]
    )

    series = signal_history.shares_series(positions, "SPY", sessions, first_live)

    assert (series.loc[sessions < first_live] == 0.0).all()
    assert series.loc[sessions[5]] == pytest.approx(35.0)
    # sessions[6] has no snapshot: the step holds, it is not interpolated.
    assert series.loc[sessions[6]] == pytest.approx(35.0)
    assert series.loc[sessions[7]] == pytest.approx(36.5)


def test_a_ticker_not_held_in_a_stored_snapshot_is_a_measured_zero() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=6)
    positions = pd.DataFrame(
        [
            {"trade_date": sessions[2], "ticker": "SPY", "weight": 0.2, "shares": 10.0},
            {"trade_date": sessions[4], "ticker": "TLT", "weight": 0.1, "shares": 5.0},
        ]
    )

    series = signal_history.shares_series(positions, "SPY", sessions, sessions[2])

    assert series.loc[sessions[3]] == pytest.approx(10.0)  # no snapshot: carry
    assert series.loc[sessions[4]] == pytest.approx(0.0)   # snapshot says not held


def test_a_snapshot_table_without_the_column_omits_the_line() -> None:
    sessions = pd.bdate_range("2026-01-05", periods=4)
    positions = pd.DataFrame(
        [{"trade_date": sessions[1], "ticker": "SPY", "weight": 0.2}]
    )

    assert signal_history.shares_series(positions, "SPY", sessions, sessions[1]) is None


def test_the_history_carries_the_share_series_for_the_figure() -> None:
    closes = _closes()
    positions = _positions(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )

    assert history["has_shares"] is True
    assert history["shares"].notna().all()
    assert history["shares"].iloc[-1] == pytest.approx(35.460991972)


def test_the_share_line_is_on_the_lower_panels_secondary_axis() -> None:
    closes = _closes()
    positions = _positions(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )

    fig = signal_history.ticker_figure("SPY", history, window)

    shares = [t for t in fig.data if t.name == "shares held"]
    assert len(shares) == 1
    trace = shares[0]
    # Row 2's primary y-axis is y3 and its secondary is y4: the share line must be on
    # y4, or it would be read against the trend score's scale.
    assert trace.yaxis == "y4"
    assert trace.line.shape == "hv", "a step line, like the weight above it"
    assert trace.line.color == signal_history.SHARES_COLOR
    assert signal_history.SHARES_COLOR != signal_history.WEIGHT_COLOR

    axes = fig.layout
    assert axes.yaxis4.title.text == "shares held"
    assert list(axes.yaxis4.range) == [
        pytest.approx(v) for v in signal_history._shares_range(history["shares"])
    ]


def test_hover_gives_date_score_band_and_shares() -> None:
    closes = _closes()
    positions = _positions(closes.index)
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )

    fig = signal_history.ticker_figure("SPY", history, window)

    score = [t for t in fig.data if t.name == "trend score"][0]
    assert "dead zone" in score.hovertemplate
    band = history["dead_zone"]
    assert np.allclose(np.asarray(score.customdata), band)

    shares = [t for t in fig.data if t.name == "shares held"][0]
    assert "shares held" in shares.hovertemplate
    # Date is the shared x axis of the unified hover, so every panel reads it once.
    assert fig.layout.hovermode == "x unified"


def test_a_chart_without_shares_still_draws_and_says_nothing_it_cannot_back() -> None:
    closes = _closes()
    positions = _positions(closes.index).drop(columns=["shares"])
    window = signal_history.history_window(closes, positions["trade_date"].min())
    history = signal_history.build_ticker_history(
        closes, positions, "SPY", window, positions["trade_date"].min()
    )

    assert history["has_shares"] is False
    fig = signal_history.ticker_figure("SPY", history, window)
    assert not [t for t in fig.data if t.name == "shares held"]
    assert "shares held" not in (fig.layout.yaxis4.title.text or "")
