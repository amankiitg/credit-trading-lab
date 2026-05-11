"""D9 — sanity baseline: total HIGH-conviction (date × signal) count
must sit in [50, 1500] over the full features.parquet; and no single
signal may contribute > 70% of the total.

Too few = thresholds miscalibrated (nothing ever fires). Too many =
the thesis test is degenerate (everything fires).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dashboard.conviction import conviction
from dashboard.signal_specs import CARD_SPECS

PROCESSED = Path("data/processed/features.parquet")


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED)


def _per_signal_high_counts(df: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in CARD_SPECS:
        z = df[spec.z_col]
        r = df[spec.regime_col].astype(str)
        mask = pd.Series(False, index=df.index)
        for i in range(len(df)):
            zi = z.iloc[i]
            ri = r.iloc[i] if isinstance(r.iloc[i], str) else None
            mask.iloc[i] = conviction(zi, ri) == "HIGH"
        counts[spec.name] = int(mask.sum())
    return counts


def test_total_high_in_band(features: pd.DataFrame) -> None:
    counts = _per_signal_high_counts(features)
    total = sum(counts.values())
    assert 50 <= total <= 1500, (
        f"total HIGH (date × signal) count = {total} (target [50, 1500]); "
        f"per-signal = {counts}"
    )


def test_no_single_signal_dominates(features: pd.DataFrame) -> None:
    counts = _per_signal_high_counts(features)
    total = sum(counts.values())
    if total == 0:
        pytest.skip("no HIGH cells, dominance check moot")
    worst = max(counts.values()) / total
    assert worst <= 0.70, (
        f"single signal contributes {worst:.2%} of HIGH count "
        f"(>70% suggests a regime/z bug on that signal). counts={counts}"
    )
