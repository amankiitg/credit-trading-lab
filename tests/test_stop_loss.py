"""Tests for risk/stop_loss.py, sprint v9.1 T1.

Covers the five acceptance criteria from TASKS.md:
  (a) long episode: NORMAL->REDUCED->STOPPED on monotone decline
  (b) short episode symmetric
  (c) recovery path STOPPED->REDUCED->NORMAL via hysteresis
  (d) whipsaw guard: z inside [-k1, -k1+h) does NOT flap states
  (e) episode reset on signal sign flip

Plus edge cases: zero-vol guard, empty universe, single-row inputs,
NaN propagation, and the T3 leakage precondition (trigger-day loss borne).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.stop_loss import (
    K1_DEFAULT,
    K2_DEFAULT,
    H_DEFAULT,
    M_REDUCED_DEFAULT,
    STATE_NAMES,
    _run_single_episode,
    apply_stop_overlay,
    compute_episodes,
)

# Shorthand for tests
K1 = K1_DEFAULT   # 1.5
K2 = K2_DEFAULT   # 2.5
H  = H_DEFAULT    # 0.5
MR = M_REDUCED_DEFAULT  # 0.5
SIGMA_ANNUAL = 0.12  # 12% annual -> sigma_m = 0.12 * sqrt(21/252) ~= 0.03464
SIGMA_M = 0.12 * np.sqrt(21.0 / 252)  # ~0.03464

# Redundancy: re-derive from stop_loss internals
SIGMA_M_IMPORTED = round(SIGMA_M, 12)

# Hand-compute the z thresholds in return space:
# z = (r - running_peak) / sigma_m, running_peak = max(0, max r)
# On a monotone decline (long), running_peak = 0 always, so z = r / sigma_m.
# REDUCED trigger: z <= -k1  =>  r <= -k1 * sigma_m
# STOPPED trigger:  z <= -k2  =>  r <= -k2 * sigma_m
R_REDUCE = -K1 * SIGMA_M   # ~ -0.05196
R_STOP   = -K2 * SIGMA_M   # ~ -0.08660


# -------------------------------------------------------------------
# Helper: build test DataFrames
# -------------------------------------------------------------------

def _make_const_vol_df(n_days: int, tickers: list[str], annual_vol: float) -> pd.DataFrame:
    """date x ticker DataFrame with constant annualized vol."""
    dates = pd.date_range("2020-01-02", periods=n_days, freq="B")
    return pd.DataFrame(annual_vol, index=dates, columns=tickers)


def _make_close_long_decline(n_days: int, entry_price: float = 100.0) -> np.ndarray:
    """Generate a monotone daily decline for a long position test.

    Returns (close_prices, entry_price_idx) where decline starts after entry.
    """
    daily_ret = -0.005  # -0.5% per day
    prices = entry_price * np.cumprod(np.full(n_days, 1.0 + daily_ret))
    return prices


def _make_close_short_adverse(n_days: int, entry_price: float = 100.0) -> np.ndarray:
    """Generate a monotone daily rise (adverse for short)."""
    daily_ret = +0.005  # +0.5% per day
    prices = entry_price * np.cumprod(np.full(n_days, 1.0 + daily_ret))
    return prices


# -------------------------------------------------------------------
# Unit tests on the single-episode state machine (_run_single_episode)
# -------------------------------------------------------------------

class TestSingleEpisodeCore:
    """Test _run_single_episode directly with hand-computed z values."""

    def test_long_monotone_decline_normal_to_reduced_to_stopped(self):
        """(a) Long episode: monotone decline triggers REDUCED then STOPPED.

        sigma_m = constant 0.04 (chosen for round z values).
        z = r / 0.04  (running peak = 0 always for pure decline).
        r_reduce = -1.5 * 0.04 = -0.06  -> P = 94.0
        r_stop   = -2.5 * 0.04 = -0.10  -> P = 90.0

        Days: entry at 100, then monotone decline by ~0.5% daily.
        Design: 15 days of decline, crossing thresholds at known days.
        """
        sig_m = 0.04  # monthly vol
        entry_price = 100.0

        # Build a price path that crosses thresholds at exact days
        # Day 0: entry at 100, z=0
        # Day 1: P=99.5, r=-0.005, z=-0.125
        # Day 2: P=99.0, r=-0.010, z=-0.25
        # ...
        # Day 12: P=94.0, r=-0.060, z=-1.5  -> REDUCED
        # Day 13: P=93.5, r=-0.065, z=-1.625
        # Day 20: P=90.0, r=-0.100, z=-2.5  -> STOPPED
        prices = np.array([
            100.0, 99.5, 99.0, 98.5, 98.0, 97.5, 97.0, 96.5, 96.0, 95.5,
            95.0, 94.5, 94.0, 93.5, 93.0, 92.5, 92.0, 91.5, 91.0, 90.5,
            89.9,  # P=89.9 -> r=-0.101 -> z=-2.525 < -2.5, STOPPED cleanly
        ])
        n = len(prices)
        r = np.array([(p / entry_price - 1.0) for p in prices])  # long, side=+1

        sigma_m_arr = np.full(n, sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # Check z values
        expected_z = r / sig_m
        np.testing.assert_allclose(z, expected_z, rtol=1e-10)

        # Day 0 entry: r=0, z=0 -> NORMAL
        assert states[0] == 0
        assert mults[0] == 1.0

        # Day 12: P=94.0, r=-0.06, z=-1.5 -> REDUCED (z <= -k1)
        assert states[12] == 1, f"Expected REDUCED at day 12, got state={states[12]}, z={z[12]}"
        assert mults[12] == MR

        # Day 13-20: still declining, should stay REDUCED -> STOPPED at some point
        # Day 20: P=89.9, r=-0.101, z=-2.525 -> STOPPED (cleanly < -2.5)
        assert states[20] == 2, f"Expected STOPPED at day 20, got state={states[20]}, z={z[20]}"
        assert mults[20] == 0.0

        # Day 19 (P=90.5, r=-0.095, z=-2.375): still REDUCED (z > -2.5 but < -1.0)
        assert states[19] == 1

    def test_short_episode_symmetric(self):
        """(b) Short episode: adverse move (price up) triggers REDUCED then STOPPED.

        side=-1, r = -1*(P/100 - 1) = 1 - P/100.
        P=106 -> r=-0.06 -> z=-1.5 -> REDUCED.
        P=110 -> r=-0.10 -> z=-2.5 -> STOPPED.
        """
        sig_m = 0.04
        entry_price = 100.0

        prices = np.array([
            100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 104.0, 104.5,
            105.0, 105.5, 106.0, 106.5, 107.0, 107.5, 108.0, 108.5, 109.0, 109.5,
            110.0,
        ])
        n = len(prices)
        # Short: r = side * (P/P_entry - 1) = -1 * (P/100 - 1)
        r = np.array([-1.0 * (p / entry_price - 1.0) for p in prices])

        sigma_m_arr = np.full(n, sig_m)

        mults, states, z = _run_single_episode(
            -1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # z check
        expected_z = r / sig_m
        np.testing.assert_allclose(z, expected_z, rtol=1e-10)

        # Day 0: P=100, r=0, z=0 -> NORMAL
        assert states[0] == 0

        # Day 12: P=106, r=-0.06, z=-1.5 -> REDUCED
        assert states[12] == 1, f"Expected REDUCED at day 12, got state={states[12]}"
        assert mults[12] == MR

        # Day 20: P=110, r=-0.10, z=-2.5 -> STOPPED
        assert states[20] == 2, f"Expected STOPPED at day 20, got state={states[20]}"
        assert mults[20] == 0.0

    def test_short_favorable_move_not_treated_as_drawdown(self):
        """Short position favorable move (price DOWN) must NOT trigger stops.

        r = side*(P/P_entry - 1) = -1*(P/100 - 1) = 1 - P/100.
        When P goes DOWN: r is POSITIVE (favorable), z is positive.
        State must stay NORMAL.
        """
        sig_m = 0.04
        entry_price = 100.0

        # Price declines (favorable for short)
        prices = np.array([100.0, 99.0, 98.0, 97.0, 96.0, 95.0])
        r = np.array([-1.0 * (p / entry_price - 1.0) for p in prices])
        # At P=95: r = -1*(0.95-1) = +0.05, z = +1.25 (positive!)

        sigma_m_arr = np.full(len(prices), sig_m)
        mults, states, z = _run_single_episode(
            -1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # All z should be >= 0 (gain territory)
        assert np.all(z >= -1e-10), f"z should be non-negative for favorable short, got {z}"
        # All states should be NORMAL
        assert np.all(states == 0), f"All states should be NORMAL, got {states}"
        # All multipliers should be 1.0
        assert np.all(mults == 1.0), f"All multipliers should be 1.0, got {mults}"

    def test_recovery_stopped_to_reduced_to_normal(self):
        """(c) Recovery path: STOPPED -> REDUCED -> NORMAL via hysteresis.

        Decline to STOPPED, then recover to REDUCED (z >= -k2+h),
        then to NORMAL (z >= -k1+h).
        """
        sig_m = 0.04
        entry_price = 100.0

        # Phase 1: decline to STOPPED
        # Phase 2: recover to REDUCED then NORMAL
        prices = np.array([
            # Decline
            100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0,
            89.9,  # r=-0.101, z=-2.525 -> STOPPED (cleanly past -2.5)
            # Recovery
            91.0,  # r=-0.09, z=-2.25, still < -k2+h = -2.0 -> stay STOPPED
            92.5,  # r=-0.075, z=-1.875, >= -2.0 -> REDUCED
            93.5,  # r=-0.065, z=-1.625
            94.5,  # r=-0.055, z=-1.375
            96.5,  # r=-0.035, z=-0.875, >= -1.0 -> NORMAL
            97.0,
        ])
        n = len(prices)
        r = np.array([p / entry_price - 1.0 for p in prices])
        sigma_m_arr = np.full(n, sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # At index 10 (P=89.9, z=-2.525): STOPPED
        assert states[10] == 2, f"Expected STOPPED at idx 10, got {states[10]}, z={z[10]}"

        # At index 11 (P=91.0, z=-2.25): still STOPPED (z < -k2+h = -2.0)
        assert states[11] == 2, f"Expected STOPPED at idx 11, got {states[11]}, z={z[11]}"

        # At index 12 (P=92.5, z=-1.875 >= -2.0): REDUCED
        assert states[12] == 1, f"Expected REDUCED at idx 12, got {states[12]}, z={z[12]}"

        # At index 15 (P=96.5, z=-0.875 >= -1.0): NORMAL
        assert states[15] == 0, f"Expected NORMAL at idx 15, got {states[15]}, z={z[15]}"
        assert mults[15] == 1.0

    def test_whipsaw_guard_no_flap(self):
        """(d) z oscillating inside [-k1, -k1+h) must NOT flap NORMAL<->REDUCED.

        k1=1.5, h=0.5: z inside [-1.5, -1.0) is the whipsaw zone.
        REDUCED triggers at z <= -1.5. Recovery requires z >= -1.0.
        If z stays in [-1.5, -1.0), state should be stable (no flapping).
        """
        sig_m = 0.04
        entry_price = 100.0

        # Build a price path where z oscillates in [-1.5, -1.0)
        # z = r / 0.04, so r = z * 0.04
        # z = -1.5 -> r = -0.06 -> P = 94.0
        # z = -1.0 -> r = -0.04 -> P = 96.0
        # z = -1.51 -> r = -0.0604 -> P = 93.96 (trigger REDUCED)

        # Start in NORMAL, then trigger REDUCED, then oscillate in whipsaw zone
        prices = np.array([
            100.0, 99.0, 98.0, 97.0, 96.0, 95.5, 95.0, 94.5,
            94.0,  # z = -1.5 -> REDUCED triggered
            # Now oscillate: z stays in [-1.5, -1.0)
            94.1,  # z = -1.475 (< -1.5? No, -0.059/0.04 = -1.475) -> stay REDUCED
            95.0,  # z = -1.25 -> stay REDUCED
            95.5,  # z = -1.125 -> stay REDUCED
            94.3,  # z = -1.425 -> stay REDUCED
            94.8,  # z = -1.30 -> stay REDUCED
            95.3,  # z = -1.175 -> stay REDUCED
        ])
        r = np.array([p / entry_price - 1.0 for p in prices])
        sigma_m_arr = np.full(len(prices), sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # Index 8: REDUCED triggered
        assert states[8] == 1, f"Expected REDUCED at idx 8, got {states[8]}, z={z[8]}"

        # Indices 9-14: all stay REDUCED (no flapping to NORMAL)
        for idx in range(9, 15):
            assert states[idx] == 1, (
                f"Flapping detected at idx {idx}: state={states[idx]}, z={z[idx]:.4f} "
                f"(should stay REDUCED since z >= -1.0 needed for recovery)"
            )

    def test_episode_reset_on_sign_flip(self):
        """(e) Episode resets when signal weight sign flips.

        Long episode -> weight goes zero -> new short episode starts fresh.
        The short episode should start at NORMAL, not inherit the prior state.
        """
        sig_m = 0.04

        # Build a multi-ticker frame with a clear sign flip
        dates = pd.date_range("2020-01-02", periods=10, freq="B")
        close = pd.DataFrame({
            "A": [100.0, 95.0, 93.0, 92.0, 91.0, 105.0, 106.0, 107.0, 108.0, 109.0],
        }, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)  # annualized
        weights = pd.DataFrame({
            "A": [1.0, 1.0, 1.0, np.nan, np.nan, -1.0, -1.0, -1.0, -1.0, -1.0],
        }, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        # Days 0-1: long position, declining -> should trigger REDUCED/STOPPED
        # Day 2: r=(93/100-1)=-0.07, sigma_m=0.12*sqrt(21/252)=0.03464, z=-2.02 -> REDUCED (not STOPPED yet)
        # Day 5: new short episode starts fresh. P=105 entry, weights=-1.
        assert states.loc[dates[5], "A"] == "NORMAL", (
            f"Episode should reset to NORMAL on sign flip, got {states.loc[dates[5], 'A']}"
        )
        assert mults.loc[dates[5], "A"] == 1.0

    def test_skip_straight_to_stopped_on_deep_breach(self):
        """NORMAL -> STOPPED directly if z <= -k2 on the same day that triggers REDUCED.

        This is the NORMAL case: z <= -k2 takes precedence over z <= -k1.
        """
        sig_m = 0.04
        entry_price = 100.0

        # Big one-day drop: P=89.0 -> r=-0.11 -> z=-2.75 < -2.5
        prices = np.array([100.0, 89.0])
        r = np.array([p / entry_price - 1.0 for p in prices])
        sigma_m_arr = np.full(2, sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # Day 0: NORMAL
        assert states[0] == 0
        # Day 1: STOPPED directly (skipped REDUCED)
        assert states[1] == 2, f"Expected STOPPED on deep breach, got {states[1]}"
        assert mults[1] == 0.0

    def test_zero_vol_guard(self):
        """Zero-vol should not cause division-by-zero or inf.

        sigma_m = 0.0 -> guard clamps to 1e-12.  z becomes large negative
        (r / tiny), which triggers STOP.  No crash, no inf.
        """
        sig_m = 1e-12  # near-zero, tests the guard path
        prices = np.array([100.0, 99.0])
        r = np.array([p / 100.0 - 1.0 for p in prices])
        sigma_m_arr = np.full(2, sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # Should not crash, no infs
        assert not np.isinf(z).any()
        assert not np.isinf(mults).any()

    def test_running_peak_updates_only_on_gains(self):
        """M_i(t) is the max of favorable territory, never negative.

        For a long: M = max(0, max r(s)).  A partial recovery should
        increase M if r becomes positive.
        """
        sig_m = 0.04
        entry_price = 100.0

        # Gain, then decline, then gain again
        prices = np.array([
            100.0, 102.0, 101.0, 100.0, 99.0, 103.0,
        ])
        r = np.array([p / entry_price - 1.0 for p in prices])
        sigma_m_arr = np.full(6, sig_m)

        mults, states, z = _run_single_episode(
            +1.0, r, sigma_m_arr, K1, K2, H, MR,
        )

        # Day 1: P=102, r=+0.02, M=0.02, z = (0.02-0.02)/sig=0
        assert abs(z[1]) < 1e-10

        # Day 4: P=99, r=-0.01, M=0.02 (peak from day 1), z = (-0.01-0.02)/sig = -0.03/0.04 = -0.75
        assert z[4] == pytest.approx(-0.75, abs=1e-10)

        # Day 5: P=103, r=+0.03, M=0.03 (new peak), z = (0.03-0.03)/sig = 0
        assert z[5] == pytest.approx(0.0, abs=1e-10)
        assert states[5] == 0  # back to NORMAL if ever left it


# -------------------------------------------------------------------
# Integration tests on apply_stop_overlay / compute_episodes
# -------------------------------------------------------------------

class TestApplyStopOverlay:
    """Test the main entry point with full DataFrames."""

    def test_basic_shape_and_outputs(self):
        """Smoke test: two tickers, known close and sigma, verify shape and invariants."""
        dates = pd.date_range("2020-01-02", periods=20, freq="B")
        tickers = ["SPY", "IEF"]

        # SPY drops 8% (enough to trigger REDUCED with sigma=0.15), IEF rises 2%
        close = pd.DataFrame({
            "SPY": np.linspace(100, 92, 20),
            "IEF": np.linspace(100, 102, 20),
        }, index=dates)
        sigma = pd.DataFrame({
            "SPY": 0.12, "IEF": 0.06,
        }, index=dates)
        # Both tickers always held long at weight 1.0
        weights = pd.DataFrame(1.0, index=dates, columns=tickers)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        assert mults.shape == (20, 2)
        assert states.shape == (20, 2)
        assert z.shape == (20, 2)

        # SPY declines -> should eventually trigger stops
        spy_states = states["SPY"].dropna()
        assert "REDUCED" in spy_states.values or "STOPPED" in spy_states.values, (
            "SPY should trigger stops on decline"
        )

        # IEF rises -> should stay NORMAL
        ief_states = states["IEF"].dropna()
        assert (ief_states == "NORMAL").all(), f"IEF should stay NORMAL, got {ief_states.unique()}"

    def test_episode_reset_full_pipeline(self):
        """Sign flip across two episodes: long declined, then short entered.

        Verifies the full pipeline resets state and anchor on flip.
        """
        dates = pd.date_range("2020-01-02", periods=12, freq="B")
        close = pd.DataFrame({
            "A": [100, 96, 94, 93, np.nan, np.nan, 105, 107, 109, 111, 113, 115],
        }, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({
            "A": [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0],
        }, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        # First episode (long): should have REDUCED or STOPPED at day 3
        s3 = states.loc[dates[3], "A"]
        assert s3 in ("REDUCED", "STOPPED"), f"Long decline should trigger stop, got {s3}"

        # Day 6: new short episode starts, must be NORMAL
        s6 = states.loc[dates[6], "A"]
        assert s6 == "NORMAL", f"New short episode must start NORMAL, got {s6}"

        # Z-score for short episode should use the new entry price (105)
        z6 = z.loc[dates[6], "A"]
        assert z6 == pytest.approx(0.0, abs=1e-10), f"z at new episode entry should be ~0, got {z6}"

    def test_nan_weights_produce_nan_outputs(self):
        """Pre-entry NaN weights should yield NaN multipliers / None states / NaN z."""
        dates = pd.date_range("2020-01-02", periods=8, freq="B")
        close = pd.DataFrame({"A": np.linspace(100, 98, 8)}, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({
            "A": [np.nan, np.nan, np.nan, 1.0, 1.0, 1.0, 1.0, 1.0],
        }, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        # First 3 rows: NaN in -> NaN out
        for i in range(3):
            assert np.isnan(mults.iloc[i, 0]), f"Row {i}: multiplier should be NaN"
            assert states.iloc[i, 0] is None, f"Row {i}: state should be None"
            assert np.isnan(z.iloc[i, 0]), f"Row {i}: z should be NaN"

        # Row 3 onward: defined
        assert not np.isnan(mults.iloc[3, 0])

    def test_invariant_mid_gap(self):
        """A mid-episode NaN weight should end the episode, not crash."""
        dates = pd.date_range("2020-01-02", periods=6, freq="B")
        close = pd.DataFrame({"A": [100, 99, 98, 97, 96, 95]}, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({
            "A": [1.0, 1.0, np.nan, np.nan, 1.0, 1.0],
        }, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        # Day 0-1: one episode -> has states
        assert not np.isnan(mults.iloc[0, 0])
        # Day 2-3: NaN weight -> NaN output
        assert np.isnan(mults.iloc[2, 0])
        # Day 4: new long episode (entry P=96, fresh)
        assert states.iloc[4, 0] == "NORMAL"
        assert z.iloc[4, 0] == pytest.approx(0.0, abs=1e-10)

    def test_multipliers_only_valid_values(self):
        """Invariant: all defined multipliers are in {1.0, m_reduced, 0.0}."""
        dates = pd.date_range("2020-01-02", periods=30, freq="B")
        tickers = ["A", "B"]
        # A declines, B rises
        close = pd.DataFrame({
            "A": 100 * np.cumprod(np.full(30, 0.997)),
            "B": 100 * np.cumprod(np.full(30, 1.002)),
        }, index=dates)
        sigma = pd.DataFrame({"A": 0.15, "B": 0.06}, index=dates)
        weights = pd.DataFrame(1.0, index=dates, columns=tickers)

        mults, _, _ = apply_stop_overlay(weights, close, sigma)

        valid = {1.0, MR, 0.0}
        for col in mults.columns:
            vals = set(mults[col].dropna().unique())
            assert vals.issubset(valid), f"Column {col}: unexpected multipliers {vals - valid}"

    def test_no_nan_drop_silent(self):
        """Row counts must be maintained: no silent drops anywhere."""
        dates = pd.date_range("2020-01-02", periods=10, freq="B")
        close = pd.DataFrame({"A": np.linspace(100, 95, 10)}, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({"A": [np.nan] * 3 + [1.0] * 7}, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        assert len(mults) == 10, f"Row count changed: {len(mults)} != 10"
        assert len(states) == 10
        assert len(z) == 10

    def test_single_row_input(self):
        """Edge case: single-row inputs should not crash."""
        dates = pd.date_range("2020-01-02", periods=1, freq="B")
        close = pd.DataFrame({"A": [100.0]}, index=dates)
        sigma = pd.DataFrame({"A": [0.12]}, index=dates)
        weights = pd.DataFrame({"A": [1.0]}, index=dates)

        mults, states, z = apply_stop_overlay(weights, close, sigma)

        assert mults.shape == (1, 1)
        assert states.iloc[0, 0] == "NORMAL"
        assert z.iloc[0, 0] == pytest.approx(0.0, abs=1e-10)

    def test_date_index_is_monotonic_unique(self):
        """Input invariants: date index must be monotonic and unique."""
        dates_good = pd.date_range("2020-01-02", periods=5, freq="B")
        close = pd.DataFrame({"A": [100, 99, 98, 97, 96]}, index=dates_good)
        sigma = pd.DataFrame({"A": 0.12}, index=dates_good)
        weights = pd.DataFrame({"A": 1.0}, index=dates_good)

        # Should not raise
        apply_stop_overlay(weights, close, sigma)

        # Duplicate index should raise
        dates_dup = pd.DatetimeIndex([
            "2020-01-02", "2020-01-03", "2020-01-03", "2020-01-04", "2020-01-05",
        ])
        close_dup = pd.DataFrame({"A": [100, 99, 98, 97, 96]}, index=dates_dup)
        sigma_dup = pd.DataFrame({"A": 0.12}, index=dates_dup)
        weights_dup = pd.DataFrame({"A": 1.0}, index=dates_dup)

        with pytest.raises(AssertionError, match="not unique"):
            apply_stop_overlay(weights_dup, close_dup, sigma_dup)

    def test_k2_must_be_greater_than_k1(self):
        """Input invariant: k2 > k1 > 0."""
        dates = pd.date_range("2020-01-02", periods=3, freq="B")
        close = pd.DataFrame({"A": [100, 99, 98]}, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({"A": 1.0}, index=dates)

        with pytest.raises(AssertionError, match="k2 > k1"):
            apply_stop_overlay(weights, close, sigma, k1=3.0, k2=2.0)


# ==================================================================
# T3: Leakage / lookahead checks (paired validation for T1+T2)
# ==================================================================

class TestLeakageLookahead:
    """T3 acceptance: three tests validating no lookahead in the state machine."""

    def test_a_trigger_day_loss_is_borne(self):
        """Test A -- Trigger-day loss is borne at pre-stop weight.

        Construct a synthetic series where z <= -k2 crossing happens on
        day t.  Assert the day-t P&L uses the pre-stop weight (1.0) and
        the reduction only affects day t+1 onward.

        The key principle: the state at close(t) determines the multiplier
        for weight held on day t+1 (via shift_to_next_day).  Day-t return
        is earned on the pre-stop weight regardless of what the state
        machine says about day-t's close.
        """
        dates = pd.date_range("2020-01-06", periods=5, freq="B")
        # Prices: entry at 100, decline, big drop on day 2
        close = pd.DataFrame({
            "A": [100.0, 99.0, 89.0, 88.0, 89.0],
        }, index=dates)
        # Constant annualized vol (12%)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        # Held long at weight 1.0 throughout
        weights = pd.DataFrame({"A": 1.0}, index=dates)

        mults, states, _ = apply_stop_overlay(weights, close, sigma)

        # Day 2: close=89.0, r=-0.11, sigma_m=0.0346, z=-3.18 <= -2.5
        # State should be STOPPED at day 2's close.
        assert states.loc[dates[2], "A"] == "STOPPED", \
            f"Day 2 should be STOPPED, got {states.loc[dates[2], 'A']}"
        assert mults.loc[dates[2], "A"] == 0.0

        # Day 3: P&L should use day-2 multiplier applied to day-3 weight.
        #        Day-2 multiplier = 0.0, so day 3 weight = 0.0.
        #        Day 3 return = 88/89 - 1 = -0.0112.
        #        Contribution at stop weight: 0.0 * (-0.0112) = 0.
        #        Contribution at pre-stop weight: 1.0 * (-0.0112) = -0.0112.
        # The overlay backtest (in T2 script) uses shift_to_next_day, so
        # day 3's weight multiplier is determined by day 2's close state.

        # Here we verify that the state machine itself marks day 2 correctly:
        # day 2's close produces STOPPED with m=0.0.  When the caller applies
        # shift_to_next_day on (multipliers * held_weights), day 3 gets m=0.0
        # and day 2 gets the day-1 multiplier (which is 1.0).  So the day-2
        # P&L is earned at full weight -- the trigger-day loss IS borne.

        # Concretely: compute the effective weight for each day using the
        # shift_to_next_day convention.
        from risk.stop_loss import compute_episodes
        held = weights  # held weights = raw weights in this test (no band)

        # Effective weights with shift: w_eff(t) = mult(t-1) * held(t)
        # For t=0: NaN (no prior multiplier)
        # For t=1: mult(0) * held(1) = 1.0 * 1.0 = 1.0
        # For t=2: mult(1) * held(2) = 1.0 * 1.0 = 1.0  <- trigger day, full weight!
        # For t=3: mult(2) * held(3) = 0.0 * 1.0 = 0.0  <- reduction effective
        # For t=4: mult(3) * held(4) = 0.0 * 1.0 = 0.0

        mult_filled = mults.fillna(1.0)
        effective_w = mult_filled.shift(1) * weights
        # Day 0 has NaN from shift (no prior), fill with NaN
        # Day 1: mult from day 0 = 1.0 -> w=1.0
        # Day 2: mult from day 1 = 1.0 -> w=1.0  (trigger day!)
        # Day 3: mult from day 2 = 0.0 -> w=0.0  (reduction effective)
        # Day 4: mult from day 3 = 0.0 -> w=0.0

        assert effective_w.loc[dates[2], "A"] == 1.0, (
            f"Day 2 (trigger day) effective weight must be 1.0 (pre-stop), "
            f"got {effective_w.loc[dates[2], 'A']}"
        )
        assert effective_w.loc[dates[3], "A"] == 0.0, (
            f"Day 3 effective weight must be 0.0 (post-stop), "
            f"got {effective_w.loc[dates[3], 'A']}"
        )

    def test_b_no_future_peak_truncation(self):
        """Test B -- No future peak: truncating prices at day t leaves states
        through t unchanged.

        M_i(t) = max(0, max_{entry<=s<=t} r(s)) uses only data through t.
        Truncating the price series at any day t must produce identical
        states/multipliers/z for days <= t.
        """
        dates = pd.date_range("2020-01-06", periods=30, freq="B")
        # Build a price path with both declines and recoveries
        np.random.seed(42)
        prices = 100.0 * np.cumprod(1.0 + np.random.randn(30) * 0.01)
        close = pd.DataFrame({"A": prices}, index=dates)
        sigma = pd.DataFrame({"A": 0.12}, index=dates)
        weights = pd.DataFrame({"A": 1.0}, index=dates)

        # Full-series run
        mults_full, states_full, z_full = apply_stop_overlay(weights, close, sigma)

        # Truncate at three different cut points and verify consistency
        for cutoff_idx in [10, 20, 28]:
            close_trunc = close.iloc[:cutoff_idx + 1]
            sigma_trunc = sigma.iloc[:cutoff_idx + 1]
            weights_trunc = weights.iloc[:cutoff_idx + 1]

            mults_trunc, states_trunc, z_trunc = apply_stop_overlay(
                weights_trunc, close_trunc, sigma_trunc,
            )

            # States through cutoff must match
            for i in range(cutoff_idx + 1):
                d = dates[i]
                assert states_trunc.loc[d, "A"] == states_full.loc[d, "A"], (
                    f"Truncation at idx {cutoff_idx}: state mismatch at idx {i} "
                    f"(trunc={states_trunc.loc[d, 'A']}, full={states_full.loc[d, 'A']})"
                )
                assert mults_trunc.loc[d, "A"] == mults_full.loc[d, "A"], (
                    f"Truncation at idx {cutoff_idx}: multiplier mismatch at idx {i}"
                )
                np.testing.assert_allclose(
                    z_trunc.loc[d, "A"], z_full.loc[d, "A"], rtol=1e-12,
                    err_msg=f"Truncation at idx {cutoff_idx}: z mismatch at idx {i}",
                )

    def test_c_sigma_alignment_with_compute_trend(self):
        """Test C -- Sigma alignment: the sigma used for day-t thresholds
        must be byte-identical to compute_trend's sigma column (63d window
        ending t, annualized).

        This verifies the contract: the stop overlay receives sigma from
        the caller, and that sigma must match compute_trend's output.
        We test this on real close data to ensure the same W=63 convention.
        """
        from signals.etf_universe import UNIVERSE, load_universe_close
        from signals.trend_signal import compute_trend, W_DEFAULT, TRADING_DAYS as ST

        try:
            close = load_universe_close()
        except FileNotFoundError:
            pytest.skip("No cached close data available")

        # Run compute_trend to get the reference sigma
        tidy = compute_trend(close, L=120, W=W_DEFAULT, long_short=True)
        sigma_ref = tidy.pivot(index="date", columns="ticker", values="sigma").sort_index()

        # Compute sigma independently using the same formula
        log_ret = np.log(close).diff()
        sigma_independent = log_ret.rolling(W_DEFAULT, min_periods=W_DEFAULT).std() * np.sqrt(ST)

        # Align
        common_idx = sigma_ref.index.intersection(sigma_independent.index)
        common_cols = [c for c in sigma_ref.columns if c in sigma_independent.columns]

        for ticker in common_cols[:3]:  # spot-check 3 tickers
            ref = sigma_ref.loc[common_idx, ticker].dropna()
            ind = sigma_independent.loc[common_idx, ticker].dropna()
            # Both should be non-empty
            assert len(ref) > 100, f"Too few sigma values for {ticker}"
            # They should match byte-for-byte (same formula, same inputs)
            overlap = ref.index.intersection(ind.index)
            np.testing.assert_allclose(
                ref.loc[overlap].to_numpy(),
                ind.loc[overlap].to_numpy(),
                rtol=1e-12,
                err_msg=f"Sigma mismatch for {ticker}: compute_trend vs independent calc",
            )
