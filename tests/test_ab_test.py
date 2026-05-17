"""W5 — A/B bootstrap of the incremental Sharpe."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.ab_test import _stationary_indices, block_bootstrap_delta_sharpe


def _series(values) -> pd.Series:
    idx = pd.date_range("2010-01-01", periods=len(values), freq="B")
    return pd.Series(np.asarray(values, dtype="float64"), index=idx)


# ----------------------------------------------------- stationary indices


def test_stationary_indices_in_range_and_full_length() -> None:
    rng = np.random.default_rng(0)
    idx = _stationary_indices(500, 21, rng)
    assert len(idx) == 500
    assert idx.min() >= 0 and idx.max() < 500


def test_stationary_indices_seed_reproducible() -> None:
    i1 = _stationary_indices(300, 21, np.random.default_rng(42))
    i2 = _stationary_indices(300, 21, np.random.default_rng(42))
    assert np.array_equal(i1, i2)


# ----------------------------------------------------- bootstrap ΔS


def test_bootstrap_positive_when_b_dominates() -> None:
    rng = np.random.default_rng(1)
    a = _series(rng.normal(0.0, 100.0, 1500))
    b = _series(a.to_numpy() + rng.normal(40.0, 20.0, 1500))  # B has clear drift
    out = block_bootstrap_delta_sharpe(a, b, n=500, block=21, seed=20260516)
    assert out["delta_sharpe"] > 0
    assert out["ci_lo"] > 0          # CI excludes zero
    assert out["frac_positive"] > 0.95


def test_bootstrap_negative_when_a_dominates() -> None:
    rng = np.random.default_rng(2)
    b = _series(rng.normal(0.0, 100.0, 1500))
    a = _series(b.to_numpy() + rng.normal(40.0, 20.0, 1500))
    out = block_bootstrap_delta_sharpe(a, b, n=500, block=21, seed=20260516)
    assert out["delta_sharpe"] < 0
    assert out["ci_hi"] < 0
    assert out["frac_positive"] < 0.05


def test_bootstrap_seed_reproducible() -> None:
    rng = np.random.default_rng(3)
    a = _series(rng.normal(0, 100, 800))
    b = _series(rng.normal(5, 100, 800))
    o1 = block_bootstrap_delta_sharpe(a, b, n=300, block=21, seed=20260516)
    o2 = block_bootstrap_delta_sharpe(a, b, n=300, block=21, seed=20260516)
    assert o1 == o2


def test_bootstrap_ci_ordering() -> None:
    rng = np.random.default_rng(4)
    a = _series(rng.normal(0, 100, 1000))
    b = _series(rng.normal(2, 100, 1000))
    out = block_bootstrap_delta_sharpe(a, b, n=400, block=21, seed=1)
    assert out["ci_lo"] <= out["ci_hi"]
    assert 0.0 <= out["frac_positive"] <= 1.0


def test_bootstrap_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        block_bootstrap_delta_sharpe(_series([1, 2, 3]), _series([1, 2]), n=10)
