"""
Vol-scaled stop ladder overlay, sprint v9.1.

Pure state machine: episode detection, z-drawdown computation,
state transitions with hysteresis, and multiplier generation.
Functions are pure DataFrame-in/DataFrame-out. No I/O, no Supabase.

Pre-registered defaults:
  k1 = 1.5  (REDUCED threshold, z units)
  k2 = 2.5  (STOPPED threshold, z units)
  h  = 0.5  (hysteresis band, z units)
  m_reduced = 0.5  (multiplier in REDUCED state)

States: NORMAL (m=1.0) -> REDUCED (m=0.5) -> STOPPED (m=0.0)
Recovery: STOPPED -> REDUCED -> NORMAL via hysteresis bands.
Episode resets on signal sign flip (new anchor = new entry).

All quantities computed from close(t) and earlier; weights effective t+1
via the existing shift_to_next_day convention.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Pre-registered defaults (sprint v9.1 PRD)
K1_DEFAULT: float = 1.5
K2_DEFAULT: float = 2.5
H_DEFAULT: float = 0.5
M_REDUCED_DEFAULT: float = 0.5

TRADING_DAYS: int = 252
STATE_NAMES: dict[int, str] = {0: "NORMAL", 1: "REDUCED", 2: "STOPPED"}


def _compute_monthly_vol(sigma_annual: pd.DataFrame) -> pd.DataFrame:
    """Scale annualized 63d vol to 1-month horizon (sqrt(21/252))."""
    return sigma_annual * np.sqrt(21.0 / TRADING_DAYS)


def _run_single_episode(
    entry_side: float,
    r: np.ndarray,
    sigma_m_t: np.ndarray,
    k1: float,
    k2: float,
    h: float,
    m_reduced: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """State machine for one position episode.

    Parameters
    ----------
    entry_side : +1 (long) or -1 (short), fixed for the episode.
    r : cumulative position return at each step t,
        r(t) = entry_side * (P(t)/P(entry) - 1).
    sigma_m_t : monthly vol (sigma_m,i(t)) at each step.
    k1, k2, h, m_reduced : threshold parameters.

    Returns
    -------
    multipliers : len-n array, values in {1.0, m_reduced, 0.0}.
    states_int : len-n array, 0=NORMAL, 1=REDUCED, 2=STOPPED.
    z : len-n array, drawdown z-score.
    """
    n = len(r)
    multipliers = np.ones(n)
    states_int = np.zeros(n, dtype=np.int32)
    z = np.zeros(n)

    current_state: int = 0       # start NORMAL
    current_M: float = 0.0       # running favorable peak

    for t in range(n):
        # --- running peak M_i(t) = max(0, max_{entry<=s<=t} r(s)) ---
        if t == 0:
            current_M = max(0.0, r[t])
        else:
            current_M = max(current_M, r[t])

        # --- trailing drawdown D_i(t) = r_i(t) - M_i(t) <= 0 ---
        D = r[t] - current_M

        # --- z-score, guard against zero vol ---
        sig = max(sigma_m_t[t], 1e-12)
        z[t] = D / sig

        # --- state transitions (evaluated at close(t)) ---
        if current_state == 0:  # NORMAL
            if z[t] <= -k2:
                current_state = 2  # skip straight to STOPPED on deep breach
            elif z[t] <= -k1:
                current_state = 1  # -> REDUCED
            # else stay NORMAL

        elif current_state == 1:  # REDUCED
            if z[t] <= -k2:
                current_state = 2  # -> STOPPED
            elif z[t] >= -k1 + h:
                current_state = 0  # -> NORMAL (hysteresis recovery)
            # else stay REDUCED

        else:  # STOPPED (state==2)
            if z[t] >= -k2 + h:
                current_state = 1  # -> REDUCED (hysteresis recovery)
            # else stay STOPPED

        states_int[t] = current_state

        # --- multiplier ---
        if current_state == 0:
            multipliers[t] = 1.0
        elif current_state == 1:
            multipliers[t] = m_reduced
        else:
            multipliers[t] = 0.0

    return multipliers, states_int, z


def compute_episodes(
    held_weights: pd.DataFrame,
    close: pd.DataFrame,
    sigma: pd.DataFrame,
    k1: float = K1_DEFAULT,
    k2: float = K2_DEFAULT,
    h: float = H_DEFAULT,
    m_reduced: float = M_REDUCED_DEFAULT,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Compute stop-ladder multipliers, states, and z-scores for all tickers.

    An "episode" starts when a ticker's held weight becomes non-zero with a
    given sign, and resets when the held weight's sign flips or goes to zero
    (new episode = new entry anchor, state returns to NORMAL).  Shadow tracking
    (r, M, z keep updating from the original anchor while STOPPED) allows
    recovery re-entry.

    Parameters
    ----------
    held_weights : date x ticker DataFrame
        Post-band held weights.  Index = as-of close date, columns = tickers.
        NaN where the ticker has not yet entered the book or has zero weight.
    close : date x ticker DataFrame
        Adjusted close prices, same shape / index as held_weights.
    sigma : date x ticker DataFrame
        Annualized 63d realized vol (from earlier pipeline), same shape.
    k1, k2, h, m_reduced : float
        Pre-registered thresholds.

    Returns
    -------
    multipliers : date x ticker DataFrame
        Values in {NaN, 1.0, m_reduced, 0.0}.  NaN before first entry.
    states : date x ticker DataFrame
        String state per day: 'NORMAL', 'REDUCED', 'STOPPED', or None
        on non-episode / pre-entry days.
    z_scores : date x ticker DataFrame
        Drawdown z-score.  NaN on non-episode days.
    """
    # Align inputs
    common_idx = held_weights.index
    common_cols = list(held_weights.columns)
    close = close.reindex(index=common_idx, columns=common_cols)
    sigma = sigma.reindex(index=common_idx, columns=common_cols)

    sigma_m = _compute_monthly_vol(sigma)

    multipliers = pd.DataFrame(np.nan, index=common_idx, columns=common_cols)
    states_df = pd.DataFrame(None, index=common_idx, columns=common_cols, dtype=object)
    z_scores = pd.DataFrame(np.nan, index=common_idx, columns=common_cols)

    for ticker in common_cols:
        w = held_weights[ticker].to_numpy(dtype="float64")
        p = close[ticker].to_numpy(dtype="float64")
        sm = sigma_m[ticker].to_numpy(dtype="float64")
        n = len(w)

        mult_col = np.full(n, np.nan)
        state_col = np.full(n, None, dtype=object)
        z_col = np.full(n, np.nan)

        i = 0
        while i < n:
            # Skip NaN / zero-weight days
            if np.isnan(w[i]) or w[i] == 0.0:
                i += 1
                continue

            # --- new episode ---
            entry_idx = i
            entry_side = np.sign(w[i])
            entry_price = p[i]

            # Guard: entry price must be valid
            if np.isnan(entry_price) or entry_price <= 0:
                i += 1
                continue

            ep_indices: list[int] = []
            ep_r: list[float] = []
            ep_sm: list[float] = []

            while i < n:
                # Episode ends on data gap
                if np.isnan(p[i]) or np.isnan(sm[i]):
                    break
                # Episode ends on sign flip or zero weight
                if i > entry_idx:
                    cur_side = np.sign(w[i]) if (not np.isnan(w[i]) and w[i] != 0.0) else 0.0
                    if cur_side != entry_side:
                        break

                r_val = entry_side * (p[i] / entry_price - 1.0)
                ep_indices.append(i)
                ep_r.append(r_val)
                ep_sm.append(sm[i])
                i += 1

            if len(ep_indices) == 0:
                continue

            ep_r_arr = np.array(ep_r, dtype="float64")
            ep_sm_arr = np.array(ep_sm, dtype="float64")
            ep_mult, ep_st, ep_z = _run_single_episode(
                entry_side, ep_r_arr, ep_sm_arr,
                k1, k2, h, m_reduced,
            )

            for j, idx in enumerate(ep_indices):
                mult_col[idx] = ep_mult[j]
                state_col[idx] = STATE_NAMES[ep_st[j]]
                z_col[idx] = ep_z[j]

        multipliers[ticker] = mult_col
        states_df[ticker] = state_col
        z_scores[ticker] = z_col

    return multipliers, states_df, z_scores


def apply_stop_overlay(
    held_weights: pd.DataFrame,
    close: pd.DataFrame,
    sigma: pd.DataFrame,
    k1: float = K1_DEFAULT,
    k2: float = K2_DEFAULT,
    h: float = H_DEFAULT,
    m_reduced: float = M_REDUCED_DEFAULT,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Main entry point: apply the stop-ladder overlay to held weights.

    Returns (multipliers, states, z_scores).  The caller computes final
    weights as ``w_final = multipliers * held_weights`` (elementwise).

    Asserts documented invariants in-code rather than relying on eyeballing.
    """
    # --- input invariants ---
    assert not held_weights.isnull().all(axis=None), "held_weights is all-NaN"
    assert not close.isnull().all(axis=None), "close is all-NaN"
    assert not sigma.isnull().all(axis=None), "sigma is all-NaN"
    assert held_weights.index.is_monotonic_increasing, "held_weights index not monotonic"
    assert held_weights.index.is_unique, "held_weights index not unique"
    assert close.index.is_monotonic_increasing, "close index not monotonic"
    assert close.index.is_unique, "close index not unique"
    assert sigma.index.is_monotonic_increasing, "sigma index not monotonic"
    assert sigma.index.is_unique, "sigma index not unique"
    assert k2 > k1 > 0, f"require k2 > k1 > 0, got k1={k1}, k2={k2}"
    assert 0.0 < m_reduced < 1.0, f"require 0 < m_reduced < 1, got {m_reduced}"
    assert h > 0.0, f"require h > 0, got {h}"

    multipliers, states, z_scores = compute_episodes(
        held_weights, close, sigma, k1, k2, h, m_reduced,
    )

    # --- output invariants ---
    # shape
    assert multipliers.shape == held_weights.shape, "multipliers shape mismatch"
    assert states.shape == held_weights.shape, "states shape mismatch"
    assert z_scores.shape == held_weights.shape, "z_scores shape mismatch"

    # multipliers in valid set
    valid_m = {1.0, m_reduced, 0.0}
    non_nan_m = multipliers[multipliers.notna()]
    for col in non_nan_m.columns:
        col_vals = set(non_nan_m[col].dropna().unique())
        assert col_vals.issubset(valid_m), (
            f"Unexpected multiplier values in {col}: {col_vals - valid_m}"
        )

    # No infs in outputs
    assert not np.isinf(multipliers.to_numpy()).any(), "multipliers contains inf"
    assert not np.isinf(z_scores.to_numpy()).any(), "z_scores contains inf"

    return multipliers, states, z_scores
