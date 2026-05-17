"""W3 — backtest engine correctness + leakage (C25)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestResult, run
from execution.costs import trade_cost


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype="float64")


# ----------------------------------------------------------- C25 (a): hand calc


def test_c25_synthetic_trade_matches_hand_calc() -> None:
    """One short-residual trade, every number hand-verified.

    rv      = [0, 0, 0.10, 0.08, 0.05, 0.02, 0, 0, 0, 0]
    pos     = [0, 0, -1, -1, -1, -1, 0, 0, 0, 0]   (short, runs idx 2..5)
    fill_lag = 1 → entry_fill = 3, exit_fill = 7
    rv_entry = rv[3] = 0.08 ; rv_exit = rv[7] = 0.0 ; holding = 4 days
    gross = (-1)·(0.0 − 0.08)·1e6 = 80,000
    cost  = 700 spread+slippage + 0.004·(4/252)·1e6 borrow = 763.49206...
    net   = 79,236.50793...
    """
    rv = _series([0, 0, 0.10, 0.08, 0.05, 0.02, 0, 0, 0, 0])
    pos = pd.Series([0, 0, -1, -1, -1, -1, 0, 0, 0, 0], index=rv.index)
    hr = pd.Series(np.full(10, 0.46), index=rv.index)

    res = run(rv, pos, hr, notional=1_000_000.0, fill_lag=1)

    assert len(res.trades) == 1
    tr = res.trades.iloc[0]
    assert tr["side"] == -1
    assert tr["holding_days"] == 4
    assert tr["gross_pnl"] == pytest.approx(80_000.0, abs=1e-6)

    expected_cost = trade_cost(1_000_000.0, 4)
    assert tr["cost"] == pytest.approx(expected_cost, abs=1e-9)
    assert tr["net_pnl"] == pytest.approx(80_000.0 - expected_cost, abs=1e-6)

    # daily P&L must sum to the trade's net P&L
    assert res.daily_pnl.sum() == pytest.approx(tr["net_pnl"], abs=1e-6)


def test_net_equals_gross_minus_cost_every_row() -> None:
    rv = _series([0, 0.05, 0.10, 0.04, 0.0, -0.03, 0.0, 0.02, 0.0, 0.0])
    pos = pd.Series([0, 1, 1, 1, 0, -1, -1, 0, 0, 0], index=rv.index)
    hr = pd.Series(np.full(10, 0.5), index=rv.index)
    res = run(rv, pos, hr, notional=1_000_000.0, fill_lag=1)
    for _, tr in res.trades.iterrows():
        assert tr["net_pnl"] == pytest.approx(tr["gross_pnl"] - tr["cost"], abs=1e-9)


def test_daily_pnl_sums_to_total_net() -> None:
    rv = _series([0, 0.05, 0.10, 0.04, 0.0, -0.03, 0.0, 0.02, 0.0, 0.0])
    pos = pd.Series([0, 1, 1, 1, 0, -1, -1, 0, 0, 0], index=rv.index)
    hr = pd.Series(np.full(10, 0.5), index=rv.index)
    res = run(rv, pos, hr, notional=1_000_000.0, fill_lag=1)
    assert res.daily_pnl.sum() == pytest.approx(res.trades["net_pnl"].sum(), abs=1e-6)


# ----------------------------------------------------------- C25 (b): no leakage


def test_c25_perturbing_future_bar_does_not_change_past_trades() -> None:
    """Perturb a residual bar; trades that exited before it are unchanged."""
    rng = np.random.default_rng(0)
    rv = _series(list(np.cumsum(rng.normal(0, 0.01, 40))))
    # alternating positions producing several trades
    raw = ([0, 1, 1, 1, 0, 0, -1, -1, 0, 0] * 4)[: len(rv)]
    pos = pd.Series(raw, index=rv.index)
    hr = pd.Series(np.full(len(rv), 0.5), index=rv.index)

    base = run(rv, pos, hr, fill_lag=1)
    assert len(base.trades) >= 3, "need several trades for a meaningful test"

    # perturb a bar near the end
    k = len(rv) - 3
    rv_pert = rv.copy()
    rv_pert.iloc[k] += 0.5
    pert = run(rv_pert, pos, hr, fill_lag=1)

    cutoff = rv.index[k]
    past_base = base.trades[base.trades["exit_fill_date"] < cutoff].reset_index(drop=True)
    past_pert = pert.trades[pert.trades["exit_fill_date"] < cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(past_base, past_pert)

    # and the daily P&L strictly before the perturbed bar is identical
    pd.testing.assert_series_equal(
        base.daily_pnl.loc[:cutoff].iloc[:-1],
        pert.daily_pnl.loc[:cutoff].iloc[:-1],
    )


def test_fill_lag_below_one_raises() -> None:
    rv = _series([0, 0.1, 0.0])
    pos = pd.Series([0, -1, 0], index=rv.index)
    hr = pd.Series([0.5, 0.5, 0.5], index=rv.index)
    with pytest.raises(ValueError):
        run(rv, pos, hr, fill_lag=0)


def test_position_open_at_end_is_closed_not_dropped() -> None:
    rv = _series([0, 0.05, 0.10, 0.12, 0.15])
    pos = pd.Series([0, 1, 1, 1, 1], index=rv.index)  # never returns to flat
    hr = pd.Series(np.full(5, 0.5), index=rv.index)
    res = run(rv, pos, hr, fill_lag=1)
    assert len(res.trades) == 1
    assert bool(res.trades.iloc[0]["closed_at_end"]) is True
    # entry_fill = 2, exit_fill clamped to 4
    assert res.trades.iloc[0]["holding_days"] == 2


def test_long_trade_sign() -> None:
    # long residual: profit when rv rises
    rv = _series([0, 0, 0.02, 0.05, 0.10, 0.10])
    pos = pd.Series([0, 0, 1, 1, 0, 0], index=rv.index)
    hr = pd.Series(np.full(6, 0.5), index=rv.index)
    res = run(rv, pos, hr, fill_lag=1)
    tr = res.trades.iloc[0]
    # entry_fill=3 rv=0.05, exit_fill=5 rv=0.10 → gross = +1·(0.10-0.05)·1e6
    assert tr["side"] == 1
    assert tr["gross_pnl"] == pytest.approx(50_000.0, abs=1e-6)
