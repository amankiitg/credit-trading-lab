"""Engineering-correctness tests for sprint v8.1 (signals/trend_signal.py).

No IC test, no Sharpe claim -- per the v8.1 PRD House Rule 1, this signal
is a mechanical reference instrument, not a predictive claim under test.
These tests check the construction is correct: no look-ahead (E1),
vol-target tracking (E2), leverage / position bounds (E3), reproducibility
(E4), and point-in-time universe membership (E5).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.etf_universe import RAW_DIR, UNIVERSE, load_universe_close
from signals.trend_signal import (
    G_MAX_DEFAULT,
    L_DEFAULT,
    V_DEFAULT,
    W_DEFAULT,
    W_MAX_DEFAULT,
    compute_trend,
    shift_to_next_day,
    to_position_matrix,
)

POSITIONS_PATH = Path("data/processed/v8_1_target_positions.parquet")


def _synthetic_close(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    cols = {}
    for i, name in enumerate(["A", "B", "C"]):
        steps = rng.normal(0.0003 * (i - 1), 0.01, size=n)
        cols[name] = 100 * np.exp(np.cumsum(steps))
    return pd.DataFrame(cols, index=idx)


@pytest.fixture(scope="module")
def real_close() -> pd.DataFrame:
    assert (RAW_DIR / "SPY.parquet").exists(), "run signals.etf_universe.ingest() first"
    return load_universe_close()


@pytest.fixture(scope="module")
def real_tidy(real_close: pd.DataFrame) -> pd.DataFrame:
    return compute_trend(real_close)


# ---------------------------------------------------------------- E1: no look-ahead

def test_no_lookahead_perturb_future_leaves_past_unchanged() -> None:
    close = _synthetic_close()
    cutoff = close.index[250]

    baseline = compute_trend(close)
    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] += 1000.0
    perturbed = compute_trend(perturbed_close)

    base_prefix = baseline[baseline["date"] <= cutoff].reset_index(drop=True)
    pert_prefix = perturbed[perturbed["date"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_prefix, pert_prefix)


def test_no_lookahead_on_real_universe(real_close: pd.DataFrame) -> None:
    cutoff = real_close.index[len(real_close) // 2]
    baseline = compute_trend(real_close)

    perturbed_close = real_close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed = compute_trend(perturbed_close)

    base_prefix = baseline[baseline["date"] <= cutoff].reset_index(drop=True)
    pert_prefix = perturbed[perturbed["date"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_prefix, pert_prefix)


def test_shift_to_next_day_aligns_one_row_forward() -> None:
    close = _synthetic_close()
    tidy = compute_trend(close)
    pos = to_position_matrix(tidy)
    target = shift_to_next_day(pos)

    assert target.index[0] == pos.index[0]
    assert target.iloc[0].isna().all()
    pd.testing.assert_series_equal(
        target.iloc[5], pos.iloc[4], check_names=False
    )


# ---------------------------------------------------------------- E4: reproducibility

def test_reproducibility_identical_inputs_identical_output() -> None:
    close = _synthetic_close()
    out1 = compute_trend(close)
    out2 = compute_trend(close.copy())
    pd.testing.assert_frame_equal(out1, out2)


def test_reproducibility_on_real_universe(real_tidy: pd.DataFrame, real_close: pd.DataFrame) -> None:
    rerun = compute_trend(real_close.copy())
    pd.testing.assert_frame_equal(real_tidy, rerun)


# ---------------------------------------------------------------- E5: point-in-time membership

def test_point_in_time_membership_synthetic() -> None:
    close = _synthetic_close()
    tidy = compute_trend(close)
    min_gap = max(L_DEFAULT, W_DEFAULT)
    for t in close.columns:
        sub = tidy[tidy["ticker"] == t]
        first_valid_idx = sub.index[sub["signal"].notna()][0]
        assert first_valid_idx >= min_gap, f"{t}: first valid at {first_valid_idx} < {min_gap}"
        assert sub.loc[: first_valid_idx - 1, "signal"].isna().all()


def test_point_in_time_membership_real_universe(real_close: pd.DataFrame, real_tidy: pd.DataFrame) -> None:
    """E5, corrected during implementation: the PRD stated the minimum gap
    as L + W trading days. The mathematically exact minimum is max(L, W),
    since trail_ret's L-day warmup and sigma's W-day warmup overlap rather
    than stack -- L=120 already exceeds the W=63 requirement.
    """
    min_gap = max(L_DEFAULT, W_DEFAULT)
    violations = []
    for t in UNIVERSE:
        sub = real_tidy[real_tidy["ticker"] == t].reset_index(drop=True)
        valid = sub.index[sub["signal"].notna()]
        if len(valid) == 0:
            continue
        first_valid_idx = valid[0]
        first_close_idx = sub.index[sub["adj_close"].notna()][0]
        gap = first_valid_idx - first_close_idx
        if gap < min_gap:
            violations.append((t, gap))
    assert violations == [], f"point-in-time violations (ticker, gap): {violations}"


# ---------------------------------------------------------------- E3 / bounds: leverage + per-name cap

def test_per_name_weight_bound_respected(real_tidy: pd.DataFrame) -> None:
    active = real_tidy.dropna(subset=["weight"])
    assert (active["weight"].abs() <= W_MAX_DEFAULT + 1e-9).all()
    assert (active["weight"] >= 0).all(), "long-only/flat: no negative weights expected"


def test_gross_leverage_cap_respected(real_close: pd.DataFrame, real_tidy: pd.DataFrame) -> None:
    pos = to_position_matrix(real_tidy)
    gross = pos.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()


def test_gross_leverage_cap_respected_under_stress() -> None:
    """Force every name to be active with a tiny vol estimate, so the
    raw (pre-cap) gross would vastly exceed g_max -- confirms the cap
    actually binds rather than only happening to not be hit on real data.
    """
    n = 300
    idx = pd.bdate_range("2019-01-01", periods=n)
    close = pd.DataFrame(
        {f"T{i}": 100 * (1 + 0.0005) ** np.arange(n) for i in range(8)}, index=idx
    )  # smooth uptrend, near-zero realized vol -> v/sigma would be huge pre-cap
    tidy = compute_trend(close)
    pos = to_position_matrix(tidy)
    gross = pos.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()
    assert gross.max() > 1.5, "expected the cap to actually bind near g_max in this stress case"


# ---------------------------------------------------------------- E2: vol-target tracking

def test_vol_target_formula_identity_real_universe(real_tidy: pd.DataFrame) -> None:
    """E2, corrected during implementation: checking sigma_i(t) (the
    *input* realized asset vol) against the v=0.10 band tests the wrong
    thing -- it is an empirical claim about each asset's natural vol, not
    a check for a scaling/unit bug in the sizing formula. EEM's realized
    vol genuinely averages ~24%, well outside any tight band, and that is
    real EM-equity behavior, not a bug (see notes.md).

    The actual no-scaling-bug invariant is on the *output*:
    raw_weight_i(t) * sigma_i(t) == min(v, w_max * sigma_i(t)) wherever
    both signal and sigma are defined. This is true by construction for a
    correct implementation and would be violated by, e.g., a missing
    sqrt(252) annualization, a variance/std mixup, or an inverted ratio.
    """
    active = real_tidy.dropna(subset=["sigma", "raw_weight", "signal"])
    vol_contribution = active["raw_weight"] * active["sigma"]
    expected = active["signal"] * np.minimum(V_DEFAULT, W_MAX_DEFAULT * active["sigma"])
    np.testing.assert_allclose(vol_contribution.to_numpy(), expected.to_numpy(), atol=1e-9)


def test_vol_target_per_ticker_report(real_tidy: pd.DataFrame) -> None:
    """Informational only (not a gate): report each ticker's average
    realized vol so a v=0.10 mismatch (e.g. EEM) is visible, not silent.
    """
    active = real_tidy.dropna(subset=["sigma"])
    avg_sigma_by_ticker = active.groupby("ticker")["sigma"].mean()
    print("\naverage realized annualized vol by ticker (target v=%.2f):" % V_DEFAULT)
    print(avg_sigma_by_ticker.to_string())
    assert (avg_sigma_by_ticker > 0).all()


# ---------------------------------------------------------------- data hygiene

def test_position_matrix_persisted_and_clean() -> None:
    assert POSITIONS_PATH.exists(), "run the v8.1 build first"
    pos = pd.read_parquet(POSITIONS_PATH)
    assert pos.index.is_monotonic_increasing
    assert pos.index.is_unique
    assert set(pos.columns) == set(UNIVERSE)
    defined = pos.dropna(how="all")
    assert np.isfinite(defined.to_numpy(dtype=float)[~np.isnan(defined.to_numpy(dtype=float))]).all()
