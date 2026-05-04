"""C22-C24 — equity-credit lag thesis, hedge stability, schema.

C22: RV1 (rv_hy_ig) half-life under equity_first regime is >20%
     shorter than under neither regime, on the best hedge method.
C23: Rolling 63-day hedge-ratio CV < 1.0 for each (pair × method).
C24: data/results/regime_signal_quality.parquet exists with the
     correct schema and ≥27 rows.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

PROCESSED_PATH = Path("data/processed/features.parquet")
RAW_CREDIT_PATH = Path("data/raw/credit_market_data.parquet")
QUALITY_PATH = Path("data/results/regime_signal_quality.parquet")
WARMUP = 252


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_PATH)


@pytest.fixture(scope="module")
def credit_data() -> pd.DataFrame:
    return pd.read_parquet(RAW_CREDIT_PATH)


@pytest.fixture(scope="module")
def quality_table() -> pd.DataFrame:
    return pd.read_parquet(QUALITY_PATH)


@pytest.fixture(scope="module")
def all_results(features, credit_data):
    import pycredit

    from signals.rv_signals import build_all_residuals, select_best_method

    res = build_all_residuals(features, credit_data, pycredit)
    best = select_best_method(res, warmup=WARMUP)
    return res, best


# ---------------------------------------------------------------- C24


def test_c24_quality_table_exists(quality_table: pd.DataFrame) -> None:
    """Parquet exists, ≥27 rows, schema matches PRD §Data Outputs."""
    expected_cols = {
        "signal",
        "hedge_method",
        "regime_classifier",
        "regime_label",
        "half_life",
        "z_magnitude",
        "signal_freq",
        "n_obs",
        "adf_pvalue",
    }
    assert expected_cols == set(quality_table.columns), (
        f"missing={expected_cols - set(quality_table.columns)} "
        f"extra={set(quality_table.columns) - expected_cols}"
    )
    assert len(quality_table) >= 27, f"only {len(quality_table)} rows (<27)"


# ---------------------------------------------------------------- C22


def test_c22_equity_credit_lag_thesis(quality_table: pd.DataFrame, all_results) -> None:
    """RV1 half-life on equity_first is >20% shorter than on neither
    under the best hedge method (selected by ADF p-value)."""
    _, best = all_results
    best_method = best["rv_hy_ig"][0]

    sub = quality_table[
        (quality_table.signal == "rv_hy_ig")
        & (quality_table.regime_classifier == "equity_credit_lag")
        & (quality_table.hedge_method == best_method)
    ]
    ef = sub[sub.regime_label == "equity_first"]["half_life"].iloc[0]
    nt = sub[sub.regime_label == "neither"]["half_life"].iloc[0]
    ratio = ef / nt
    assert ratio < 0.80, (
        f"equity_first hl={ef:.3f}, neither hl={nt:.3f}, ratio={ratio:.3f} "
        f"(>=0.80 — thesis: equity_first should be >20% shorter)"
    )


# ---------------------------------------------------------------- C23


def test_c23_hedge_ratio_cv_under_one(all_results) -> None:
    """Rolling 63-day hedge-ratio CV (std/|mean|) must stay < 1.0
    for every (pair × method × window) post-warmup."""
    from signals.rv_signals import hedge_ratio_cv

    results, _ = all_results
    failures = []
    for pair, methods in results.items():
        for method, (_, hr) in methods.items():
            cv = hedge_ratio_cv(hr).iloc[WARMUP:].dropna()
            mx = float(cv.max()) if len(cv) else 0.0
            if mx >= 1.0:
                failures.append((pair, method, mx))
    assert not failures, "CV>=1.0 found: " + ", ".join(
        f"{p}/{m}: max CV={c:.2f}" for p, m, c in failures
    )
