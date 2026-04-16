"""Integrity and invariant tests for the v1 data pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.features import compute_returns, compute_spreads, compute_vol
from signals.flags import FLAG_NAMES, RV_STUB_COLUMNS, FlagThresholds, compute_flags
from signals.zscore import compute_zscores

RAW_DIR = Path("data/raw")
PROCESSED_PATH = Path("data/processed/features.parquet")
TICKERS = ["HYG", "LQD", "SPY", "IEF"]
SPREADS = ["hy_spread", "ig_spread", "hy_ig"]
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
    expected += list(SPREADS)
    for s in SPREADS:
        for w in (63, 126, 252):
            expected.append(f"{s}_z{w}")
    flag_cols: list[str] = []
    for s in SPREADS:
        for f in FLAG_NAMES:
            flag_cols.append(f"{s}_{f}")
    expected += flag_cols
    expected += list(RV_STUB_COLUMNS)
    assert list(df.columns) == expected, (
        f"schema drift: missing={set(expected) - set(df.columns)} "
        f"extra={set(df.columns) - set(expected)}"
    )
    assert len(df.columns) == 49  # 32 original + 12 flags + 5 RV stubs

    # Dtype discipline: numeric cols float64, flag cols bool
    for c in df.columns:
        if c in flag_cols:
            assert df[c].dtype == np.bool_, f"{c}: expected bool, got {df[c].dtype}"
        else:
            assert df[c].dtype == np.float64, f"{c}: {df[c].dtype}"


def test_features_no_nan_post_warmup() -> None:
    """Non-stub, non-flag numeric columns must have no NaN after warmup."""
    df = _features()
    post = df.iloc[WARMUP:]
    flag_cols = [f"{s}_{f}" for s in SPREADS for f in FLAG_NAMES]
    stub_cols = list(RV_STUB_COLUMNS)
    numeric = post.drop(columns=flag_cols + stub_cols)
    nan_counts = numeric.isna().sum()
    assert (nan_counts == 0).all(), f"NaNs after warmup: {nan_counts[nan_counts > 0].to_dict()}"


def test_flags_no_nan_and_bool_dtype() -> None:
    """Flags must be bool and non-NaN across the *entire* frame (warmup included)."""
    df = _features()
    for s in SPREADS:
        for f in FLAG_NAMES:
            col = f"{s}_{f}"
            assert df[col].dtype == np.bool_, f"{col}: {df[col].dtype}"
            assert df[col].notna().all(), f"{col} has NaN"


def test_rv_stubs_are_all_nan() -> None:
    """RV stubs must exist, be float64, and be entirely NaN (Phase 3 populates)."""
    df = _features()
    for c in RV_STUB_COLUMNS:
        assert c in df.columns, f"missing RV stub: {c}"
        assert df[c].dtype == np.float64, f"{c}: {df[c].dtype}"
        assert df[c].isna().all(), f"{c}: stub should be all-NaN in Phase 1"


def test_flag_threshold_semantics() -> None:
    """entry_long fires below -entry; entry_short above +entry; exit inside ±exit; stop outside ±stop."""
    idx = pd.date_range("2020-01-01", periods=7, freq="B")
    z = pd.Series([-5.0, -2.5, -1.0, 0.0, 1.0, 2.5, 5.0], index=idx, name="hy_spread_z63")
    df = pd.DataFrame({"hy_spread_z63": z})
    flags = compute_flags(df, ["hy_spread"], window=63)
    # entry=2, exit=0.5, stop=4
    np.testing.assert_array_equal(
        flags["hy_spread_entry_long"].to_numpy(),
        [True, True, False, False, False, False, False],
    )
    np.testing.assert_array_equal(
        flags["hy_spread_entry_short"].to_numpy(),
        [False, False, False, False, False, True, True],
    )
    np.testing.assert_array_equal(
        flags["hy_spread_exit"].to_numpy(),
        [False, False, False, True, False, False, False],
    )
    np.testing.assert_array_equal(
        flags["hy_spread_stop"].to_numpy(),
        [True, False, False, False, False, False, True],
    )


def test_flags_handle_nan_z_score() -> None:
    """Rows with NaN z-score must produce False flags, never NaN."""
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    z = pd.Series([np.nan, np.nan, 2.5, -2.5, 0.0], index=idx, name="hy_spread_z63")
    df = pd.DataFrame({"hy_spread_z63": z})
    flags = compute_flags(df, ["hy_spread"], window=63)
    assert flags.notna().all().all()
    assert flags.dtypes.eq(np.bool_).all()
    # First two rows (NaN z) must be all-False
    assert not flags.iloc[:2].any().any()


def test_flag_thresholds_reject_bad_config() -> None:
    import pytest as _pt

    df = pd.DataFrame({"hy_spread_z63": [0.0]})
    with _pt.raises(ValueError):
        compute_flags(df, ["hy_spread"], thresholds=FlagThresholds(entry=1.0, exit=1.0))
    with _pt.raises(ValueError):
        compute_flags(df, ["hy_spread"], thresholds=FlagThresholds(entry=2.0, stop=2.0))


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
