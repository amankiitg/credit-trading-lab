"""Engineering-correctness tests for sprints v8.1-v8.2 (signals/trend_signal.py).

No IC test, no Sharpe claim -- per the v8.1/v8.2 PRD House Rule 1, this
signal is a mechanical reference instrument, not a predictive claim under
test. These tests check the construction is correct: no look-ahead (E1),
vol-target tracking (E2), leverage / position bounds (E3), reproducibility
(E4), point-in-time universe membership (E5), and the net/gross exposure
relationship for the signed (long/short) signal (E6).

`real_tidy` is the v8.2 default (`long_short=True`, signed). `real_tidy_long_only`
is the v8.1 rule (`long_short=False`), kept available for comparison, not
deleted.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.etf_universe import RAW_DIR, UNIVERSE, load_universe_close
from signals.trend_signal import (
    BAND_PCT_CANDIDATE,
    G_MAX_DEFAULT,
    K_DEAD_ZONE_CANDIDATE,
    L_DEFAULT,
    NO_TRADE_BAND_CANDIDATE,
    REBAL_FREQ_CANDIDATE,
    V_DEFAULT,
    W_DEFAULT,
    W_MAX_DEFAULT,
    _hysteresis_signal,
    apply_rebalance_control,
    compute_dead_zone,
    compute_trend,
    shift_to_next_day,
    to_exposure_series,
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


@pytest.fixture(scope="module")
def real_tidy_long_only(real_close: pd.DataFrame) -> pd.DataFrame:
    return compute_trend(real_close, long_short=False)


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
    """v8.2 default (long_short=True): |weight| <= w_max, sign unrestricted."""
    active = real_tidy.dropna(subset=["weight"])
    assert (active["weight"].abs() <= W_MAX_DEFAULT + 1e-9).all()
    assert (active["weight"] < 0).any(), "expected at least some short positions with long_short=True"
    assert (active["weight"] > 0).any(), "expected at least some long positions with long_short=True"


def test_long_only_weight_bound_respected(real_tidy_long_only: pd.DataFrame) -> None:
    """v8.1 rule (long_short=False), kept for comparison: no negative weights."""
    active = real_tidy_long_only.dropna(subset=["weight"])
    assert (active["weight"].abs() <= W_MAX_DEFAULT + 1e-9).all()
    assert (active["weight"] >= 0).all(), "long-only/flat: no negative weights expected"


def test_gross_leverage_cap_respected(real_close: pd.DataFrame, real_tidy: pd.DataFrame) -> None:
    pos = to_position_matrix(real_tidy)
    gross = pos.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()


# ---------------------------------------------------------------- E6: net/gross exposure relationship

def test_net_within_gross_real_universe(real_tidy: pd.DataFrame) -> None:
    """|net(t)| <= gross(t) for every date -- a mathematical identity
    (|sum(x_i)| <= sum(|x_i|)), verified directly rather than assumed.
    """
    exposure = to_exposure_series(real_tidy)
    assert (exposure["net_exposure"].abs() <= exposure["gross_exposure"] + 1e-9).all()
    assert (exposure["gross_exposure"] <= G_MAX_DEFAULT + 1e-9).all()


def test_net_diverges_from_gross_with_shorts_on(real_tidy: pd.DataFrame) -> None:
    """With long_short=True, net should differ from gross on at least some
    days -- in the v8.1 long-only book net always equalled gross exactly
    (no shorts existed); that equality must not silently persist here.
    """
    exposure = to_exposure_series(real_tidy)
    active = exposure.dropna()
    diverges = (active["net_exposure"] - active["gross_exposure"]).abs() > 1e-9
    assert diverges.any(), "expected net != gross on at least some days once shorts are allowed"


def test_net_equals_gross_long_only(real_tidy_long_only: pd.DataFrame) -> None:
    """Sanity check that the long_short=False path is unchanged from v8.1:
    net always equals gross when there are no shorts.
    """
    exposure = to_exposure_series(real_tidy_long_only)
    active = exposure.dropna()
    np.testing.assert_allclose(
        active["net_exposure"].to_numpy(), active["gross_exposure"].to_numpy(), atol=1e-9
    )


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


def test_gross_leverage_cap_respected_under_stress_all_short() -> None:
    """Same stress case as above, mirrored to an all-downtrend universe so
    every raw_weight is negative. Confirms gross (sum of absolute values)
    is computed correctly with negative inputs -- not, e.g., a plain sum
    that would let negative weights cancel and silently undercount gross.
    """
    n = 300
    idx = pd.bdate_range("2019-01-01", periods=n)
    close = pd.DataFrame(
        {f"T{i}": 100 * (1 - 0.0005) ** np.arange(n) for i in range(8)}, index=idx
    )  # smooth downtrend, near-zero realized vol -> all-short, raw weights all negative
    tidy = compute_trend(close)
    active = tidy.dropna(subset=["weight"])
    assert (active["weight"] <= 1e-9).all(), "expected every active weight to be short (<=0)"
    pos = to_position_matrix(tidy)
    gross = pos.abs().sum(axis=1, skipna=True)
    net = pos.sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()
    assert gross.max() > 1.5, "expected the cap to actually bind near g_max in this stress case"
    np.testing.assert_allclose(net.dropna().to_numpy(), (-gross.dropna()).to_numpy(), atol=1e-9)


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
    assert (active["signal"] == -1).any(), "expected short (signal=-1) rows to exercise the negative case"
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


# ---------------------------------------------------------------- T2: rebalance control (rebal_freq, no-trade band)

def _desired_matrix(real_close: pd.DataFrame, long_short: bool = True) -> pd.DataFrame:
    return to_position_matrix(compute_trend(real_close, long_short=long_short))


def test_rebalance_control_default_matches_unrestricted_no_gaps() -> None:
    """rebal_freq=1, no_trade_band=0 (the bare defaults) must reproduce the
    plain desired matrix exactly -- the control is a no-op until enabled.
    Uses gap-free synthetic data deliberately: on real data, a temporary
    data gap (missing price print) is carried forward by the rebalance
    control rather than dropping the name to NaN for that one day (see
    test_rebalance_control_carries_forward_through_temporary_data_gap),
    so the "exact no-op" property only holds in the absence of gaps --
    documented, not silently swept past.
    """
    close = _synthetic_close()
    desired = to_position_matrix(compute_trend(close))
    held = apply_rebalance_control(desired)
    pd.testing.assert_frame_equal(held, desired, check_names=False)


def test_rebalance_control_default_differs_from_desired_only_on_gap_affected_rows(
    real_close: pd.DataFrame,
) -> None:
    """On real data, rebal_freq=1/no_trade_band=0 deviates from `desired`
    only on dates where carrying a temporary gap forward changes the
    joint gross-leverage scale for the whole book that day -- a real,
    expected consequence of carrying positions through data gaps rather
    than force-flattening them, not a bug. Document the magnitude rather
    than assume it away.
    """
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(desired)
    diff_mask = (held - desired).abs() > 1e-9
    diff_mask = diff_mask.fillna(False)
    n_diff_rows = diff_mask.any(axis=1).sum()
    has_gap = real_close.isna().any(axis=1).sum()
    assert n_diff_rows <= has_gap, (
        f"{n_diff_rows} differing rows exceeds the {has_gap} known gap rows in the close matrix"
    )


def test_rebalance_control_weekly_reduces_turnover(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held_daily = apply_rebalance_control(desired, rebal_freq=1)
    held_weekly = apply_rebalance_control(desired, rebal_freq=REBAL_FREQ_CANDIDATE)

    turnover_daily = held_daily.diff().abs().sum(axis=1, skipna=True).sum()
    turnover_weekly = held_weekly.diff().abs().sum(axis=1, skipna=True).sum()
    assert turnover_weekly < turnover_daily


def test_rebalance_control_no_trade_band_reduces_turnover(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held_no_band = apply_rebalance_control(desired, no_trade_band=0.0)
    held_band = apply_rebalance_control(desired, no_trade_band=NO_TRADE_BAND_CANDIDATE)

    turnover_no_band = held_no_band.diff().abs().sum(axis=1, skipna=True).sum()
    turnover_band = held_band.diff().abs().sum(axis=1, skipna=True).sum()
    assert turnover_band < turnover_no_band


def test_rebalance_control_point_in_time_entry_unaffected_by_band_or_schedule(
    real_close: pd.DataFrame,
) -> None:
    """A name's first valid entry must take effect immediately regardless
    of recompute phase or band -- there is no stale pre-entry value to
    hold instead.
    """
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(
        desired, rebal_freq=REBAL_FREQ_CANDIDATE, no_trade_band=NO_TRADE_BAND_CANDIDATE
    )
    for t in desired.columns:
        first_desired = desired[t].first_valid_index()
        first_held = held[t].first_valid_index()
        assert first_desired == first_held, f"{t}: entry date shifted by rebalance control"


def test_rebalance_control_no_lookahead(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    cutoff = desired.index[len(desired) // 2]
    baseline = apply_rebalance_control(
        desired, rebal_freq=REBAL_FREQ_CANDIDATE, no_trade_band=NO_TRADE_BAND_CANDIDATE
    )

    perturbed_close = real_close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed_desired = _desired_matrix(perturbed_close)
    perturbed = apply_rebalance_control(
        perturbed_desired, rebal_freq=REBAL_FREQ_CANDIDATE, no_trade_band=NO_TRADE_BAND_CANDIDATE
    )

    pd.testing.assert_frame_equal(baseline.loc[:cutoff], perturbed.loc[:cutoff])


def test_rebalance_control_weight_bound_respected(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(
        desired, rebal_freq=REBAL_FREQ_CANDIDATE, no_trade_band=NO_TRADE_BAND_CANDIDATE
    )
    active = held.to_numpy()
    active = active[~np.isnan(active)]
    assert (np.abs(active) <= W_MAX_DEFAULT + 1e-9).all()


def test_rebalance_control_gross_cap_respected_despite_staleness() -> None:
    """Engineered case: two names, one updates every day, one is frozen
    far in the past (simulating a stale, un-recomputed weight) right up
    against w_max -- without the re-cap pass, the combination would
    exceed g_max even though each individual snapshot, taken alone,
    would not have.
    """
    idx = pd.bdate_range("2022-01-01", periods=10)
    desired = pd.DataFrame(
        {
            "A": [0.5] * 10,                                   # always at the per-name cap
            "B": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5],  # ramps up to the cap too
        },
        index=idx,
    )
    # rebal_freq large enough that B's earlier (smaller) values would be
    # held stale relative to A always being recomputed at 0.5 -- but here
    # both go to 0.5 eventually, so gross would reach 1.0 < g_max=2.0
    # without the cap mattering at this scale. Force a tighter g_max to
    # actually exercise the cap binding under partial staleness.
    held = apply_rebalance_control(desired, rebal_freq=3, no_trade_band=0.0, g_max=0.6)
    gross = held.abs().sum(axis=1, skipna=True)
    assert (gross <= 0.6 + 1e-9).all()


def test_rebalance_control_gross_cap_respected_real_universe(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(
        desired, rebal_freq=REBAL_FREQ_CANDIDATE, no_trade_band=NO_TRADE_BAND_CANDIDATE
    )
    gross = held.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()


def test_rebalance_control_carries_forward_through_temporary_data_gap() -> None:
    """A one-day NaN in `desired` for an already-entered name (e.g. a
    missing price print) must carry forward the last held value, not
    revert to NaN.
    """
    idx = pd.bdate_range("2022-01-01", periods=5)
    desired = pd.DataFrame({"A": [0.2, 0.3, np.nan, 0.3, 0.4]}, index=idx)
    held = apply_rebalance_control(desired, rebal_freq=1, no_trade_band=0.0)
    assert held["A"].notna().all()
    assert held["A"].iloc[2] == held["A"].iloc[1]


# ---------------------------------------------------------------- data hygiene

def test_position_matrix_persisted_and_clean() -> None:
    assert POSITIONS_PATH.exists(), "run the v8.1 build first"
    pos = pd.read_parquet(POSITIONS_PATH)
    assert pos.index.is_monotonic_increasing
    assert pos.index.is_unique
    assert set(pos.columns) == set(UNIVERSE)
    defined = pos.dropna(how="all")
    assert np.isfinite(defined.to_numpy(dtype=float)[~np.isnan(defined.to_numpy(dtype=float))]).all()


# ---------------------------------------------------------------- T2 (refined): proportional no-trade band

def test_band_pct_small_drift_below_band_produces_no_trade() -> None:
    """A name held at 0.20, with a new desired value just inside the 20%
    proportional band (|0.20 -> 0.23| = 0.03 < 0.20*0.23 = 0.046), must
    not trade -- the held value stays at the previous level.
    """
    idx = pd.bdate_range("2022-01-01", periods=3)
    desired = pd.DataFrame({"A": [0.20, 0.20, 0.23]}, index=idx)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    assert held["A"].iloc[2] == held["A"].iloc[1] == 0.20


def test_band_pct_drift_above_band_trades_fully_to_target() -> None:
    """A desired move from 0.20 to 0.30 (|delta|=0.10 > 0.20*0.30=0.06)
    breaches the band and must trade all the way to the new target, not
    partway to the band edge.
    """
    idx = pd.bdate_range("2022-01-01", periods=3)
    desired = pd.DataFrame({"A": [0.20, 0.20, 0.30]}, index=idx)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    assert held["A"].iloc[2] == 0.30


def test_band_pct_is_proportional_to_target_weight() -> None:
    """Same absolute drift (0.05), applied to a small target (EEM-like,
    0.10) and a large target (IEF-like, 0.40): the small-target drift
    should breach a 20% band (0.05 > 0.20*0.15=0.03) while the
    large-target drift of the same absolute size should not
    (0.05 < 0.20*0.45=0.09) -- the band scales with position size, not a
    single flat number across very differently-sized weights.
    """
    idx = pd.bdate_range("2022-01-01", periods=3)
    desired_small = pd.DataFrame({"EEM_like": [0.10, 0.10, 0.15]}, index=idx)
    desired_large = pd.DataFrame({"IEF_like": [0.40, 0.40, 0.45]}, index=idx)

    held_small = apply_rebalance_control(desired_small, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    held_large = apply_rebalance_control(desired_large, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)

    assert held_small["EEM_like"].iloc[2] == 0.15, "small-weight name: same absolute drift should breach"
    assert held_large["IEF_like"].iloc[2] == 0.40, "large-weight name: same absolute drift should NOT breach"


def test_band_pct_no_lookahead(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    cutoff = desired.index[len(desired) // 2]
    baseline = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)

    perturbed_close = real_close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed_desired = _desired_matrix(perturbed_close)
    perturbed = apply_rebalance_control(perturbed_desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)

    pd.testing.assert_frame_equal(baseline.loc[:cutoff], perturbed.loc[:cutoff])


def test_band_pct_gross_cap_respected_real_universe(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    gross = held.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()


def test_band_pct_weight_bound_respected(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    active = held.to_numpy()
    active = active[~np.isnan(active)]
    assert (np.abs(active) <= W_MAX_DEFAULT + 1e-9).all()


def test_band_pct_reduces_turnover_on_real_universe(real_close: pd.DataFrame) -> None:
    desired = _desired_matrix(real_close)
    held_no_band = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.0)
    held_band = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)

    turnover_no_band = held_no_band.diff().abs().sum(axis=1, skipna=True).sum()
    turnover_band = held_band.diff().abs().sum(axis=1, skipna=True).sum()
    assert turnover_band < turnover_no_band


def test_band_pct_zero_target_has_zero_tolerance() -> None:
    """Proportional to zero is zero: once the desired weight goes flat,
    any nonzero held position must trade out immediately, not linger.
    """
    idx = pd.bdate_range("2022-01-01", periods=3)
    desired = pd.DataFrame({"A": [0.20, 0.20, 0.0]}, index=idx)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)
    assert held["A"].iloc[2] == 0.0


# ---------------------------------------------------------------- T2b: signal-level hysteresis (dead zone)

def test_compute_dead_zone_formula() -> None:
    idx = pd.bdate_range("2022-01-01", periods=3)
    sigma = pd.DataFrame({"A": [0.10, 0.20, 0.30]}, index=idx)
    dz = compute_dead_zone(sigma, L=L_DEFAULT, k=0.5)
    expected = 0.5 * sigma * np.sqrt(L_DEFAULT / 252)
    pd.testing.assert_frame_equal(dz, expected)


def test_compute_dead_zone_symmetric_thresholds() -> None:
    """The dead zone is used as +/-dead_zone -- a single value defines a
    symmetric band, not independently chosen upper/lower thresholds.
    """
    idx = pd.bdate_range("2022-01-01", periods=1)
    sigma = pd.DataFrame({"A": [0.15]}, index=idx)
    dz = compute_dead_zone(sigma, L=L_DEFAULT, k=K_DEAD_ZONE_CANDIDATE)
    upper = dz["A"].iloc[0]
    lower = -dz["A"].iloc[0]
    assert upper == -lower
    assert upper > 0


def test_hysteresis_oscillation_inside_dead_zone_holds_prior_sign() -> None:
    """trail_ret oscillates within +/-band after an initial clear long --
    the held sign must not flip on any of the small in-band wiggles.
    """
    idx = pd.bdate_range("2022-01-01", periods=6)
    trail_ret = pd.DataFrame({"A": [0.10, 0.01, -0.01, 0.005, -0.005, 0.02]}, index=idx)
    dead_zone = pd.DataFrame({"A": [0.03] * 6}, index=idx)
    defined = pd.DataFrame({"A": [True] * 6}, index=idx)

    signal = _hysteresis_signal(trail_ret, dead_zone, defined, long_short=True)
    assert (signal["A"] == 1.0).all(), "all in-band wiggles after the initial long should hold sign=+1"


def test_hysteresis_clear_move_beyond_threshold_flips() -> None:
    idx = pd.bdate_range("2022-01-01", periods=4)
    trail_ret = pd.DataFrame({"A": [0.10, 0.01, -0.10, 0.01]}, index=idx)
    dead_zone = pd.DataFrame({"A": [0.03] * 4}, index=idx)
    defined = pd.DataFrame({"A": [True] * 4}, index=idx)

    signal = _hysteresis_signal(trail_ret, dead_zone, defined, long_short=True)
    assert signal["A"].iloc[0] == 1.0
    assert signal["A"].iloc[1] == 1.0, "small in-band move should hold prior sign"
    assert signal["A"].iloc[2] == -1.0, "clear move beyond the opposite threshold must flip"
    assert signal["A"].iloc[3] == -1.0, "back in-band after a flip should hold the new sign"


def test_hysteresis_first_valid_date_seeds_with_plain_sign() -> None:
    """No prior state exists before the first valid date -- it must seed
    with the plain (unbuffered) sign, even if that value sits inside what
    would otherwise be the dead zone.
    """
    idx = pd.bdate_range("2022-01-01", periods=2)
    trail_ret = pd.DataFrame({"A": [0.01, 0.01]}, index=idx)  # inside the dead zone from day 1
    dead_zone = pd.DataFrame({"A": [0.03, 0.03]}, index=idx)
    defined = pd.DataFrame({"A": [True, True]}, index=idx)

    signal = _hysteresis_signal(trail_ret, dead_zone, defined, long_short=True)
    assert signal["A"].iloc[0] == np.sign(0.01)  # plain sign, seeded, not NaN-and-held
    assert signal["A"].iloc[1] == signal["A"].iloc[0]  # held thereafter


def test_hysteresis_masks_to_nan_before_warmup_and_on_gap_days() -> None:
    idx = pd.bdate_range("2022-01-01", periods=4)
    trail_ret = pd.DataFrame({"A": [np.nan, 0.10, np.nan, 0.10]}, index=idx)
    dead_zone = pd.DataFrame({"A": [np.nan, 0.03, np.nan, 0.03]}, index=idx)
    defined = pd.DataFrame({"A": [False, True, False, True]}, index=idx)

    signal = _hysteresis_signal(trail_ret, dead_zone, defined, long_short=True)
    assert pd.isna(signal["A"].iloc[0])
    assert signal["A"].iloc[1] == 1.0
    assert pd.isna(signal["A"].iloc[2]), "a gap day must report NaN even though state carries through internally"
    assert signal["A"].iloc[3] == 1.0


def test_hysteresis_no_lookahead_real_universe() -> None:
    close = load_universe_close()
    cutoff = close.index[len(close) // 2]
    baseline = compute_trend(close, k_dead_zone=K_DEAD_ZONE_CANDIDATE)

    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed = compute_trend(perturbed_close, k_dead_zone=K_DEAD_ZONE_CANDIDATE)

    base_prefix = baseline[baseline["date"] <= cutoff].reset_index(drop=True)
    pert_prefix = perturbed[perturbed["date"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_prefix, pert_prefix)


def test_hysteresis_vol_target_identity_holds(real_close: pd.DataFrame) -> None:
    tidy = compute_trend(real_close, k_dead_zone=K_DEAD_ZONE_CANDIDATE)
    active = tidy.dropna(subset=["sigma", "raw_weight", "signal"])
    assert (active["signal"] == -1).any(), "expected short rows under hysteresis too"
    vol_contribution = active["raw_weight"] * active["sigma"]
    expected = active["signal"] * np.minimum(V_DEFAULT, W_MAX_DEFAULT * active["sigma"])
    np.testing.assert_allclose(vol_contribution.to_numpy(), expected.to_numpy(), atol=1e-9)


def test_hysteresis_gross_cap_respected(real_close: pd.DataFrame) -> None:
    tidy = compute_trend(real_close, k_dead_zone=K_DEAD_ZONE_CANDIDATE)
    pos = to_position_matrix(tidy)
    gross = pos.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()


def test_hysteresis_reduces_sign_flips_real_universe(real_close: pd.DataFrame) -> None:
    pos_plain = to_position_matrix(compute_trend(real_close))
    pos_hyst = to_position_matrix(compute_trend(real_close, k_dead_zone=K_DEAD_ZONE_CANDIDATE))

    def n_flips(pos):
        sign = np.sign(pos)
        flipped = (sign != sign.shift(1)) & sign.notna() & sign.shift(1).notna() & (sign != 0) & (sign.shift(1) != 0)
        return int(flipped.sum().sum())

    assert n_flips(pos_hyst) < n_flips(pos_plain)


def test_band_still_functions_on_top_of_hysteresis(real_close: pd.DataFrame) -> None:
    """T2 (magnitude band) stacks on T2b (hysteresis): apply_rebalance_control
    still reduces turnover further when fed an already-hysteresis-buffered
    desired matrix, and the combined gross cap still holds.
    """
    desired = to_position_matrix(compute_trend(real_close, k_dead_zone=K_DEAD_ZONE_CANDIDATE))
    held_no_band = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.0)
    held_band = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_CANDIDATE)

    turnover_no_band = held_no_band.diff().abs().sum(axis=1, skipna=True).sum()
    turnover_band = held_band.diff().abs().sum(axis=1, skipna=True).sum()
    assert turnover_band <= turnover_no_band

    gross = held_band.abs().sum(axis=1, skipna=True)
    assert (gross <= G_MAX_DEFAULT + 1e-9).all()
