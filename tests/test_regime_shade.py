"""D6 — regime_shade.spans correctness on a hand-crafted series."""

from __future__ import annotations

import pandas as pd
import pytest

from dashboard.components.regime_shade import spans


def _toy_df(labels: list[str | None]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(labels), freq="B")
    return pd.DataFrame({"vol_regime": labels}, index=idx)


def test_contiguous_runs_emitted_in_order() -> None:
    df = _toy_df(["low", "low", "low", "high", "high", "low", "low"])
    out = spans(df, "vol_regime")
    labels = [s[2] for s in out]
    assert labels == ["low", "high", "low"]
    # First run covers days 0..2
    assert out[0][0] == df.index[0]
    assert out[0][1] == df.index[2]
    # Second run covers days 3..4
    assert out[1][0] == df.index[3]
    assert out[1][1] == df.index[4]
    # Third run covers days 5..6
    assert out[2][0] == df.index[5]
    assert out[2][1] == df.index[6]


def test_nan_labels_skipped() -> None:
    df = _toy_df(["low", None, "high", "high"])
    out = spans(df, "vol_regime")
    labels = [s[2] for s in out]
    assert labels == ["low", "high"]


def test_no_overlapping_or_gapped_ranges() -> None:
    df = _toy_df(["low", "low", "high", "high", "high", "low"])
    out = spans(df, "vol_regime")
    # End of run i is immediately before start of run i+1 (since runs cover contiguous indices)
    for prev, nxt in zip(out, out[1:]):
        prev_end_pos = df.index.get_loc(prev[1])
        nxt_start_pos = df.index.get_loc(nxt[0])
        assert nxt_start_pos == prev_end_pos + 1


def test_known_palette_applied() -> None:
    df = _toy_df(["low", "high"])
    out = spans(df, "vol_regime")
    assert "rgba(90, 130, 255" in out[0][3]  # low → blueish
    assert "rgba(255, 90, 90" in out[1][3]   # high → reddish


def test_unknown_column_raises() -> None:
    df = _toy_df(["low"])
    with pytest.raises(KeyError):
        spans(df, "nonexistent_column")
