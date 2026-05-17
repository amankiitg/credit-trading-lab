"""W10 — multi-signal portfolio combination."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult
from risk.portfolio import equal_weight, inverse_vol_weight, portfolio_table


def _result(daily: pd.Series) -> BacktestResult:
    trades = pd.DataFrame({"net_pnl": [1.0], "holding_days": [1]})
    return BacktestResult(trades=trades, daily_pnl=daily, equity=daily.cumsum())


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-01", periods=n, freq="B")


def test_equal_weight_is_mean() -> None:
    idx = _idx(100)
    a = pd.Series(np.full(100, 30.0), index=idx)
    b = pd.Series(np.full(100, 60.0), index=idx)
    c = pd.Series(np.full(100, 90.0), index=idx)
    ew = equal_weight({"a": _result(a), "b": _result(b), "c": _result(c)})
    assert np.allclose(ew.to_numpy(), 60.0)  # mean of 30, 60, 90


def test_inverse_vol_overweights_calmer_signal() -> None:
    idx = _idx(300)
    rng = np.random.default_rng(0)
    calm = pd.Series(rng.normal(10, 20, 300), index=idx)    # low vol
    wild = pd.Series(rng.normal(10, 200, 300), index=idx)   # high vol
    iv = inverse_vol_weight({"calm": _result(calm), "wild": _result(wild)}, window=63)
    # the inverse-vol book should track the calm signal more closely
    corr_calm = np.corrcoef(iv.iloc[100:], calm.iloc[100:])[0, 1]
    corr_wild = np.corrcoef(iv.iloc[100:], wild.iloc[100:])[0, 1]
    assert corr_calm > corr_wild


def test_inverse_vol_no_lookahead() -> None:
    """Weights are lagged — the combined value at t must not depend on
    any signal value after t."""
    idx = _idx(200)
    rng = np.random.default_rng(1)
    a = pd.Series(rng.normal(0, 50, 200), index=idx)
    b = pd.Series(rng.normal(0, 50, 200), index=idx)
    base = inverse_vol_weight({"a": _result(a), "b": _result(b)}, window=63)

    a_pert = a.copy()
    a_pert.iloc[-1] += 1e6  # perturb the very last bar
    pert = inverse_vol_weight({"a": _result(a_pert), "b": _result(b)}, window=63)

    # everything before the last bar is unchanged
    pd.testing.assert_series_equal(base.iloc[:-1], pert.iloc[:-1])


def test_portfolio_table_has_all_books() -> None:
    idx = _idx(120)
    rng = np.random.default_rng(2)
    results = {
        "RV1": _result(pd.Series(rng.normal(20, 100, 120), index=idx)),
        "RV2": _result(pd.Series(rng.normal(15, 100, 120), index=idx)),
        "RV3": _result(pd.Series(rng.normal(5, 100, 120), index=idx)),
    }
    table = portfolio_table(results)
    books = set(table["book"])
    assert {"RV1", "RV2", "RV3",
            "portfolio_equal_weight", "portfolio_inverse_vol"} == books
