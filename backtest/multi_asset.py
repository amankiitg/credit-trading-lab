"""Daily P&L accumulator for a continuously-weighted multi-asset book.

backtest.engine.run() assumes a single pair, one hedge ratio, and discrete
{-1, 0, +1} positions with a round-trip trade ledger -- it does not fit an
8-name, continuously fractional-weighted, daily-rebalanced book (see
sprints/v8.2/PRD.md, Research Architecture: this mismatch is a pre-
registered design decision, not a mid-sprint discovery). This module
reuses the same v6.5 cost-model constants (execution.costs.CostParams) via
a turnover-based daily cost instead of trade_cost()'s per-round-trip
formula.

    ret_i(t)       = close_i(t) / close_i(t-1) - 1
    daily_pnl(t)   = sum_i [ target_i(t) * ret_i(t) ] * notional - daily_cost(t)
    daily_cost(t)  = (half_spread_bp + slippage_bp) * 1e-4
                        * sum_i |target_i(t) - target_i(t-1)| * notional
                     + borrow_annual / 252 * notional
                        * sum_i max(-target_i(t), 0)

`target` must already be the position actually HELD on each date (e.g. the
output of signals.trend_signal.shift_to_next_day) -- this module applies
no further look-ahead shift. Passing an un-shifted `weight` matrix (the
position computed FROM data through that date's close, per
signals.trend_signal.compute_trend) would let target_i(t) trade on
ret_i(t), the same day's return -- a look-ahead bug, not a feature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from execution.costs import CostParams

TRADING_DAYS = 252
_BP = 1e-4


@dataclass(frozen=True)
class MultiAssetResult:
    daily_pnl: pd.Series
    equity: pd.Series
    turnover_cost: pd.Series
    borrow_cost: pd.Series
    daily_return: pd.Series  # daily_pnl / notional, for Sharpe/vol on a return basis


def run_multi_asset(
    target: pd.DataFrame,
    close: pd.DataFrame,
    notional: float = 1_000_000.0,
    cost_params: CostParams = CostParams(),
) -> MultiAssetResult:
    """Daily P&L for a continuously-weighted multi-asset book.

    `target` and `close` must share the same columns (tickers); dates are
    aligned via inner join on the index.
    """
    common_cols = [c for c in close.columns if c in target.columns]
    common_idx = target.index.intersection(close.index)
    target = target.loc[common_idx, common_cols].sort_index()
    close = close.loc[common_idx, common_cols].sort_index()

    ret = close.pct_change(fill_method=None)

    pnl_gross = (target * ret).sum(axis=1, skipna=True) * notional

    delta = target.diff().abs().sum(axis=1, skipna=True)
    turnover_cost = (cost_params.half_spread_bp + cost_params.slippage_bp) * _BP * delta * notional

    short_exposure = target.clip(upper=0.0).abs().sum(axis=1, skipna=True)
    borrow_cost = cost_params.borrow_annual / TRADING_DAYS * notional * short_exposure

    daily_cost = turnover_cost + borrow_cost
    daily_pnl = pnl_gross - daily_cost
    equity = daily_pnl.cumsum()
    daily_return = daily_pnl / notional

    return MultiAssetResult(
        daily_pnl=daily_pnl,
        equity=equity,
        turnover_cost=turnover_cost,
        borrow_cost=borrow_cost,
        daily_return=daily_return,
    )


def annualized_turnover(target: pd.DataFrame) -> float:
    """Annualized turnover: mean daily sum_i |Delta target_i(t)|, * 252.

    Interpreted as "the book replaces this many multiples of its own
    notional per year." Not "trades per year" (backtest.metrics.summary's
    definition) -- this book has no discrete trades.
    """
    daily_turnover = target.diff().abs().sum(axis=1, skipna=True)
    return float(daily_turnover.mean() * TRADING_DAYS)


def annualized_return(daily_pnl: pd.Series, notional: float = 1_000_000.0) -> float:
    return float(daily_pnl.mean() / notional * TRADING_DAYS)


def annualized_vol(daily_pnl: pd.Series, notional: float = 1_000_000.0) -> float:
    x = (daily_pnl / notional).to_numpy(dtype="float64")
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return 0.0
    return float(x.std(ddof=1) * np.sqrt(TRADING_DAYS))
