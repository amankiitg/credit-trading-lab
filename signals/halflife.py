"""Ornstein-Uhlenbeck half-life estimation.

Discrete-time AR(1) regression: Δr_t = a + b · r_{t-1} + ε_t.
For mean-reverting series b < 0; half-life = -ln(2) / b in the
sample's time unit (trading days here). If b ≥ 0 the series is
explosive or random-walking; we return ``np.inf``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ou_halflife(series: pd.Series) -> float:
    """OLS estimate of OU half-life (in trading days).

    Returns ``np.inf`` if the slope is non-negative (no mean reversion)
    or the regression is degenerate.
    """
    s = series.dropna().to_numpy(dtype="float64")
    if len(s) < 30:
        return float("inf")
    lag = s[:-1]
    diff = np.diff(s)
    A = np.column_stack([np.ones_like(lag), lag])
    coef, *_ = np.linalg.lstsq(A, diff, rcond=None)
    b = float(coef[1])
    if b >= 0 or not np.isfinite(b):
        return float("inf")
    return float(-np.log(2.0) / b)
