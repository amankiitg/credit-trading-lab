"""Unit tests for signals/nav_wedge.py.

These tests use synthetic fixture series to verify the wedge / z_wedge
math and the G0b / G0c gate-check logic are implemented correctly. They
do not claim a real NAV-wedge signal -- sprint v7.1's G0a probe found
that daily NAV is not retrievable via a free endpoint
(data/processed/nav_audit.md), so no real NAV series exists yet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals.nav_wedge import (
    DEFAULT_WINDOW,
    S1B_STATEMENT,
    check_date_alignment,
    check_eod_striking,
    compute_wedge,
    compute_z_wedge,
    g0b_passes,
    g0c_passes,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def test_compute_wedge_basic() -> None:
    idx = _dates(5)
    close = pd.Series([100.0, 101.0, 99.0, 102.0, 103.0], index=idx)
    nav = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0], index=idx)
    wedge = compute_wedge(close, nav)
    expected = pd.Series([0.0, 0.01, -0.01, 0.02, 0.03], index=idx)
    pd.testing.assert_series_equal(wedge, expected, check_names=False)


def test_compute_wedge_rejects_zero_nav() -> None:
    idx = _dates(3)
    close = pd.Series([100.0, 101.0, 99.0], index=idx)
    nav = pd.Series([100.0, 0.0, 100.0], index=idx)
    with pytest.raises(ValueError):
        compute_wedge(close, nav)


def test_z_wedge_window_is_pre_registered() -> None:
    """63d is fixed in sprints/v7.1/PRD.md and must not be casually changed."""
    assert DEFAULT_WINDOW == 63


def test_z_wedge_no_lookahead() -> None:
    rng = np.random.default_rng(0)
    idx = _dates(200)
    wedge = pd.Series(rng.normal(0, 0.01, size=200), index=idx)
    window = 63
    z = compute_z_wedge(wedge, window=window)

    assert z.iloc[: window - 1].isna().all()
    assert z.iloc[window - 1 :].notna().all()

    # z_wedge at t must be unchanged if values after t are perturbed
    perturbed = wedge.copy()
    perturbed.iloc[150:] = perturbed.iloc[150:] + 10.0
    z_perturbed = compute_z_wedge(perturbed, window=window)
    pd.testing.assert_series_equal(z.iloc[:150], z_perturbed.iloc[:150])


def test_check_date_alignment_passes_on_clean_series() -> None:
    rng = np.random.default_rng(1)
    idx = _dates(300)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.005, size=300))), index=idx)
    nav = close * 1.001  # same series, small constant wedge, no date shift
    lag_corr = check_date_alignment(nav, close, max_lag=2)
    assert g0b_passes(lag_corr)


def test_check_date_alignment_detects_one_day_offset() -> None:
    rng = np.random.default_rng(2)
    idx = _dates(300)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.005, size=300))), index=idx)
    shifted_nav = close.shift(1).bfill()  # NAV artificially offset by one day
    lag_corr = check_date_alignment(shifted_nav, close, max_lag=2)
    assert not g0b_passes(lag_corr)


def test_check_eod_striking_matches_within_tolerance() -> None:
    idx = _dates(5)
    nav = pd.Series([100.0, 100.5, 99.8, 101.0, 100.2], index=idx)
    reference = {idx[2]: 99.8, idx[4]: 100.2}
    spot_check = check_eod_striking(nav, reference)
    assert g0c_passes(spot_check)


def test_check_eod_striking_flags_mismatch() -> None:
    idx = _dates(5)
    nav = pd.Series([100.0, 100.5, 99.8, 101.0, 100.2], index=idx)
    reference = {idx[2]: 95.0}  # deliberately wrong reference value
    spot_check = check_eod_striking(nav, reference)
    assert not g0c_passes(spot_check)


def test_check_eod_striking_flags_missing_date() -> None:
    idx = _dates(5)
    nav = pd.Series([100.0, 100.5, 99.8, 101.0, 100.2], index=idx)
    reference = {pd.Timestamp("2031-01-01"): 100.0}  # not in nav index
    spot_check = check_eod_striking(nav, reference)
    assert not g0c_passes(spot_check)


def test_s1b_statement_present() -> None:
    assert "not evidence of tradeability" in S1B_STATEMENT
    assert "No IC test" in S1B_STATEMENT
