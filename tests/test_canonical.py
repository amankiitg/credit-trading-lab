"""Sprint v5.5 — Foundation Repair falsification tests (E1–E6).

E3 (this file, so far): the Kalman residual is the one-step-ahead
innovation (past-data-only), not the posterior leftover.

  - test_e3_kalman_is_innovation: independently reconstruct the
    innovation and assert equality with kalman_hedge's residual.
  - test_e3_kalman_std_exceeds_posterior: the corrected full-sample
    rv_hy_ig Kalman residual std must strictly exceed the old whitened
    posterior std (0.00463 from data/processed/_pre_v5_5).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.rv_signals import (
    build_all_residuals,
    canonical_residuals,
    dv01_hedge,
    kalman_hedge,
    ols_hedge,
    select_tradeable_method,
)

PROCESSED_PATH = Path("data/processed/features.parquet")
RAW_CREDIT_PATH = Path("data/raw/credit_market_data.parquet")
OLD_POSTERIOR_STD = 0.00463  # pre-v5.5 rv_hy_ig stored (whitened) residual std


@pytest.fixture(scope="module")
def credit_data() -> pd.DataFrame:
    return pd.read_parquet(RAW_CREDIT_PATH)


def _ar1(n: int, phi: float, sigma: float, seed: int) -> np.ndarray:
    """AR(1): x_t = phi·x_{t-1} + e_t. OU half-life ≈ -ln2 / ln(phi)."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0.0, sigma, n)
    x = np.empty(n)
    x[0] = e[0]
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def _phi_for_halflife(hl: float) -> float:
    return float(np.exp(-np.log(2.0) / hl))


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_PATH)


def _innovation_reference(y: pd.Series, x: pd.Series, Q: float = 1e-5,
                          init_window: int = 63) -> np.ndarray:
    """Independent reimplementation that returns the one-step-ahead
    innovation e_t = y_t - [1, x_t] @ s_{t|t-1} (prior state)."""
    x = x.reindex(y.index)
    yv = y.to_numpy(float)
    xv = x.to_numpy(float)
    valid = np.isfinite(yv) & np.isfinite(xv)
    cum = np.cumsum(valid)
    ie = int(np.searchsorted(cum, init_window))
    im = valid.copy()
    im[ie + 1:] = False
    A = np.column_stack([np.ones(im.sum()), xv[im]])
    coef, *_ = np.linalg.lstsq(A, yv[im], rcond=None)
    s = coef.astype(float)
    R = float(np.var(yv[im] - A @ coef, ddof=1)) or 1e-12
    P = np.eye(2)
    Qm = Q * np.eye(2)
    n = len(yv)
    out = np.full(n, np.nan)
    for t in range(ie + 1, n):
        if not valid[t]:
            continue
        Pp = P + Qm
        H = np.array([1.0, xv[t]])
        S = float(H @ Pp @ H + R)
        K = (Pp @ H) / S
        innov = yv[t] - H @ s  # prior-state prediction error
        out[t] = innov
        s = s + K * innov
        P = (np.eye(2) - np.outer(K, H)) @ Pp
    return out


def test_e3_kalman_is_innovation(features: pd.DataFrame) -> None:
    """The residual returned by kalman_hedge equals the one-step-ahead
    innovation at every date (not the posterior residual)."""
    y, x = features["hy_spread"], features["ig_spread"]
    resid, _ = kalman_hedge(y, x)
    ref = _innovation_reference(y, x)
    np.testing.assert_allclose(
        resid.to_numpy(), ref, rtol=0, atol=1e-12, equal_nan=True
    )


def test_e3_kalman_std_exceeds_posterior(features: pd.DataFrame) -> None:
    """Corrected Kalman residual std strictly exceeds the old whitened
    posterior std — proves we are no longer storing the shrunk leftover."""
    y, x = features["hy_spread"], features["ig_spread"]
    resid, _ = kalman_hedge(y, x)
    std = float(resid.dropna().std())
    assert std > OLD_POSTERIOR_STD, (
        f"Kalman residual std={std:.6f} is not > old posterior "
        f"{OLD_POSTERIOR_STD} — innovation fix may not have taken"
    )


# ----------------------------------------------------------------- E2
# select_tradeable_method: stationary AND half-life ∈ [5, 63]; tiebreak
# on hedge-ratio stability; None if nothing qualifies.


def test_e2_selector_picks_only_in_band() -> None:
    """Given a whitened (HL≈1), a tradeable (HL≈12), and a slow (HL≈300)
    residual, only the in-band one is selected; the whitened one is
    stationary yet rejected by the half-life floor."""
    n = 1600
    hr = pd.Series(np.full(n, 0.5))  # identical stable hedge for all 3
    results = {
        "synthetic": {
            "whitened": (pd.Series(_ar1(n, _phi_for_halflife(1.0), 1.0, 1)), hr),
            "tradeable": (pd.Series(_ar1(n, _phi_for_halflife(12.0), 1.0, 2)), hr),
            "slow": (pd.Series(_ar1(n, _phi_for_halflife(300.0), 1.0, 3)), hr),
        }
    }
    sel = select_tradeable_method(results, warmup=252)
    chosen, diag = sel["synthetic"]
    assert chosen == "tradeable", f"expected 'tradeable', got {chosen}: {diag}"
    # the whitened series is stationary but disqualified by the HL floor
    assert diag["whitened"]["adf_p"] < 0.05
    assert diag["whitened"]["half_life"] < 5.0
    assert not diag["whitened"]["qualified"]
    # the slow series is disqualified (HL too long and/or non-stationary)
    assert not diag["slow"]["qualified"]
    # the chosen series respects the band
    assert 5.0 <= diag["tradeable"]["half_life"] <= 63.0


def test_e2_selector_tiebreak_prefers_stable_hedge() -> None:
    """When two methods both qualify on half-life, the one with the lower
    hedge-ratio CV (more stable hedge) wins."""
    n = 1600
    resid_a = pd.Series(_ar1(n, _phi_for_halflife(12.0), 1.0, 4))
    resid_b = pd.Series(_ar1(n, _phi_for_halflife(15.0), 1.0, 5))
    rng = np.random.default_rng(6)
    hr_stable = pd.Series(0.5 + 0.001 * rng.normal(size=n))   # low CV
    hr_jumpy = pd.Series(0.5 + 0.40 * rng.normal(size=n))     # high CV
    results = {
        "synthetic": {
            "stable_hedge": (resid_a, hr_stable),
            "jumpy_hedge": (resid_b, hr_jumpy),
        }
    }
    sel = select_tradeable_method(results, warmup=252)
    chosen, diag = sel["synthetic"]
    assert diag["stable_hedge"]["qualified"] and diag["jumpy_hedge"]["qualified"], diag
    assert diag["stable_hedge"]["hedge_cv"] < diag["jumpy_hedge"]["hedge_cv"]
    assert chosen == "stable_hedge", f"tiebreak should pick stable hedge, got {chosen}"


def test_e2_selector_returns_none_when_nothing_qualifies() -> None:
    """A pair whose only residuals are whitened (HL≈1) yields chosen=None
    — not tradeable, not force-filled."""
    n = 1600
    hr = pd.Series(np.full(n, 0.5))
    results = {
        "dead": {
            "white1": (pd.Series(_ar1(n, _phi_for_halflife(1.0), 1.0, 7)), hr),
            "white2": (pd.Series(_ar1(n, _phi_for_halflife(0.7), 1.0, 8)), hr),
        }
    }
    sel = select_tradeable_method(results, warmup=252)
    chosen, _ = sel["dead"]
    assert chosen is None


# ----------------------------------------------------------------- E4
# rv_xterm DV01 hedge removed (was a copy-paste of rv_hy_ig's 4y/9y ratio).


def test_e4_xterm_has_no_dv01(features: pd.DataFrame, credit_data: pd.DataFrame) -> None:
    """The DV01 method is unavailable for rv_xterm — not present in
    dv01_hedge output nor in build_all_residuals' rv_xterm methods."""
    import pycredit

    dv = dv01_hedge(features, credit_data, pycredit)
    assert "rv_xterm" not in dv, "rv_xterm must have no DV01 hedge (E4)"

    results = build_all_residuals(features, credit_data, pycredit)
    assert "dv01" not in results["rv_xterm"], "rv_xterm should expose only OLS/Kalman"
    assert "dv01" in results["rv_hy_ig"]  # the real DV01 pairs keep it
    assert "dv01" in results["rv_credit_rates"]


def test_e4_no_copy_of_pair1_dv01(features: pd.DataFrame, credit_data: pd.DataFrame) -> None:
    """Guard against the old bug returning: if any rv_xterm DV01 series
    existed, it must not equal rv_hy_ig's DV01 hedge ratio."""
    import pycredit

    dv = dv01_hedge(features, credit_data, pycredit)
    if "rv_xterm" in dv:  # belt-and-suspenders; current code drops it entirely
        _, hr_x = dv["rv_xterm"]
        _, hr_1 = dv["rv_hy_ig"]
        assert not hr_x.equals(hr_1), "rv_xterm DV01 is a verbatim copy of rv_hy_ig's"


# ----------------------------------------------------------------- R3
# canonical_residuals — single source of truth.


def test_canonical_selects_one_method_per_pair(
    features: pd.DataFrame, credit_data: pd.DataFrame
) -> None:
    """canonical_residuals returns exactly one chosen method per pair, with
    aligned residual/hedge/z, and the chosen residual matches what the
    selector picked from build_all_residuals (no second computation path)."""
    import pycredit

    canon = canonical_residuals(features, credit_data, pycredit)
    results = build_all_residuals(features, credit_data, pycredit)
    selection = select_tradeable_method(results)

    for pair, info in canon.items():
        chosen = selection[pair][0]
        assert info["method"] == chosen
        assert info["residual"].index.equals(features.index)
        if chosen is not None:
            expected_resid, expected_hr = results[pair][chosen]
            pd.testing.assert_series_equal(
                info["residual"], expected_resid, check_names=False
            )
            pd.testing.assert_series_equal(
                info["hedge_ratio"], expected_hr, check_names=False
            )
        else:
            assert info["residual"].isna().all()


# ----------------------------------------------------------------- E1
# One residual, consumed identically by features.parquet, the dashboard,
# and the backtest.

PAIR_TO_RESID_COL = {
    "rv_hy_ig": "rv_hy_ig_residual",
    "rv_credit_rates": "rv_credit_rates_residual",
    "rv_xterm": "rv_xterm_residual",
}


def test_e1_all_consumers_read_identical_residual(
    features: pd.DataFrame, credit_data: pd.DataFrame
) -> None:
    """The residual stored in features.parquet, read by the dashboard, and
    fed to the backtest engine are bit-identical for every pair."""
    import pycredit

    from backtest.ab_test import _resolve_method
    from dashboard.loader import load_features

    # (a) features.parquet column (direct read)
    parquet = pd.read_parquet(PROCESSED_PATH)
    # (b) dashboard accessor — same I/O boundary the views use
    dash = load_features()
    # (c) backtest path — what build_strategy feeds the engine for the
    #     canonical (tradeability-selected) method
    residuals = build_all_residuals(features, credit_data, pycredit)

    for pair, col in PAIR_TO_RESID_COL.items():
        method = _resolve_method(residuals, pair, None)  # canonical, not hardcoded
        bt_resid, _ = residuals[pair][method]

        a = parquet[col]
        b = dash[col]
        # dashboard vs parquet: same bytes
        pd.testing.assert_series_equal(a, b, check_names=True, check_exact=True)
        # backtest vs parquet: bit-identical (parquet round-trips float64
        # exactly; ols_hedge is deterministic). Names differ (column vs
        # computed), so check_names=False; check_exact enforces bit-identity.
        pd.testing.assert_series_equal(
            a, bt_resid, check_names=False, check_freq=False, check_exact=True
        )


def test_e1_backtest_method_is_canonical_not_hardcoded(
    features: pd.DataFrame, credit_data: pd.DataFrame
) -> None:
    """The headline backtest resolves its method from the selector (None →
    canonical), and that method matches what canonical_residuals selected."""
    import pycredit

    from backtest.ab_test import _resolve_method

    residuals = build_all_residuals(features, credit_data, pycredit)
    canon = canonical_residuals(features, credit_data, pycredit)
    for pair in PAIR_TO_RESID_COL:
        assert _resolve_method(residuals, pair, None) == canon[pair]["method"]


# ----------------------------------------------------------------- R9
# Leakage / trailing-only: perturbing a FUTURE bar must not change any
# residual at an EARLIER date (for both OLS canonical and Kalman innovation).

T0 = 3000  # perturbation index — well past warmup, well before the end


@pytest.mark.parametrize("perturb_col", ["hy_spread", "ig_spread"])
@pytest.mark.parametrize("method_fn", [ols_hedge, kalman_hedge], ids=["ols", "kalman"])
def test_r9_no_future_leakage(features: pd.DataFrame, perturb_col: str, method_fn) -> None:
    """Shock a single future bar of an input spread; every residual strictly
    before that bar must be bit-identical. Proves the Kalman innovation at t
    is invariant to data at t+k (k≥1), and OLS is trailing-only."""
    y, x = features["hy_spread"].copy(), features["ig_spread"].copy()
    base, _ = method_fn(y, x)

    pert = features[perturb_col].copy()
    pert.iloc[T0] = pert.iloc[T0] + 10.0  # large shock at a future bar
    if perturb_col == "hy_spread":
        shocked, _ = method_fn(pert, x)
    else:
        shocked, _ = method_fn(y, pert)

    # everything strictly before T0 must be unchanged, to the bit
    pd.testing.assert_series_equal(
        base.iloc[:T0], shocked.iloc[:T0], check_exact=True, check_names=False
    )
    # sanity: the shock DID move something at/after T0 (test isn't vacuous)
    assert not base.iloc[T0:].equals(shocked.iloc[T0:])


def test_r9_fill_lag_preserved() -> None:
    """The backtest still fills one day late end-to-end (no same-bar fill)."""
    from backtest.ab_test import FILL_LAG

    assert FILL_LAG == 1
