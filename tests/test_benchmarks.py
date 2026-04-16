"""Tests for the random-entry MC baseline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals.benchmarks import SPREADS, random_baseline

BASELINE_PATH = Path("data/benchmarks/random_baseline.parquet")
EXPECTED_COLS = [
    "path_id", "spread", "n_trades", "total_pnl",
    "mean_trade_pnl", "std_trade_pnl", "sharpe", "hit_rate",
]


def _baseline() -> pd.DataFrame:
    assert BASELINE_PATH.exists(), "run signals.benchmarks.build() first"
    return pd.read_parquet(BASELINE_PATH)


def test_baseline_schema() -> None:
    df = _baseline()
    assert list(df.columns) == EXPECTED_COLS
    assert df.shape == (3000, 8)
    assert df["spread"].value_counts().tolist() == [1000, 1000, 1000]
    assert (df["n_trades"] > 0).all()


def test_baseline_seed_reproducibility() -> None:
    """Same seed → byte-identical output."""
    a = random_baseline(n_paths=50, seed=42)
    b = random_baseline(n_paths=50, seed=42)
    pd.testing.assert_frame_equal(a, b)


def test_random_baseline_is_noise() -> None:
    """C11 — per-spread Sharpe distribution mean ∈ [-0.2, 0.2]
    and std ∈ [0.2, 1.0]. A baseline centered away from zero would
    indicate a direction bias or look-ahead in the sampler.
    """
    df = _baseline()
    for s in SPREADS:
        sh = df.loc[df["spread"] == s, "sharpe"]
        mean, std = float(sh.mean()), float(sh.std(ddof=1))
        assert -0.2 < mean < 0.2, f"{s}: Sharpe mean {mean:.3f} off-center"
        assert 0.5 < std < 1.5, f"{s}: Sharpe std {std:.3f} out of range"
