"""Strategy performance metrics.

All ratio metrics annualise with √252. Inputs are the daily net-P&L
series and the trade ledger produced by ``backtest.engine.run``.
Dollar P&L is used directly — the notional cancels in every ratio.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TRADING_DAYS: int = 252


@dataclass(frozen=True)
class Summary:
    sharpe: float
    sortino: float
    hit_rate: float
    turnover: float            # trades per year
    max_drawdown: float        # signed, <= 0 (dollars)
    avg_holding_days: float
    total_net_pnl: float
    n_trades: int

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def sharpe(daily_pnl: pd.Series) -> float:
    """Annualised Sharpe of a daily P&L series. Returns 0.0 if the
    series has zero variance (e.g. never traded)."""
    x = daily_pnl.to_numpy(dtype="float64")
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    if sd == 0.0:
        return 0.0
    return float(x.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(daily_pnl: pd.Series, target: float = 0.0) -> float:
    """Annualised Sortino — mean excess over ``target`` divided by the
    downside deviation (RMS of below-target observations)."""
    x = daily_pnl.to_numpy(dtype="float64")
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return 0.0
    downside = np.minimum(x - target, 0.0)
    dd = np.sqrt(np.mean(downside ** 2))
    if dd == 0.0:
        return 0.0
    return float((x.mean() - target) / dd * np.sqrt(TRADING_DAYS))


def max_drawdown(daily_pnl: pd.Series) -> float:
    """Largest peak-to-trough drop on the cumulative equity curve.

    Returned signed (<= 0). Zero if the equity curve never declines.
    """
    x = daily_pnl.to_numpy(dtype="float64")
    if len(x) == 0:
        return 0.0
    equity = np.cumsum(np.nan_to_num(x))
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    return float(drawdown.min())


def summary(
    daily_pnl: pd.Series,
    trades: pd.DataFrame,
    n_days: int | None = None,
) -> Summary:
    """Full metric bundle for one strategy."""
    n_trades = int(len(trades))
    if n_days is None:
        n_days = int(len(daily_pnl))

    if n_trades > 0:
        wins = (trades["net_pnl"] > 0).sum()
        hit = float(wins / n_trades)
        avg_hold = float(trades["holding_days"].mean())
        total_net = float(trades["net_pnl"].sum())
    else:
        hit = 0.0
        avg_hold = 0.0
        total_net = 0.0

    years = n_days / TRADING_DAYS if n_days > 0 else np.nan
    turnover = float(n_trades / years) if years and years > 0 else 0.0

    return Summary(
        sharpe=sharpe(daily_pnl),
        sortino=sortino(daily_pnl),
        hit_rate=hit,
        turnover=turnover,
        max_drawdown=max_drawdown(daily_pnl),
        avg_holding_days=avg_hold,
        total_net_pnl=total_net,
        n_trades=n_trades,
    )
