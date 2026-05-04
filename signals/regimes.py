"""Regime classifiers — vol, equity, equity-credit lag.

Each classifier labels every trading day with a categorical regime,
using only information available at time t (trailing-only, no
``center=True``, no future data).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def vol_regime(df: pd.DataFrame, window: int = 63) -> pd.Series:
    """Label each day ``high`` or ``low`` vol.

    Compares ``SPY_vol_{window}`` to its expanding median (trailing).
    Days where the rolling vol or expanding median is undefined are NaN.
    """
    col = f"SPY_vol_{window}"
    vol = df[col]
    expanding_median = vol.expanding(min_periods=window).median()
    out = pd.Series(np.where(vol > expanding_median, "high", "low"), index=df.index)
    out[vol.isna() | expanding_median.isna()] = np.nan
    return out


def equity_regime(df: pd.DataFrame, window: int = 63) -> pd.Series:
    """Label each day ``bull`` or ``bear`` based on rolling cumulative SPY return.

    ``bull`` if the trailing ``window``-day sum of ``SPY_log_ret`` > 0.
    """
    cum = df["SPY_log_ret"].rolling(window, min_periods=window).sum()
    out = pd.Series(np.where(cum > 0, "bull", "bear"), index=df.index)
    out[cum.isna()] = np.nan
    return out


def equity_credit_lag(
    df: pd.DataFrame,
    xcorr_window: int = 21,
    max_lag: int = 5,
    noise_floor: float = 0.15,
) -> pd.Series:
    """Label each day ``equity_first``, ``credit_first``, or ``neither``.

    For each t, computes cross-correlation of SPY_log_ret vs
    Δhy_spread at lags k ∈ [-max_lag, +max_lag]. The lag k* with the
    largest |xcorr| determines the label:
      - k* > 0 → ``equity_first`` (equity at τ correlates with credit at τ+k)
      - k* < 0 → ``credit_first``
      - k* = 0 OR max|xcorr| < noise_floor → ``neither``

    Trailing-only: to test "equity leads credit by k>0" at time t we
    pair spy_τ with dhy_{τ+k} for τ ∈ {t-w+1-k .. t-k}; both ends of
    that pair are ≤ t. We achieve this by shifting the earlier-time
    series forward: for k≥0, shift spy by +k; for k<0, shift dhy by
    -k. No future data enters the window for any lag.
    """
    spy = df["SPY_log_ret"]
    dhy = df["hy_spread"].diff()

    lags = list(range(-max_lag, max_lag + 1))
    xcorrs: dict[int, pd.Series] = {}
    for k in lags:
        if k >= 0:
            xcorrs[k] = spy.shift(k).rolling(xcorr_window, min_periods=xcorr_window).corr(dhy)
        else:
            xcorrs[k] = dhy.shift(-k).rolling(xcorr_window, min_periods=xcorr_window).corr(spy)

    xcorr_df = pd.DataFrame(xcorrs)  # columns are integer lags

    # For each row, find the lag with max |xcorr|.
    abs_xc = xcorr_df.abs()
    valid = abs_xc.notna().all(axis=1)
    best_lag = pd.Series(np.nan, index=df.index, dtype="float64")
    best_abs = pd.Series(np.nan, index=df.index, dtype="float64")
    if valid.any():
        sub = abs_xc.loc[valid]
        best_lag.loc[valid] = sub.idxmax(axis=1).astype("float64")
        best_abs.loc[valid] = sub.max(axis=1)

    labels = pd.Series(np.nan, index=df.index, dtype="object")
    is_neither = (best_abs < noise_floor) | (best_lag == 0)
    labels[valid & is_neither] = "neither"
    labels[valid & ~is_neither & (best_lag > 0)] = "equity_first"
    labels[valid & ~is_neither & (best_lag < 0)] = "credit_first"
    return labels
