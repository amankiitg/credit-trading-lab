"""Live risk module -- MCTR/PCTR from a rolling covariance, sprint v9.1.

Pure functions: no I/O, no Supabase. Computes marginal contribution to
total risk (MCTR) and percentage contribution to total risk (PCTR) from
a vector of portfolio weights and a trailing covariance matrix.

    Sigma     = annualise( cov(returns_{t-N+1:t}, ddof=1) ),  x252
    sigma_p   = sqrt(w' Sigma w)
    MCTR_i    = (Sigma w)_i / sigma_p          marginal contribution (annualised)
    CTR_i     = w_i * MCTR_i                   contribution, sums to sigma_p
    PCTR_i    = CTR_i / sigma_p                sums to 1.0

Weights are signed (negative for shorts); the covariance sees signed
weights, so a short that is correlated with the book reduces PCTR
(sign-correct Euler decomposition per Paleologo).

No zero-padding. The covariance is estimated only from tickers whose
returns are fully observed over the window. A ticker with a shorter
history, or a hole inside the window, is *excluded* and named, never
filled with a zero covariance or a padded price: a padded price is a
daily return of exactly 0.0, which drags its variance and every
covariance it appears in toward zero while still letting the ticker carry
its full weight in the portfolio variance. That is a measurement, not an
estimate, and it is what made the panel's equity share report 79% of the
book's risk on 2026-10-30 (see tests/test_live_covariance.py).

Gate A1: PCTR sum == 1.0 within 1e-6.
Gate A3: dropping day t from the return window changes MCTR (anchored at t).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS: int = 252

# Sessions in the covariance window. 120 is the signal's own trend lookback
# (signals.trend_signal L=120), so the risk panel measures the book over the same
# horizon the position was chosen on. It is also the horizon at which the sleeve
# flip of 2026-10-30 -> 2026-11-02 reads correctly: a 63-session window is short
# enough that the equity sleeve's share never crossed 50% and the diagnosis
# (79% -> 7%) was invisible on it.
COV_WINDOW_SESSIONS: int = 120

# A ticker needs at least this many returns inside the window to be estimated.
MIN_RETURN_OBS: int = 20

# Tolerances
PCTR_SUM_TOL: float = 1e-6
MCTR_CLOSED_FORM_TOL: float = 1e-10


def select_eligible_returns(
    returns: pd.DataFrame,
    min_obs: int = MIN_RETURN_OBS,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Split a return panel into the columns a covariance can be built from.

    A column is eligible only when it is *complete* over the window and has at
    least ``min_obs`` observations. Incomplete columns are returned separately
    with the reason, so a caller can name them instead of silently attributing a
    zero covariance to them.

    Returns
    -------
    (eligible, excluded)
        ``eligible`` is ``returns`` restricted to the usable columns.
        ``excluded`` maps ticker -> plain-English reason.
    """
    n = len(returns)
    eligible: list[str] = []
    excluded: dict[str, str] = {}
    for col in returns.columns:
        s = returns[col]
        count = int(s.notna().sum())
        if count < max(min_obs, 2):
            excluded[col] = f"insufficient history ({count} of {n} returns)"
        elif s.isna().any():
            excluded[col] = f"incomplete history ({count} of {n} returns)"
        else:
            eligible.append(col)
    return returns[eligible].copy(), excluded


def mctr_pctr(
    weights: pd.Series,
    returns: pd.DataFrame,
    annualize: bool = True,
) -> pd.DataFrame:
    """Compute MCTR and PCTR for a portfolio snapshot.

    Parameters
    ----------
    weights : Series, index = ticker
        Signed portfolio weights (positive = long, negative = short).
        Must be defined (no NaN) for every ticker in ``returns``.
    returns : DataFrame, index = date (most recent last), columns = ticker
        Trailing daily simple returns for one window, fully observed: every
        column complete over every row. Use :func:`select_eligible_returns`
        first; this function refuses to invent the missing entries.
    annualize : bool
        If True, covariance scaled by 252 (annualised risk).

    Returns
    -------
    DataFrame with columns: ticker, weight, mctr, ctr, pctr.
    Index = ticker.

    Raises
    ------
    ValueError if a weight is missing or the return panel has a hole.
    AssertionError if PCTR does not sum to 1.0 within PCTR_SUM_TOL.
    """
    tickers = list(weights.index)
    missing_weights = [t for t in tickers if pd.isna(weights[t])]
    if missing_weights:
        raise ValueError(f"weights undefined for {missing_weights}")

    absent = [t for t in tickers if t not in returns.columns]
    if absent:
        raise ValueError(f"no returns for {absent} -- exclude them, do not price them at zero")

    # Align returns columns to weight tickers
    rets = returns[tickers]
    w = weights[tickers].to_numpy(dtype="float64")

    holes = [c for c in rets.columns if rets[c].isna().any()]
    if holes:
        raise ValueError(
            f"returns have holes for {holes} -- exclude them via "
            "select_eligible_returns(); a zero-filled covariance is not an estimate"
        )

    # Sample covariance, pairwise (and here complete) observations
    Sigma = rets.cov()
    if annualize:
        Sigma = Sigma * TRADING_DAYS

    Sigma_arr = Sigma.to_numpy(dtype="float64")

    # Portfolio vol: sigma_p = sqrt(w' Sigma w)
    sigma2_p = float(w.T @ Sigma_arr @ w)
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
    mctr_arr = (Sigma_arr @ w) / sigma_p

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


def risk_contributions(
    weights: pd.Series,
    returns: pd.DataFrame,
    annualize: bool = True,
    min_obs: int = MIN_RETURN_OBS,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """MCTR/PCTR over the tickers that actually have a history in the window.

    Weights for excluded tickers are dropped too: their risk is not estimated, so
    including their weight would attribute a share of the book to a covariance
    nobody measured. The caller is expected to surface ``excluded`` -- the panel
    prints "risk not available for X (insufficient history)" for each one.

    Returns
    -------
    (contributions, excluded)
        ``contributions`` is the :func:`mctr_pctr` frame over eligible tickers.
        ``excluded`` maps ticker -> reason. A ticker with a zero weight is not a
        loss of information and is not reported.
    """
    usable_cols = [c for c in returns.columns if c in weights.index]
    eligible, excluded = select_eligible_returns(returns[usable_cols], min_obs=min_obs)

    # A ticker we never held is irrelevant to the book's risk; do not report it.
    trivial = [t for t in excluded if float(weights.get(t, 0.0) or 0.0) == 0.0]
    for t in trivial:
        excluded.pop(t, None)

    kept = [t for t in weights.index if t in eligible.columns]
    if not kept:
        raise ValueError(
            "no ticker has a complete return history in the window: "
            + "; ".join(f"{t} ({why})" for t, why in excluded.items())
        )

    return mctr_pctr(weights[kept], eligible[kept], annualize=annualize), excluded
