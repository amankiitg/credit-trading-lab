"""Benchmark comparisons — buy-hold HYG and the random-entry baseline.

C29: Strategy B's net Sharpe must exceed the 95th percentile of the
Sprint-1 random-entry baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import sharpe

TRADING_DAYS: int = 252


def random_p95_sharpe(
    random_baseline: pd.DataFrame,
    spread: str = "hy_spread",
) -> float:
    """95th-percentile Sharpe across random-entry baseline paths for
    the given spread."""
    sub = random_baseline[random_baseline["spread"] == spread]
    if sub.empty:
        raise ValueError(f"no random-baseline rows for spread={spread!r}")
    return float(np.percentile(sub["sharpe"].to_numpy(dtype="float64"), 95))


def trade_sharpe(trades: pd.DataFrame) -> float:
    """Per-trade Sharpe on the *same basis* as the Sprint-1 random
    baseline: ``mean(net_pnl) / std(net_pnl) * sqrt(n_trades)``.

    The Sprint-1 baseline stores ``sharpe = mean/std*sqrt(n_trades)``
    (see signals/benchmarks.py). The C29 comparison must use this
    basis — an annualised daily Sharpe is a different number and not
    comparable to the random baseline.
    """
    pnl = trades["net_pnl"].to_numpy(dtype="float64")
    n = len(pnl)
    if n < 2:
        return 0.0
    sd = pnl.std(ddof=1)
    if sd == 0.0:
        return 0.0
    return float(pnl.mean() / sd * np.sqrt(n))


def excess_sharpe(strategy_sharpe: float, random_p95: float) -> float:
    """Strategy Sharpe minus the random-baseline 95th-percentile Sharpe."""
    return strategy_sharpe - random_p95


def buy_hold_hyg(features: pd.DataFrame) -> tuple[pd.Series, float]:
    """Buy-and-hold HYG benchmark.

    Returns the cumulative-log-return equity curve and the annualised
    Sharpe of HYG's daily log returns over the sample.
    """
    daily = features["HYG_log_ret"].fillna(0.0)
    equity = daily.cumsum().rename("buy_hold_hyg")
    return equity, sharpe(daily)


def vs_random(
    trades: pd.DataFrame,
    random_baseline: pd.DataFrame,
    spread: str = "hy_spread",
) -> dict[str, float]:
    """Compare a strategy's per-trade Sharpe to the random p95.

    Both sides use ``mean/std*sqrt(n_trades)`` — the basis the
    Sprint-1 random baseline was built on.
    """
    p95 = random_p95_sharpe(random_baseline, spread)
    strat = trade_sharpe(trades)
    return {
        "random_p95_sharpe": p95,
        "strategy_trade_sharpe": strat,
        "excess_sharpe": excess_sharpe(strat, p95),
    }
