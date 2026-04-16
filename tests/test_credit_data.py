"""Coverage + integrity tests for credit_market_data.parquet."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

CREDIT_PATH = Path("data/raw/credit_market_data.parquet")
FEATURES_PATH = Path("data/processed/features.parquet")

FRED_NUMERIC_COLS = [
    "oas_hy", "oas_bbb", "oas_ig",
    "dgs1", "dgs2", "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30",
]
OAS_COLS = ["oas_hy", "oas_bbb", "oas_ig"]
DGS_COLS = ["dgs1", "dgs2", "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30"]
SYNTH_COLS = ["synth_cds_hy", "synth_cds_ig"]


def _credit() -> pd.DataFrame:
    assert CREDIT_PATH.exists(), "run signals.fred.build() first"
    return pd.read_parquet(CREDIT_PATH)


def test_fred_schema() -> None:
    df = _credit()
    assert df.index.name == "date"
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is None
    assert df.index.is_monotonic_increasing
    assert df.index.is_unique
    for c in FRED_NUMERIC_COLS + SYNTH_COLS:
        assert c in df.columns, f"missing {c}"
        assert df[c].dtype == np.float64, f"{c}: {df[c].dtype}"
    assert "ytw_source_hyg" in df.columns
    assert "ytw_source_lqd" in df.columns


def test_fred_coverage() -> None:
    """C9 — all listed series, coverage back to 1996-12-31,
    max business-day gap ≤ 10, OAS non-negative.
    """
    df = _credit()
    assert df.index.min() <= pd.Timestamp("1996-12-31")
    obs = df.index.values.astype("datetime64[D]")
    gaps = np.busday_count(obs[:-1], obs[1:])
    max_gap = int(gaps.max() - 1)
    assert max_gap <= 10, f"max business-day gap {max_gap}"
    for c in OAS_COLS:
        assert (df[c] >= 0).all(), f"{c}: negative values present"
    for c in DGS_COLS:
        assert df[c].notna().all()
        assert np.isfinite(df[c]).all()


def test_etf_oas_correlation() -> None:
    """C10 — |corr(hy_spread, oas_hy)| > 0.7 on the overlapping sample.

    Sign is negative by construction: hy_spread = ln(HYG/IEF) rises
    when credit is tight (HYG rallies, IEF retraces); oas_hy widens
    when credit is stressed. The economic claim is that the ETF proxy
    tracks the BAML cash-bond series, regardless of sign.
    """
    assert FEATURES_PATH.exists(), "run signals.pipeline.build() first"
    feats = pd.read_parquet(FEATURES_PATH)
    credit = _credit()
    joined = pd.concat(
        [feats["hy_spread"], credit["oas_hy"]], axis=1, join="inner"
    ).dropna()
    assert len(joined) > 2000, f"overlap too small: {len(joined)} rows"
    rho = joined.corr().iloc[0, 1]
    assert abs(rho) > 0.7, f"C10 fail: |corr(hy_spread, oas_hy)| = {abs(rho):.3f}"
    assert rho < 0, f"sign unexpected: rho = {rho:.3f} (should be negative)"


def test_synth_cds_sign() -> None:
    """Synthetic CDS must be mostly positive (credit premium is a spread)."""
    df = _credit()
    # Use the post-ETF era (HYG inception) for a clean comparison
    post = df.loc["2008-01-01":]
    for c in SYNTH_COLS:
        frac_pos = (post[c].dropna() > 0).mean()
        assert frac_pos > 0.85, f"{c}: only {frac_pos:.1%} positive after 2008"


def test_ytw_source_flags() -> None:
    df = _credit()
    assert df["ytw_source_hyg"].unique().tolist() == ["ttm_div_proxy"]
    assert df["ytw_source_lqd"].unique().tolist() == ["ttm_div_proxy"]
