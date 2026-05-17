"""W4 — performance metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import TRADING_DAYS, max_drawdown, sharpe, sortino, summary


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype="float64")


# ---------------------------------------------------------------- sharpe


def test_sharpe_annualised() -> None:
    rng = np.random.default_rng(1)
    x = _series(list(rng.normal(10.0, 100.0, 2000)))
    daily = x.mean() / x.std(ddof=1)
    assert sharpe(x) == pytest.approx(daily * np.sqrt(TRADING_DAYS), rel=1e-9)


def test_sharpe_zero_variance_is_zero_not_nan() -> None:
    assert sharpe(_series([0.0] * 50)) == 0.0
    assert sharpe(_series([5.0] * 50)) == 0.0  # constant → zero std


def test_sharpe_empty_safe() -> None:
    assert sharpe(_series([])) == 0.0
    assert sharpe(_series([1.0])) == 0.0


# ---------------------------------------------------------------- sortino


def test_sortino_uses_downside_deviation() -> None:
    # mixed series; Sortino denominator must be RMS of negatives only
    x = _series([10.0, -5.0, 8.0, -3.0, 12.0, -4.0])
    arr = x.to_numpy()
    downside = np.minimum(arr, 0.0)
    dd = np.sqrt(np.mean(downside ** 2))
    expected = arr.mean() / dd * np.sqrt(TRADING_DAYS)
    assert sortino(x) == pytest.approx(expected, rel=1e-9)


def test_sortino_differs_from_sharpe_on_asymmetric_series() -> None:
    # big upside, small downside → Sortino > Sharpe
    x = _series([50.0, 50.0, -2.0, 50.0, -2.0, 50.0])
    assert sortino(x) > sharpe(x)


def test_sortino_no_downside_is_zero() -> None:
    # all-positive → downside deviation 0 → guarded to 0.0
    assert sortino(_series([1.0, 2.0, 3.0, 4.0])) == 0.0


# ---------------------------------------------------------------- max drawdown


def test_max_drawdown_known_path() -> None:
    # equity: 0,10,30,20,5,25  → cumulative of daily
    # daily diffs: 10,20,-10,-15,20  (start equity 0)
    daily = _series([10, 20, -10, -15, 20])
    # equity = [10,30,20,5,25]; running max=[10,30,30,30,30]
    # drawdown = [0,0,-10,-25,-5]; min = -25
    assert max_drawdown(daily) == pytest.approx(-25.0, abs=1e-9)


def test_max_drawdown_monotone_up_is_zero() -> None:
    assert max_drawdown(_series([5, 5, 5, 5])) == 0.0


def test_max_drawdown_is_non_positive() -> None:
    rng = np.random.default_rng(2)
    daily = _series(list(rng.normal(0, 50, 500)))
    assert max_drawdown(daily) <= 0.0


# ---------------------------------------------------------------- summary


def test_summary_fields() -> None:
    daily = _series([10, 20, -10, -15, 20, 0, 0, 5])
    trades = pd.DataFrame({
        "net_pnl": [100.0, -50.0, 30.0],
        "holding_days": [5, 10, 3],
    })
    s = summary(daily, trades)
    assert s.n_trades == 3
    assert s.hit_rate == pytest.approx(2 / 3)
    assert s.avg_holding_days == pytest.approx(6.0)
    assert s.total_net_pnl == pytest.approx(80.0)
    assert s.turnover == pytest.approx(3 / (8 / TRADING_DAYS))
    assert s.max_drawdown <= 0.0


def test_summary_no_trades_safe() -> None:
    daily = _series([0.0] * 20)
    trades = pd.DataFrame({"net_pnl": [], "holding_days": []})
    s = summary(daily, trades)
    assert s.n_trades == 0
    assert s.hit_rate == 0.0
    assert s.sharpe == 0.0
    assert s.total_net_pnl == 0.0
