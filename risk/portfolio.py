"""Multi-signal portfolio — combine RV1/RV2/RV3 into one book.

Two weighting schemes:
  - equal weight: 1/N of each signal's daily P&L.
  - inverse volatility: weight ∝ 1/σ, σ = trailing std of the
    signal's daily P&L, lagged one day (no look-ahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult

VOL_WINDOW: int = 63


def _aligned_pnl(results: dict[str, BacktestResult]) -> pd.DataFrame:
    """Stack per-signal daily P&L into one aligned frame."""
    return pd.DataFrame({name: r.daily_pnl for name, r in results.items()}).fillna(0.0)


def equal_weight(results: dict[str, BacktestResult]) -> pd.Series:
    """Equal-weight portfolio daily P&L — mean across signals."""
    pnl = _aligned_pnl(results)
    return pnl.mean(axis=1).rename("portfolio_equal_weight")


def inverse_vol_weight(
    results: dict[str, BacktestResult],
    window: int = VOL_WINDOW,
) -> pd.Series:
    """Inverse-volatility portfolio daily P&L.

    Weight on signal i at day t ∝ 1 / σ_i, where σ_i is the trailing
    ``window``-day std of signal i's daily P&L through t-1 (lagged to
    avoid look-ahead). Weights are renormalised to sum to 1 each day.
    Before the first full window, falls back to equal weight.
    """
    pnl = _aligned_pnl(results)
    vol = pnl.rolling(window, min_periods=window).std().shift(1)
    inv = 1.0 / vol.replace(0.0, np.nan)
    weights = inv.div(inv.sum(axis=1), axis=0)
    # equal-weight fallback while the trailing window is still warming up
    n = pnl.shape[1]
    weights = weights.fillna(1.0 / n)
    combined = (pnl * weights).sum(axis=1)
    return combined.rename("portfolio_inverse_vol")


def portfolio_table(
    results: dict[str, BacktestResult],
    window: int = VOL_WINDOW,
) -> pd.DataFrame:
    """Sharpe of each single signal and of both portfolio schemes."""
    from backtest.metrics import sharpe

    rows: list[dict[str, object]] = []
    for name, r in results.items():
        rows.append({"book": name, "sharpe": sharpe(r.daily_pnl)})
    ew = equal_weight(results)
    iv = inverse_vol_weight(results, window=window)
    rows.append({"book": "portfolio_equal_weight", "sharpe": sharpe(ew)})
    rows.append({"book": "portfolio_inverse_vol", "sharpe": sharpe(iv)})
    return pd.DataFrame(rows)
