"""Tests for backtest/multi_asset.py (sprint v8.2, gates B1, B2, and a
no-look-ahead check on the P&L accumulator itself, separate from E1's
check on signal/position construction).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.multi_asset import annualized_turnover, run_multi_asset
from execution.costs import CostParams
from signals.etf_universe import UNIVERSE, load_universe_close
from signals.trend_signal import compute_trend, shift_to_next_day, to_position_matrix

NOTIONAL = 1_000_000.0


def _hand_worked_example() -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.bdate_range("2024-01-01", periods=3)
    close = pd.DataFrame({"A": [100.0, 101.0, 99.0], "B": [50.0, 49.0, 51.0]}, index=idx)
    target = pd.DataFrame({"A": [0.5, 0.3, 0.3], "B": [0.0, 0.2, -0.1]}, index=idx)
    return close, target


def _reference_pnl(close: pd.DataFrame, target: pd.DataFrame, cp: CostParams, notional: float) -> pd.Series:
    """Independent re-derivation of the P&L formula, written separately
    from backtest/multi_asset.py, used as the B1 reconciliation oracle.
    """
    ret = close.pct_change(fill_method=None)
    pnl_gross = (target * ret).sum(axis=1, skipna=True) * notional
    delta = target.diff().abs().sum(axis=1, skipna=True)
    turnover_cost = (cp.half_spread_bp + cp.slippage_bp) * 1e-4 * delta * notional
    short_exposure = target.clip(upper=0.0).abs().sum(axis=1, skipna=True)
    borrow_cost = cp.borrow_annual / 252 * notional * short_exposure
    return pnl_gross - turnover_cost - borrow_cost


# ---------------------------------------------------------------- B1: P&L reconciliation

def test_b1_pnl_reconciles_with_independent_reference() -> None:
    close, target = _hand_worked_example()
    result = run_multi_asset(target, close, notional=NOTIONAL)
    expected = _reference_pnl(close, target, CostParams(), NOTIONAL)
    pd.testing.assert_series_equal(result.daily_pnl, expected, check_names=False)


def test_b1_first_day_has_no_return_pnl() -> None:
    """The first row has no prior close, so ret is NaN and the only
    possible non-zero term is cost, which is also zero on day 0 (no prior
    target to diff against, no short yet by construction here).
    """
    close, target = _hand_worked_example()
    result = run_multi_asset(target, close, notional=NOTIONAL)
    assert result.daily_pnl.iloc[0] == 0.0


def test_b1_unshifted_weight_is_a_lookahead_bug_demonstration() -> None:
    """Passing the un-shifted `weight` (computed FROM close(t), per
    compute_trend) instead of the shifted `target` (the position actually
    HELD entering day t) makes day t's position trade on day t's own
    return -- the exact look-ahead bug this module's docstring warns
    against. This test demonstrates the two are NOT interchangeable, so a
    caller cannot accidentally swap them without changing the result.
    """
    close, target = _hand_worked_example()
    unshifted = target  # reinterpreted as if it were "weight" (computed from close(t))
    shifted = target.shift(1)
    shifted.iloc[0] = 0.0

    pnl_unshifted = run_multi_asset(unshifted, close, notional=NOTIONAL).daily_pnl
    pnl_shifted = run_multi_asset(shifted, close, notional=NOTIONAL).daily_pnl
    assert not pnl_unshifted.equals(pnl_shifted)


def test_b1_no_lookahead_on_real_universe() -> None:
    close = load_universe_close()
    tidy = compute_trend(close)
    target = shift_to_next_day(to_position_matrix(tidy))

    cutoff = close.index[len(close) // 2]
    baseline = run_multi_asset(target, close, notional=NOTIONAL)

    perturbed_close = close.copy()
    perturbed_close.loc[perturbed_close.index > cutoff] *= 5.0
    perturbed_tidy = compute_trend(perturbed_close)
    perturbed_target = shift_to_next_day(to_position_matrix(perturbed_tidy))
    perturbed = run_multi_asset(perturbed_target, perturbed_close, notional=NOTIONAL)

    base_prefix = baseline.daily_pnl.loc[:cutoff]
    pert_prefix = perturbed.daily_pnl.loc[:cutoff]
    pd.testing.assert_series_equal(base_prefix, pert_prefix)


# ---------------------------------------------------------------- B2: cost model fidelity

def test_b2_default_cost_params_match_v6_5_constants() -> None:
    cp = CostParams()
    assert cp.half_spread_bp == 1.5
    assert cp.slippage_bp == 0.5
    assert cp.borrow_annual == 0.004


def test_b2_run_multi_asset_uses_default_cost_params_unless_overridden() -> None:
    close, target = _hand_worked_example()
    default_result = run_multi_asset(target, close, notional=NOTIONAL)
    explicit_result = run_multi_asset(target, close, notional=NOTIONAL, cost_params=CostParams())
    pd.testing.assert_series_equal(default_result.daily_pnl, explicit_result.daily_pnl)

    looser = CostParams(half_spread_bp=0.0, slippage_bp=0.0, borrow_annual=0.0)
    looser_result = run_multi_asset(target, close, notional=NOTIONAL, cost_params=looser)
    assert not looser_result.daily_pnl.equals(default_result.daily_pnl)


# ---------------------------------------------------------------- turnover metric

def test_annualized_turnover_hand_worked_example() -> None:
    _, target = _hand_worked_example()
    # daily turnover: day0 NaN->0, day1 |0.3-0.5|+|0.2-0|=0.4, day2 |0.3-0.3|+|-0.1-0.2|=0.3
    expected = np.mean([0.0, 0.4, 0.3]) * 252
    assert annualized_turnover(target) == pytest.approx(expected)


def test_annualized_turnover_real_universe_is_finite_and_positive() -> None:
    close = load_universe_close()
    tidy = compute_trend(close)
    target = shift_to_next_day(to_position_matrix(tidy))
    t = annualized_turnover(target)
    assert np.isfinite(t)
    assert t > 0


def test_target_columns_match_universe() -> None:
    close = load_universe_close()
    tidy = compute_trend(close)
    target = shift_to_next_day(to_position_matrix(tidy))
    assert set(target.columns) == set(UNIVERSE)
