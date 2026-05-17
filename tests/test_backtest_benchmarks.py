"""W7 — backtest benchmark comparisons (buy-hold, random p95)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.benchmarks import (
    buy_hold_hyg,
    excess_sharpe,
    random_p95_sharpe,
    trade_sharpe,
    vs_random,
)


def _random_baseline() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "spread": ["hy_spread"] * 100 + ["ig_spread"] * 100,
        "sharpe": np.concatenate([rng.normal(0, 1, 100), rng.normal(5, 1, 100)]),
    })


def test_random_p95_is_95th_percentile() -> None:
    rb = _random_baseline()
    hy = rb[rb["spread"] == "hy_spread"]["sharpe"]
    assert random_p95_sharpe(rb, "hy_spread") == pytest.approx(
        np.percentile(hy, 95), rel=1e-9
    )


def test_random_p95_selects_correct_spread() -> None:
    rb = _random_baseline()
    # ig_spread rows are centered at 5 → p95 clearly higher
    assert random_p95_sharpe(rb, "ig_spread") > random_p95_sharpe(rb, "hy_spread")


def test_random_p95_unknown_spread_raises() -> None:
    with pytest.raises(ValueError):
        random_p95_sharpe(_random_baseline(), "nonexistent")


def test_trade_sharpe_formula() -> None:
    # mean/std * sqrt(n) — the Sprint-1 random-baseline basis
    trades = pd.DataFrame({"net_pnl": [100.0, 200.0, 150.0, 50.0, 300.0]})
    pnl = trades["net_pnl"].to_numpy()
    expected = pnl.mean() / pnl.std(ddof=1) * np.sqrt(len(pnl))
    assert trade_sharpe(trades) == pytest.approx(expected, rel=1e-9)


def test_trade_sharpe_degenerate_safe() -> None:
    assert trade_sharpe(pd.DataFrame({"net_pnl": [10.0]})) == 0.0          # n<2
    assert trade_sharpe(pd.DataFrame({"net_pnl": [5.0, 5.0, 5.0]})) == 0.0  # zero std


def test_excess_sharpe() -> None:
    assert excess_sharpe(2.0, 1.5) == pytest.approx(0.5)
    assert excess_sharpe(1.0, 1.7) == pytest.approx(-0.7)


def test_vs_random_structure() -> None:
    rb = _random_baseline()
    trades = pd.DataFrame({"net_pnl": [100.0, 200.0, 150.0, 50.0, 300.0]})
    out = vs_random(trades, rb, "hy_spread")
    assert set(out) == {"random_p95_sharpe", "strategy_trade_sharpe", "excess_sharpe"}
    assert out["excess_sharpe"] == pytest.approx(
        out["strategy_trade_sharpe"] - out["random_p95_sharpe"], rel=1e-9
    )


def test_buy_hold_hyg() -> None:
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    feats = pd.DataFrame({"HYG_log_ret": [np.nan] + [0.01] * 9}, index=idx)
    equity, shp = buy_hold_hyg(feats)
    # first NaN filled to 0 → equity ends at 9 * 0.01
    assert equity.iloc[-1] == pytest.approx(0.09, abs=1e-12)
    assert np.isfinite(shp)
