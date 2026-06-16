"""NAV wedge signal construction for sprint v7.1.

wedge_t = close_t / nav_t - 1
z_wedge_t = (wedge_t - mean(wedge, window)) / std(wedge, window)

Both transforms are ratios and trailing rolling moments only. No
regression, no fitted intercept, no rolling beta anywhere in this
module, per the v7.1 PRD house rules (sprints/v7.1/PRD.md).

This module also carries the G0b (date alignment) and G0c (end-of-day
striking) gate-check functions, and the S1b guardrail text. The G0a
data-availability probe itself lives in scripts/probe_nav.py.

As of the v7.1 T1 probe, G0a failed: daily NAV is not retrievable via a
free, scriptable endpoint (see data/processed/nav_audit.md). The
functions below have no real NAV series to operate on yet and are
validated against synthetic fixtures in tests/test_nav_wedge.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_WINDOW = 63  # trading days, pre-registered in sprints/v7.1/PRD.md, not tuned

S1B_STATEMENT = (
    "z_wedge stationarity, if observed, is not evidence of tradeability. "
    "No IC test, no backtest, and no Sharpe/hit-rate claim is in scope for v7.1."
)


def compute_wedge(close: pd.Series, nav: pd.Series) -> pd.Series:
    """wedge_t = close_t / nav_t - 1, no fitted parameters."""
    aligned_close, aligned_nav = close.align(nav, join="inner")
    if aligned_nav.eq(0).any():
        raise ValueError("nav contains zero values, cannot compute wedge")
    wedge = aligned_close / aligned_nav - 1.0
    wedge.name = "wedge"
    return wedge


def compute_z_wedge(wedge: pd.Series, window: int = DEFAULT_WINDOW) -> pd.Series:
    """Strictly trailing z-score of wedge, right-aligned rolling window.

    z_wedge_t uses only wedge[t-window+1 .. t]. Values before
    min_periods=window are NaN by construction -- no look-ahead.
    """
    mu = wedge.rolling(window, min_periods=window, center=False).mean()
    sd = wedge.rolling(window, min_periods=window, center=False).std()
    z = (wedge - mu) / sd
    z.name = "z_wedge"
    return z


def check_date_alignment(nav: pd.Series, close: pd.Series, max_lag: int = 2) -> pd.DataFrame:
    """G0b: lag-correlation of daily returns between NAV and close.

    Returns a frame indexed by lag in [-max_lag, max_lag] with the
    correlation of nav_return[t] vs close_return[t + lag]. The gate
    passes only if the correlation peaks at lag 0 -- a peak away from
    zero indicates a systematic date-shift in the NAV series.
    """
    nav_ret = nav.pct_change().dropna()
    close_ret = close.pct_change().dropna()
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        shifted = close_ret.shift(-lag)
        joined = pd.concat([nav_ret, shifted], axis=1, join="inner").dropna()
        joined.columns = ["nav_ret", "close_ret"]
        corr = joined["nav_ret"].corr(joined["close_ret"]) if len(joined) > 2 else np.nan
        rows.append({"lag": lag, "corr": corr, "n_obs": len(joined)})
    return pd.DataFrame(rows).set_index("lag")


def g0b_passes(lag_corr: pd.DataFrame) -> bool:
    """True if the correlation table peaks at lag 0."""
    return int(lag_corr["corr"].idxmax()) == 0


def check_eod_striking(nav: pd.Series, reference: dict) -> pd.DataFrame:
    """G0c: spot-check NAV against an independently sourced EOD reference.

    reference maps date -> independently sourced EOD NAV value. Returns
    a frame of (date, our_nav, reference_nav, diff) for every date in
    reference.
    """
    rows = []
    for raw_date, ref_value in reference.items():
        ts = pd.Timestamp(raw_date)
        our_value = nav.loc[ts] if ts in nav.index else np.nan
        rows.append(
            {
                "date": ts,
                "our_nav": our_value,
                "reference_nav": ref_value,
                "diff": our_value - ref_value,
            }
        )
    return pd.DataFrame(rows).set_index("date").sort_index()


def g0c_passes(spot_check: pd.DataFrame, tol: float = 0.01) -> bool:
    """True if every spot-checked date matches the reference within tol."""
    if spot_check["diff"].isna().any():
        return False
    return bool((spot_check["diff"].abs() <= tol).all())
