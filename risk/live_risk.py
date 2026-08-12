"""Live risk module -- MCTR/PCTR from rolling covariance, sprint v9.1.

Pure functions: no I/O, no Supabase. Computes marginal contribution to
total risk (MCTR) and percentage contribution to total risk (PCTR) from
a vector of portfolio weights and a 63-day trailing covariance matrix.

    Sigma     = annualise( cov(returns_{t-62:t}, ddof=1) ),  x252
    sigma_p   = sqrt(w' Sigma w)
    MCTR_i    = (Sigma w)_i / sigma_p          marginal contribution (annualised)
    CTR_i     = w_i * MCTR_i                   contribution, sums to sigma_p
    PCTR_i    = CTR_i / sigma_p                sums to 1.0

Weights are signed (negative for shorts); the covariance sees signed
weights, so a short that is correlated with the book reduces PCTR
(sign-correct Euler decomposition per Paleologo).

The 63-day window is the SAME window used for vol-targeting in
signals.trend_signal -- this module does not introduce a new free
parameter.

Gate A1: PCTR sum == 1.0 within 1e-6.
Gate A3: dropping day t from the return window changes MCTR (anchored at t).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS: int = 252
WINDOW: int = 63  # same as signals.trend_signal.W_DEFAULT

# Tolerances
PCTR_SUM_TOL: float = 1e-6
MCTR_CLOSED_FORM_TOL: float = 1e-10


def mctr_pctr(
    weights: pd.Series,
    returns_63d: pd.DataFrame,
    annualize: bool = True,
) -> pd.DataFrame:
    """Compute MCTR and PCTR for a portfolio snapshot.

    Parameters
    ----------
    weights : Series, index = ticker
        Signed portfolio weights (positive = long, negative = short).
        Must be defined (no NaN) for every ticker in returns_63d.
    returns_63d : DataFrame, index = date (most recent last), columns = ticker
        Trailing 63-day (or fewer) daily simple returns.  Missing values
        (e.g. for staggered ticker inception) are dropped pairwise in
        the covariance estimate via min_periods.
    annualize : bool
        If True, covariance scaled by 252 (annualised risk).

    Returns
    -------
    DataFrame with columns: ticker, weight, mctr, ctr, pctr.
    Index = ticker.

    Raises
    ------
    AssertionError if PCTR does not sum to 1.0 within PCTR_SUM_TOL.
    """
    tickers = list(weights.index)
    # Align returns columns to weight tickers
    rets = returns_63d[tickers].copy()
    w = weights[tickers].to_numpy(dtype="float64")

    # 63-day sample covariance, pairwise complete observations
    Sigma = rets.cov(min_periods=20)  # at least ~1 month for a valid estimate
    if annualize:
        Sigma = Sigma * TRADING_DAYS

    # Ensure Sigma is positive semi-definite by filling NaN with 0
    Sigma_filled = Sigma.fillna(0.0).to_numpy(dtype="float64")

    # Portfolio vol: sigma_p = sqrt(w' Sigma w)
    sigma2_p = float(w.T @ Sigma_filled @ w)
    if sigma2_p <= 0:
        # No risk: return zero contributions
        result = pd.DataFrame({
            "ticker": tickers,
            "weight": w,
            "mctr": 0.0,
            "ctr": 0.0,
            "pctr": np.nan,
        }).set_index("ticker")
        return result

    sigma_p = np.sqrt(sigma2_p)

    # MCTR_i = (Sigma w)_i / sigma_p
    mctr_arr = (Sigma_filled @ w) / sigma_p

    # CTR_i = w_i * MCTR_i
    ctr_arr = w * mctr_arr

    # PCTR_i = CTR_i / sigma_p
    pctr_arr = ctr_arr / sigma_p

    result = pd.DataFrame({
        "ticker": tickers,
        "weight": w,
        "mctr": mctr_arr,
        "ctr": ctr_arr,
        "pctr": pctr_arr,
    }).set_index("ticker")

    # Gate A1: PCTR must sum to 1.0
    pctr_sum = float(pctr_arr.sum())
    if abs(pctr_sum - 1.0) > PCTR_SUM_TOL:
        raise AssertionError(
            f"PCTR sum = {pctr_sum:.12f} != 1.0 (tol={PCTR_SUM_TOL})"
        )

    return result
