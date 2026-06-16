"""Time-series trend signal and vol-targeted position sizing, sprint v8.1.

No edge claim. This is a mechanical, fully pre-registered rule used to
exercise universe loading, vol targeting, and leverage-capped position
construction -- not a predictive-signal validation
(sprints/v8.1/PRD.md, House Rule 1). No IC test, no Sharpe claim.

    trail_ret_i(t) = close_i(t) / close_i(t-L) - 1            L = 120 trading days
    signal_i(t)    = 1 if trail_ret_i(t) > 0 else 0            long-only / flat
    sigma_i(t)     = std(log_ret_i, window=W) * sqrt(252)      W = 63 trading days
    raw_weight_i(t) = signal_i(t) * min(v / sigma_i(t), w_max)  v = 0.10, w_max = 0.50
    weight_i(t)     = raw_weight_i(t) * scale(t)                scale caps gross at g_max = 2.0

All parameters are pre-registered in the PRD and fixed for this sprint --
not retuned after looking at output (House Rule 5). A cell is left NaN
(rather than 0) until both the L-day and W-day warmup are satisfied, so
a ticker only enters the book once it has enough real history -- no
synthetic backfill (House Rule 4 / gate E5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

L_DEFAULT = 120     # trend lookback, trading days
W_DEFAULT = 63       # vol estimation window, trading days
V_DEFAULT = 0.10      # target annualized vol per active name
W_MAX_DEFAULT = 0.50  # per-name weight cap
G_MAX_DEFAULT = 2.0   # gross leverage cap


def compute_trend(
    close: pd.DataFrame,
    L: int = L_DEFAULT,
    W: int = W_DEFAULT,
    v: float = V_DEFAULT,
    w_max: float = W_MAX_DEFAULT,
    g_max: float = G_MAX_DEFAULT,
) -> pd.DataFrame:
    """Build the tidy (long) target-position frame from a close matrix.

    `close` is a date x ticker DataFrame (ascending, unique date index),
    leading NaN per column allowed for staggered inception.

    Returns one row per (date, ticker) with columns:
    date, ticker, adj_close, trail_ret, signal, sigma, raw_weight,
    gross, scale, weight.

    `weight` is the position computed from data through that row's date.
    Use `shift_to_next_day` to label it as the target for the following
    trading day, per the PRD's `target_position_vector(t+1)` convention.
    """
    log_ret = np.log(close).diff()
    trail_ret = close / close.shift(L) - 1.0
    sigma = log_ret.rolling(W, min_periods=W).std() * np.sqrt(TRADING_DAYS)

    defined = trail_ret.notna() & sigma.notna()
    signal = (trail_ret > 0).astype(float).where(defined)

    raw_weight = signal * (v / sigma).clip(upper=w_max)

    gross = raw_weight.abs().sum(axis=1, skipna=True)
    scale = (g_max / gross).clip(upper=1.0)
    scale = scale.where(gross > 0, 1.0)

    weight = raw_weight.mul(scale, axis=0).where(defined)

    frames = []
    for t in close.columns:
        frames.append(
            pd.DataFrame(
                {
                    "date": close.index,
                    "ticker": t,
                    "adj_close": close[t].to_numpy(),
                    "trail_ret": trail_ret[t].to_numpy(),
                    "signal": signal[t].to_numpy(),
                    "sigma": sigma[t].to_numpy(),
                    "raw_weight": raw_weight[t].to_numpy(),
                    "gross": gross.to_numpy(),
                    "scale": scale.to_numpy(),
                    "weight": weight[t].to_numpy(),
                }
            )
        )
    tidy = pd.concat(frames, ignore_index=True)
    return tidy.sort_values(["date", "ticker"]).reset_index(drop=True)


def to_position_matrix(tidy: pd.DataFrame) -> pd.DataFrame:
    """Pivot the tidy frame to a date x ticker weight matrix."""
    return tidy.pivot(index="date", columns="ticker", values="weight").sort_index()


def shift_to_next_day(position_matrix: pd.DataFrame) -> pd.DataFrame:
    """Relabel weights computed from close(t) as the target for t+1.

    `result.loc[date_k] == position_matrix.loc[date_{k-1}]` for every
    row k > 0 -- the weight computed from data through the previous row's
    close becomes the target position for the current row's date.
    """
    out = position_matrix.shift(1)
    out.index.name = "target_date"
    return out
