"""W9 — regime performance table schema."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult
from backtest.regime_table import (
    REGIME_TABLE_COLUMNS,
    regime_performance,
)


def _result(idx: pd.DatetimeIndex) -> BacktestResult:
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(50, 500, len(idx)), index=idx, name="daily_pnl")
    trades = pd.DataFrame({
        "entry_signal_date": [idx[5], idx[20], idx[40]],
        "net_pnl": [1000.0, -300.0, 500.0],
    })
    return BacktestResult(trades=trades, daily_pnl=daily, equity=daily.cumsum())


def _features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(idx)
    return pd.DataFrame({
        "vol_regime": (["high", "low"] * n)[:n],
        "equity_regime": (["bull", "bear"] * n)[:n],
        "equity_credit_lag": (["equity_first", "credit_first", "neither"] * n)[:n],
    }, index=idx)


def test_regime_table_full_cartesian() -> None:
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    strategies = {"A": _result(idx), "B": _result(idx)}
    table = regime_performance(strategies, _features(idx))
    # 2 strategies × (2 + 2 + 3 labels) = 14 rows
    assert len(table) == 14
    assert list(table.columns) == list(REGIME_TABLE_COLUMNS)


def test_regime_table_all_metrics_present() -> None:
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    table = regime_performance({"A": _result(idx)}, _features(idx))
    for col in ("sharpe", "sortino", "max_drawdown", "hit_rate"):
        assert table[col].notna().all(), f"{col} has NaN"
    assert (table["max_drawdown"] <= 0).all()
    assert ((table["hit_rate"] >= 0) & (table["hit_rate"] <= 1)).all()


def test_regime_table_covers_every_label() -> None:
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    table = regime_performance({"A": _result(idx)}, _features(idx))
    vol = set(table[table.regime_classifier == "vol_regime"]["regime_label"])
    assert vol == {"high", "low"}
    ecl = set(table[table.regime_classifier == "equity_credit_lag"]["regime_label"])
    assert ecl == {"equity_first", "credit_first", "neither"}
