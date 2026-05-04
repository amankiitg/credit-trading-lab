"""Regime classifier tests — C18 coverage + non-degeneracy."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from signals.regimes import equity_credit_lag, equity_regime, vol_regime

PROCESSED_PATH = Path("data/processed/features.parquet")
WARMUP = 252


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_PATH)


@pytest.fixture(scope="module")
def post_warmup(features: pd.DataFrame) -> pd.Index:
    return features.index[WARMUP:]


# ------------------------------------------------------------------- C18 (a): coverage


@pytest.mark.parametrize(
    "fn,name",
    [
        (vol_regime, "vol_regime"),
        (equity_regime, "equity_regime"),
        (lambda df: equity_credit_lag(df), "equity_credit_lag"),
    ],
)
def test_c18_coverage_post_warmup(features, post_warmup, fn, name) -> None:
    s = fn(features).loc[post_warmup]
    coverage = s.notna().mean()
    assert coverage > 0.95, f"{name}: coverage={coverage:.3f} (<0.95)"


# ------------------------------------------------------------------- C18 (b): non-degeneracy


@pytest.mark.parametrize(
    "fn,name",
    [
        (vol_regime, "vol_regime"),
        (equity_regime, "equity_regime"),
        (lambda df: equity_credit_lag(df), "equity_credit_lag"),
    ],
)
def test_c18_non_degenerate(features, post_warmup, fn, name) -> None:
    s = fn(features).loc[post_warmup].dropna()
    top_share = s.value_counts(normalize=True).max()
    assert top_share <= 0.70, f"{name}: top label share={top_share:.3f} (>0.70)"


# ------------------------------------------------------------------- C18 (c): expected labels


def test_c18_vol_regime_labels(features, post_warmup) -> None:
    s = vol_regime(features).loc[post_warmup].dropna()
    assert set(s.unique()) == {"high", "low"}


def test_c18_equity_regime_labels(features, post_warmup) -> None:
    s = equity_regime(features).loc[post_warmup].dropna()
    assert set(s.unique()) == {"bull", "bear"}


def test_c18_equity_credit_lag_labels(features, post_warmup) -> None:
    s = equity_credit_lag(features).loc[post_warmup].dropna()
    assert set(s.unique()) == {"equity_first", "credit_first", "neither"}


# ------------------------------------------------------------------- C18 (d): noise floor live


def test_c18_equity_credit_lag_noise_floor_active(features, post_warmup) -> None:
    s = equity_credit_lag(features, noise_floor=0.15).loc[post_warmup].dropna()
    neither_share = (s == "neither").mean()
    assert neither_share >= 0.05, (
        f"neither share={neither_share:.3f} (<0.05 — noise floor not engaging)"
    )
