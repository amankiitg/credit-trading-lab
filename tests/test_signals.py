"""Integrity and invariant tests for the v1 data pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.features import compute_returns, compute_spreads, compute_vol
from signals.zscore import compute_zscores

RAW_DIR = Path("data/raw")
PROCESSED_PATH = Path("data/processed/features.parquet")
TICKERS = ["HYG", "LQD", "SPY", "IEF"]
RAW_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
WARMUP = 252


# ---------------------------------------------------------------- raw data

@pytest.mark.parametrize("ticker", TICKERS)
def test_raw_integrity(ticker: str) -> None:
    df = pd.read_parquet(RAW_DIR / f"{ticker}.parquet")
    assert df.index.name == "date"
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is None
    assert df.index.is_monotonic_increasing
    assert df.index.is_unique
    assert list(df.columns) == RAW_COLUMNS
    assert df["adj_close"].notna().all()
    assert (df["volume"] >= 0).all()
    assert df["volume"].dtype == np.int64

    # max consecutive missing business days
    obs = df.index.values.astype("datetime64[D]")
    gaps = np.busday_count(obs[:-1], obs[1:])  # 1 = consecutive
    assert gaps.max() - 1 <= 5, f"{ticker}: max gap {gaps.max() - 1} business days"


# ---------------------------------------------------------------- features

def _features() -> pd.DataFrame:
    assert PROCESSED_PATH.exists(), "run signals.pipeline.build() first"
    return pd.read_parquet(PROCESSED_PATH)


def test_features_schema() -> None:
    df = _features()
    assert df.index.name == "date"
    assert df.index.is_monotonic_increasing
    assert df.index.is_unique
    expected: list[str] = []
    for t in TICKERS:
        expected.append(f"{t}_adj_close")
        expected.append(f"{t}_log_ret")
        for w in (21, 63, 126):
            expected.append(f"{t}_vol_{w}")
    expected += ["hy_spread", "ig_spread", "hy_ig"]
    for s in ("hy_spread", "ig_spread", "hy_ig"):
        for w in (63, 126, 252):
            expected.append(f"{s}_z{w}")
    assert list(df.columns) == expected, (
        f"schema drift: missing={set(expected) - set(df.columns)} "
        f"extra={set(df.columns) - set(expected)}"
    )
    assert len(df.columns) == 32
    for c in df.columns:
        assert df[c].dtype == np.float64, f"{c}: {df[c].dtype}"


def test_features_no_nan_post_warmup() -> None:
    df = _features()
    post = df.iloc[WARMUP:]
    nan_counts = post.isna().sum()
    assert (nan_counts == 0).all(), f"NaNs after warmup: {nan_counts[nan_counts > 0].to_dict()}"


# ---------------------------------------------------------------- invariants

def test_spread_identity() -> None:
    df = pd.DataFrame(
        {
            "HYG_adj_close": [100.0, 101.0, 99.5],
            "IEF_adj_close": [90.0, 90.5, 91.0],
            "LQD_adj_close": [110.0, 110.8, 110.2],
            "SPY_adj_close": [400.0, 401.0, 399.0],
        }
    )
    s = compute_spreads(df)
    np.testing.assert_allclose(s["hy_ig"], s["hy_spread"] - s["ig_spread"], atol=1e-12)


def test_returns_no_leakage() -> None:
    rng = np.random.default_rng(0)
    prices = pd.DataFrame(
        {"HYG_adj_close": np.cumprod(1 + rng.normal(0, 0.01, 100)) * 100},
    )
    r1 = compute_returns(prices.copy())
    prices_tainted = prices.copy()
    prices_tainted.iloc[-1] = np.nan
    r2 = compute_returns(prices_tainted)
    # earlier rows must be identical
    pd.testing.assert_series_equal(r1["HYG_log_ret"].iloc[:-1], r2["HYG_log_ret"].iloc[:-1])


def test_zscore_no_leakage() -> None:
    rng = np.random.default_rng(0)
    x = pd.DataFrame({"s": rng.normal(0, 1, 500).cumsum()})
    z1 = compute_zscores(x.copy(), ["s"], [63])
    x_tainted = x.copy()
    x_tainted.iloc[-1] = np.nan
    z2 = compute_zscores(x_tainted, ["s"], [63])
    pd.testing.assert_series_equal(z1["s_z63"].iloc[:-1], z2["s_z63"].iloc[:-1])


def test_zscore_known_values() -> None:
    # synthetic constant-distribution series: z should oscillate around 0
    rng = np.random.default_rng(1)
    x = pd.DataFrame({"s": rng.normal(5.0, 2.0, 5000)})
    z = compute_zscores(x, ["s"], [252])
    post = z["s_z252"].dropna()
    assert abs(post.mean()) < 0.2
    assert 0.8 < post.std() < 1.2


def test_vol_is_annualized() -> None:
    # constant daily log return of 0.01 → vol should be ~0 (degenerate)
    # use a controlled random series instead
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"X_adj_close": np.cumprod(1 + rng.normal(0, 0.01, 1000)) * 100})
    with_ret = compute_returns(df)
    with_vol = compute_vol(with_ret, windows=[63])
    realized = with_vol["X_vol_63"].dropna()
    # daily std ~0.01 → annualized ~0.01*sqrt(252) ≈ 0.1587
    assert 0.12 < realized.mean() < 0.20
