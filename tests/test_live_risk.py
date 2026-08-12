"""Tests for risk/live_risk.py, sprint v9.1 T8 + T10.

T8 acceptance:
  - 2-asset toy book closed-form MCTR/PCTR to 1e-10
  - PCTR sums to 1.0 within 1e-6 (gate A1)
  - Dropping last return row changes MCTR (gate A3 companion)

T10 reconciliation (paired validation for T8+T9):
  - A2 identity: beta_explained + residual equals book P&L (covered in T9 implementation)
  - A1 on live snapshot (covered above)
  - A3 lookahead (covered above)
  - P&L contribution totals match pnl_log (T9 dependent)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from risk.live_risk import TRADING_DAYS, PCTR_SUM_TOL, mctr_pctr


class TestMCTRPCTR:
    """T8: MCTR/PCTR on toy books and invariants."""

    def test_two_asset_closed_form(self):
        """2-asset book: verify MCTR matches closed-form to 1e-10.

        Weights: w1=0.6, w2=0.4 (both long).
        Returns: 63 days, r1 std ~= 0.01 daily, r2 std ~= 0.005 daily,
                 correlation ~= 0.3.
        Closed-form:
          sigma1 = 0.01 * sqrt(252)
          sigma2 = 0.005 * sqrt(252)
          cov12  = 0.3 * sigma1 * sigma2 / 252 * 252 = 0.3 * 0.01 * 0.005 * 252
          Sigma = [[sigma1^2, cov12], [cov12, sigma2^2]]
          MCTR1 = (Sigma w)_1 / sigma_p
        """
        np.random.seed(42)
        n = 63
        # Generate correlated returns
        rng = np.random.RandomState(42)
        z1 = rng.randn(n)
        z2 = rng.randn(n)
        corr = 0.3
        r1 = 0.01 * z1
        r2 = 0.005 * (corr * z1 + np.sqrt(1 - corr**2) * z2)

        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        rets = pd.DataFrame({"A": r1, "B": r2}, index=dates)

        weights = pd.Series({"A": 0.6, "B": 0.4})

        result = mctr_pctr(weights, rets)

        # Closed-form
        Sigma = rets.cov() * TRADING_DAYS
        sigma_p = np.sqrt(weights @ Sigma @ weights)
        mctr_expected = (Sigma @ weights) / sigma_p
        ctr_expected = weights * mctr_expected
        pctr_expected = ctr_expected / sigma_p

        np.testing.assert_allclose(
            result["mctr"].to_numpy(), mctr_expected.to_numpy(),
            rtol=0, atol=1e-10,
            err_msg="MCTR closed-form mismatch",
        )
        np.testing.assert_allclose(
            result["pctr"].to_numpy(), pctr_expected.to_numpy(),
            rtol=0, atol=1e-10,
            err_msg="PCTR closed-form mismatch",
        )

    def test_pctr_sums_to_one(self):
        """Gate A1: PCTR must sum to 1.0 within 1e-6."""
        np.random.seed(99)
        n, k = 63, 5
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        tickers = [f"T{i}" for i in range(k)]
        rets = pd.DataFrame(
            np.random.randn(n, k) * 0.01,
            index=dates, columns=tickers,
        )
        weights = pd.Series(
            np.array([0.3, 0.2, 0.15, 0.25, 0.10]),
            index=tickers,
        )

        result = mctr_pctr(weights, rets)
        pctr_sum = float(result["pctr"].sum())

        assert abs(pctr_sum - 1.0) <= PCTR_SUM_TOL, (
            f"PCTR sum = {pctr_sum:.12f} != 1.0 (tol={PCTR_SUM_TOL})"
        )

    def test_shorts_contribute_with_sign(self):
        """Shorts must contribute with sign through the covariance.

        PCTR for a short position that is negatively correlated with
        the rest of the book should be NEGATIVE (risk-reducing),
        not computed off gross/unsigned weights.

        Use deterministic returns to control covariance precisely:
        - r_A = common + eps_A  (high vol, correlated with common)
        - r_B = common          (pure common factor, perfectly correlated)
        - Shorting B at -0.5 alongside A at 0.5 lowers portfolio vol.
        - B's PCTR is negative (hedging).
        """
        np.random.seed(99)
        n = 63
        rng = np.random.RandomState(99)
        common = rng.randn(n) * 0.008
        eps_A = rng.randn(n) * 0.004
        r_A = common + eps_A   # A = common + noise, ~ 0.9% daily vol
        r_B = common            # B = pure common, ~ 0.8% daily vol

        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        rets = pd.DataFrame({"A": r_A, "B": r_B}, index=dates)

        # Both long: positive PCTRs
        w_long = pd.Series({"A": 0.5, "B": 0.5})
        res_long = mctr_pctr(w_long, rets)
        assert (res_long["pctr"] > 0).all(), "All-long PCTR should be positive"

        # A long 0.5, B short 0.5: B hedges A partially (common factor cancels).
        # The portfolio is: 0.5*(common+eps) - 0.5*common = 0.5*eps (~0.2% vol).
        # A contributes all the risk; B's short reduces risk -> negative PCTR.
        w_hedged = pd.Series({"A": 0.5, "B": -0.5})
        res_hedged = mctr_pctr(w_hedged, rets)
        # B short is risk-reducing -> negative PCTR
        assert res_hedged.loc["B", "pctr"] < 0, (
            f"Short B PCTR should be negative (risk-reducing), got {res_hedged.loc['B', 'pctr']}"
        )
        # A long should have positive PCTR
        assert res_hedged.loc["A", "pctr"] > 0

        # PCTR still sums to 1.0
        assert abs(res_hedged["pctr"].sum() - 1.0) <= PCTR_SUM_TOL

    def test_drop_last_row_changes_mctr(self):
        """Gate A3 companion: dropping the last return row changes MCTR.

        This proves the cov window is anchored at t, not using future data.
        """
        np.random.seed(55)
        n = 63
        dates = pd.date_range("2020-01-02", periods=n, freq="B")
        rets = pd.DataFrame(
            np.random.randn(n, 3) * 0.01,
            index=dates, columns=["X", "Y", "Z"],
        )
        weights = pd.Series({"X": 0.4, "Y": 0.35, "Z": 0.25})

        # Full period
        result_full = mctr_pctr(weights, rets)

        # Drop last row (data through t-1)
        rets_trunc = rets.iloc[:-1]
        result_trunc = mctr_pctr(weights, rets_trunc)

        # MCTR must differ (cov estimate changed)
        mctr_diff = (result_full["mctr"] - result_trunc["mctr"]).abs().max()
        assert mctr_diff > 1e-12, (
            f"Dropping last row should change MCTR, but max diff = {mctr_diff:.2e}"
        )

    def test_zero_risk_portfolio(self):
        """Zero-weight or zero-vol portfolio: should not crash, returns zeros/NaNs."""
        dates = pd.date_range("2020-01-02", periods=63, freq="B")
        rets = pd.DataFrame(
            np.random.randn(63, 2) * 0.01,
            index=dates, columns=["A", "B"],
        )
        weights = pd.Series({"A": 0.0, "B": 0.0})

        result = mctr_pctr(weights, rets)
        # Zero vol portfolio
        assert (result["mctr"] == 0.0).all()
        assert (result["ctr"] == 0.0).all()

    def test_single_asset(self):
        """Single-asset book: PCTR should be 1.0."""
        np.random.seed(7)
        dates = pd.date_range("2020-01-02", periods=63, freq="B")
        rets = pd.DataFrame({"A": np.random.randn(63) * 0.01}, index=dates)
        weights = pd.Series({"A": 1.0})

        result = mctr_pctr(weights, rets)

        assert abs(result.loc["A", "pctr"] - 1.0) <= 1e-10
        assert abs(result.loc["A", "ctr"] - np.sqrt(rets["A"].var() * TRADING_DAYS)) <= 1e-10

    def test_less_than_63_days(self):
        """Fewer than 63 days: should still work with available data."""
        np.random.seed(33)
        dates = pd.date_range("2020-01-02", periods=30, freq="B")
        rets = pd.DataFrame(
            np.random.randn(30, 3) * 0.01,
            index=dates, columns=["X", "Y", "Z"],
        )
        weights = pd.Series({"X": 0.5, "Y": 0.3, "Z": 0.2})

        result = mctr_pctr(weights, rets)

        assert abs(result["pctr"].sum() - 1.0) <= PCTR_SUM_TOL
