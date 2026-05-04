"""Relative-value signals — OLS / Kalman / DV01 hedge methods.

For each (y, x) pair, returns ``(residual, hedge_ratio)`` aligned to
``y``'s index. All methods are trailing-only — at time t, β_t uses
only data {..., t-1, t}.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OLS_WINDOW = 126


def ols_hedge(y: pd.Series, x: pd.Series, window: int = OLS_WINDOW) -> tuple[pd.Series, pd.Series]:
    """Rolling OLS hedge: y_t = α_t + β_t · x_t + ε_t over a trailing window.

    Returns (residual, hedge_ratio) Series aligned to y.index. Both
    are NaN until the first full window is available.
    """
    if not y.index.equals(x.index):
        x = x.reindex(y.index)

    mean_x = x.rolling(window, min_periods=window).mean()
    mean_y = y.rolling(window, min_periods=window).mean()
    var_x = x.rolling(window, min_periods=window).var(ddof=0)
    cov_xy = x.rolling(window, min_periods=window).cov(y, ddof=0)

    beta = cov_xy / var_x
    alpha = mean_y - beta * mean_x
    residual = y - (alpha + beta * x)

    # Mask warmup explicitly (rolling already returns NaN, but clarify)
    residual = residual.where(beta.notna())
    return residual, beta


def kalman_hedge(
    y: pd.Series,
    x: pd.Series,
    Q: float = 1e-5,
    init_window: int = 63,
) -> tuple[pd.Series, pd.Series]:
    """Two-state Kalman hedge with random-walk α, β.

    State s_t = [α_t, β_t]^T ; s_t = s_{t-1} + η_t,  η ~ N(0, Q·I).
    Observation: y_t = [1, x_t] · s_t + ε_t,  ε ~ N(0, R).

    Initial state from OLS on the first ``init_window`` valid rows.
    R estimated from that OLS residual variance.

    Returns (residual, β_t) Series aligned to y.index. Values prior
    to init_window are NaN.
    """
    if not y.index.equals(x.index):
        x = x.reindex(y.index)

    n = len(y)
    yv = y.to_numpy(dtype="float64")
    xv = x.to_numpy(dtype="float64")
    valid = np.isfinite(yv) & np.isfinite(xv)
    if valid.sum() < init_window:
        raise ValueError(f"need ≥{init_window} valid rows for Kalman init")

    cum_valid = np.cumsum(valid)
    init_end = int(np.searchsorted(cum_valid, init_window))
    init_mask = valid.copy()
    init_mask[init_end + 1 :] = False
    A = np.column_stack([np.ones(init_mask.sum()), xv[init_mask]])
    coef, *_ = np.linalg.lstsq(A, yv[init_mask], rcond=None)
    s = coef.astype("float64")  # [α, β]
    R = float(np.var(yv[init_mask] - A @ coef, ddof=1)) or 1e-12

    P = np.eye(2, dtype="float64")
    Qm = Q * np.eye(2, dtype="float64")
    beta = np.full(n, np.nan, dtype="float64")
    resid = np.full(n, np.nan, dtype="float64")

    for t in range(init_end + 1, n):
        if not valid[t]:
            beta[t] = s[1]
            continue
        P_pred = P + Qm
        H = np.array([1.0, xv[t]])
        S = float(H @ P_pred @ H + R)
        K = (P_pred @ H) / S
        innov = yv[t] - H @ s
        s = s + K * innov
        P = (np.eye(2) - np.outer(K, H)) @ P_pred
        beta[t] = s[1]
        resid[t] = yv[t] - H @ s

    return (
        pd.Series(resid, index=y.index, name="kalman_residual"),
        pd.Series(beta, index=y.index, name="kalman_beta"),
    )
