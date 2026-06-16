"""Reconciliation and no-look-ahead tests for sprint v8.3 (risk/attribution.py).

No edge claim. These tests check that the seven decompositions reconcile
to the quantity they claim to decompose (R1-R7) and that the attribution
layer, including the rolling factor regression, introduces no look-ahead
(E1').

R1-R6 reconcile to total daily gross P&L. R7 reconciles to total portfolio
volatility via the Euler identity, not to P&L -- a different reconciled
quantity, tested separately, not conflated with R1-R6.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.attribution import (
    NOTIONAL_DEFAULT,
    RECONCILE_TOL,
    SLEEVES,
    build_tidy_attribution,
    build_v82_book,
    carry_price_pnl,
    directional_selection_pnl,
    factor_returns,
    gross_net_cost,
    gross_pnl_series,
    long_short_pnl,
    mctr_by_sleeve,
    per_asset_class_pnl,
    per_instrument_pnl,
    rolling_factor_regression,
)
from signals.dividends import load_dividend_matrix
from signals.etf_universe import RAW_DIR, UNIVERSE, load_universe_close


@pytest.fixture(scope="module")
def close() -> pd.DataFrame:
    assert (RAW_DIR / "SPY.parquet").exists(), "run signals.etf_universe.ingest() first"
    return load_universe_close()


@pytest.fixture(scope="module")
def book(close: pd.DataFrame):
    return build_v82_book(close)


@pytest.fixture(scope="module")
def target(book) -> pd.DataFrame:
    return book[0]


@pytest.fixture(scope="module")
def result(book):
    return book[1]


@pytest.fixture(scope="module")
def gross(result) -> pd.Series:
    return gross_pnl_series(result)


@pytest.fixture(scope="module")
def pnl_i(target: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    return per_instrument_pnl(target, close)


@pytest.fixture(scope="module")
def dividends(close: pd.DataFrame) -> pd.DataFrame:
    return load_dividend_matrix(UNIVERSE, close.index)


def _max_abs_residual(a: pd.Series, b: pd.Series) -> float:
    idx = a.index.intersection(b.index)
    return float((a.loc[idx] - b.loc[idx]).abs().max())


# ---------------------------------------------------------------- R1

def test_r1_per_instrument_reconciles(pnl_i: pd.DataFrame, gross: pd.Series) -> None:
    total = pnl_i.sum(axis=1, skipna=True)
    assert _max_abs_residual(total, gross) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r1_per_asset_class_reconciles(pnl_i: pd.DataFrame, gross: pd.Series) -> None:
    pnl_class = per_asset_class_pnl(pnl_i)
    assert set(pnl_class.columns) == set(SLEEVES)
    total = pnl_class.sum(axis=1, skipna=True)
    assert _max_abs_residual(total, gross) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r1_asset_class_grouping_no_double_count_no_omission(pnl_i: pd.DataFrame) -> None:
    pnl_class = per_asset_class_pnl(pnl_i)
    # every ticker's pnl must appear in exactly one sleeve's sum
    direct_total = pnl_i.sum(axis=1, skipna=True)
    sleeve_total = pnl_class.sum(axis=1, skipna=True)
    pd.testing.assert_series_equal(
        direct_total.rename(None), sleeve_total.rename(None), check_exact=False, atol=1e-6
    )


# ---------------------------------------------------------------- R2

def test_r2_long_short_reconciles(target: pd.DataFrame, pnl_i: pd.DataFrame, gross: pd.Series) -> None:
    ls = long_short_pnl(target, pnl_i)
    total = ls["pnl_long"] + ls["pnl_short"]
    assert _max_abs_residual(total, gross) <= RECONCILE_TOL * NOTIONAL_DEFAULT


# ---------------------------------------------------------------- R3

def test_r3_directional_selection_reconciles(
    target: pd.DataFrame, close: pd.DataFrame, gross: pd.Series
) -> None:
    d3 = directional_selection_pnl(target, close, gross)
    total = d3["directional"] + d3["selection"]
    assert _max_abs_residual(total, gross) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r3_selection_is_exactly_the_residual(
    target: pd.DataFrame, close: pd.DataFrame, gross: pd.Series
) -> None:
    """selection is defined as gross_pnl - directional -- this is a
    tautological identity, not an empirical test, and the test name says
    so rather than implying otherwise.
    """
    d3 = directional_selection_pnl(target, close, gross)
    idx = d3.index
    expected_selection = gross.loc[idx] - d3["directional"]
    pd.testing.assert_series_equal(d3["selection"], expected_selection, check_names=False)


# ---------------------------------------------------------------- R4

@pytest.fixture(scope="module")
def factors(close: pd.DataFrame) -> pd.DataFrame:
    return factor_returns(close)


@pytest.fixture(scope="module")
def factor_result(gross: pd.Series, factors: pd.DataFrame):
    book_ret = gross / NOTIONAL_DEFAULT
    return rolling_factor_regression(book_ret, factors)


def test_r4_reconciles(factor_result, gross: pd.Series) -> None:
    total = factor_result.beta_explained + factor_result.residual
    assert _max_abs_residual(total, gross) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r4_r_squared_is_a_real_nontrivial_measurement(factor_result) -> None:
    """Not a reconciliation check -- a sanity check that R^2 is a genuine
    empirical number (between 0 and 1, not degenerate at exactly 0 or 1
    every day), consistent with the PRD's framing that this is the one
    non-tautological output of the sprint.
    """
    r2 = factor_result.r_squared.dropna()
    assert len(r2) > 100
    assert (r2 >= -1e-9).all()
    assert (r2 <= 1 + 1e-9).all()
    assert r2.std() > 0, "R^2 should vary over time, not be a constant"


# ---------------------------------------------------------------- E1' for the factor regression (no future leakage)

def test_factor_regression_no_future_leakage(close: pd.DataFrame, result, gross: pd.Series) -> None:
    factors_baseline = factor_returns(close)
    book_ret = gross / NOTIONAL_DEFAULT
    baseline = rolling_factor_regression(book_ret, factors_baseline)

    cutoff = close.index[len(close) // 2]
    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    factors_perturbed = factor_returns(perturbed_close)
    perturbed = rolling_factor_regression(book_ret, factors_perturbed)

    pd.testing.assert_frame_equal(
        baseline.betas.loc[:cutoff], perturbed.betas.loc[:cutoff]
    )
    pd.testing.assert_series_equal(
        baseline.beta_explained.loc[:cutoff], perturbed.beta_explained.loc[:cutoff]
    )


def test_factor_regression_beta_at_t_uses_only_data_through_t_minus_1() -> None:
    """Synthetic, fully controlled check: perturb only the factor value
    on the evaluation day itself (not the fitting window) and confirm the
    beta used to explain that day is unchanged -- the beta is a function
    of the trailing window only, not of day t's own factor value.
    """
    idx = pd.bdate_range("2015-01-01", periods=400)
    rng = np.random.default_rng(0)
    factors = pd.DataFrame(
        {
            "eq": rng.normal(0, 0.01, 400),
            "rates": rng.normal(0, 0.01, 400),
            "credit": rng.normal(0, 0.01, 400),
            "gold": rng.normal(0, 0.01, 400),
        },
        index=idx,
    )
    book_ret = (
        0.5 * factors["eq"] + 0.3 * factors["rates"] + rng.normal(0, 0.001, 400)
    )
    book_ret = pd.Series(book_ret, index=idx)

    baseline = rolling_factor_regression(book_ret, factors, window=252)

    eval_day = idx[300]
    perturbed_factors = factors.copy()
    perturbed_factors.loc[eval_day, "eq"] += 10.0  # huge, obvious perturbation
    perturbed = rolling_factor_regression(book_ret, perturbed_factors, window=252)

    pos = idx.get_loc(eval_day)
    pd.testing.assert_series_equal(
        baseline.betas.iloc[pos], perturbed.betas.iloc[pos], check_names=False
    )


# ---------------------------------------------------------------- R5

def test_r5_carry_price_reconciles_per_instrument(
    target: pd.DataFrame, close: pd.DataFrame, dividends: pd.DataFrame, pnl_i: pd.DataFrame
) -> None:
    cp = carry_price_pnl(target, close, dividends, pnl_i)
    for ticker in UNIVERSE:
        recombined = cp[f"carry_{ticker}"] + cp[f"price_change_{ticker}"]
        assert _max_abs_residual(recombined, pnl_i[ticker]) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r5_gld_has_zero_carry(
    target: pd.DataFrame, close: pd.DataFrame, dividends: pd.DataFrame, pnl_i: pd.DataFrame
) -> None:
    """GLD pays no distributions (physical gold, no income) -- this is
    the correct expected value, not a data gap.
    """
    cp = carry_price_pnl(target, close, dividends, pnl_i)
    assert (cp["carry_GLD"].fillna(0.0) == 0.0).all()


def test_r5_carry_lumpiness_not_smoothed(dividends: pd.DataFrame) -> None:
    """Distribution data must be genuinely sparse (mostly zero), not
    amortized across non-payment days.
    """
    nonzero_frac = (dividends["HYG"] > 0).mean()
    assert 0 < nonzero_frac < 0.05, f"HYG dividend nonzero fraction {nonzero_frac} looks smoothed"


# ---------------------------------------------------------------- R6

def test_r6_gross_net_cost_reconciles(result) -> None:
    gnc = gross_net_cost(result)
    total = gnc["net_pnl"] + gnc["turnover_cost"] + gnc["borrow_cost"]
    assert _max_abs_residual(total, gnc["gross_pnl"]) <= RECONCILE_TOL * NOTIONAL_DEFAULT


def test_r6_cost_bps_consistent_with_dollar_cost(result) -> None:
    gnc = gross_net_cost(result)
    implied_dollars = gnc["turnover_cost_bps"] / 1e4 * NOTIONAL_DEFAULT
    assert _max_abs_residual(implied_dollars, gnc["turnover_cost"]) <= RECONCILE_TOL * NOTIONAL_DEFAULT


# ---------------------------------------------------------------- R7 (reconciles to portfolio vol, not P&L)

def test_r7_mctr_reconciles_to_portfolio_vol(target: pd.DataFrame, close: pd.DataFrame) -> None:
    mctr = mctr_by_sleeve(target, close)
    assert len(mctr) > 0
    sleeve_cols = [f"mctr_{s}" for s in SLEEVES]
    total = mctr[sleeve_cols].sum(axis=1)
    residual = (total - mctr["sigma_portfolio"]).abs()
    assert residual.max() <= 1e-9, "R7 must reconcile to sigma_portfolio, not to P&L"


def test_r7_both_modes_present(target: pd.DataFrame, close: pd.DataFrame) -> None:
    mctr = mctr_by_sleeve(target, close)
    assert set(mctr["mode"].unique()) == {"ex_ante", "realized"}


# ---------------------------------------------------------------- E1' general: no look-ahead in book construction

def test_book_construction_no_lookahead(close: pd.DataFrame) -> None:
    cutoff = close.index[len(close) // 2]
    baseline_target, baseline_result = build_v82_book(close)

    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed_target, perturbed_result = build_v82_book(perturbed_close)

    pd.testing.assert_frame_equal(
        baseline_target.loc[:cutoff], perturbed_target.loc[:cutoff]
    )
    pd.testing.assert_series_equal(
        baseline_result.daily_pnl.loc[:cutoff], perturbed_result.daily_pnl.loc[:cutoff]
    )


def test_per_instrument_pnl_no_lookahead(target: pd.DataFrame, close: pd.DataFrame) -> None:
    cutoff = close.index[len(close) // 2]
    baseline = per_instrument_pnl(target, close)

    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed = per_instrument_pnl(target, perturbed_close)

    pd.testing.assert_frame_equal(baseline.loc[:cutoff], perturbed.loc[:cutoff])


# ---------------------------------------------------------------- tidy output

def test_build_tidy_attribution_shape_and_no_nans_in_key_columns(
    target: pd.DataFrame,
    close: pd.DataFrame,
    result,
    dividends: pd.DataFrame,
    factor_result,
    pnl_i: pd.DataFrame,
    gross: pd.Series,
) -> None:
    d3 = directional_selection_pnl(target, close, gross)
    tidy = build_tidy_attribution(target, close, result, dividends, factor_result, d3)
    assert set(tidy["ticker"].unique()) == set(UNIVERSE)
    assert tidy["date"].is_monotonic_increasing or tidy.sort_values(["date", "ticker"]).equals(tidy)
    assert tidy["asset_class"].notna().all()

    # pnl, carry, price_change should be defined wherever the book is
    # active AND the underlying close is defined that day. A handful of
    # known trailing data gaps (e.g. HYG/IEF missing their last few
    # closes in this snapshot) leave weight defined (carried forward,
    # signals.trend_signal's documented gap behavior) while pnl is
    # correctly NaN that day -- this is the expected, bounded case, not
    # an unconditional invariant.
    close_long = close.reset_index().melt(id_vars="date", var_name="ticker", value_name="close_px")
    merged = tidy.merge(close_long, on=["date", "ticker"], how="left")
    active_and_priced = merged.dropna(subset=["weight", "close_px"])
    assert active_and_priced["pnl"].notna().all()
    assert active_and_priced["carry"].notna().all()
    assert active_and_priced["price_change"].notna().all()
    active_rows = merged.dropna(subset=["weight"])
    gap_rows = active_rows["close_px"].isna().sum()
    gap_frac = gap_rows / len(active_rows)
    assert gap_frac < 0.01, f"unexpectedly many trailing/interior data gaps: {gap_rows} ({gap_frac:.2%})"
