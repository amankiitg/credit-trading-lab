"""End-to-end build of ``data/processed/features.parquet``.

Sole I/O boundary for processed outputs. Orchestrates
``load → features → zscore`` and writes the 32-column feature frame
specified in PRD §Parquet Schemas.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from signals.features import compute_returns, compute_spreads, compute_vol
from signals.flags import (
    DEFAULT_FLAG_WINDOW,
    FLAG_NAMES,
    RV_STUB_COLUMNS,
    compute_flags,
    rv_stubs,
)
from signals.zscore import compute_zscores

RAW_DIR: Path = Path("data/raw")
PROCESSED_PATH: Path = Path("data/processed/features.parquet")
TICKERS: list[str] = ["HYG", "LQD", "SPY", "IEF"]
VOL_WINDOWS: list[int] = [21, 63, 126]
Z_WINDOWS: list[int] = [63, 126, 252]
SPREADS: list[str] = ["hy_spread", "ig_spread", "hy_ig"]


def _merge_adj_close(raw_dir: Path) -> pd.DataFrame:
    """Inner-join adjusted close across tickers on the business-day index."""
    frames = []
    for t in TICKERS:
        r = pd.read_parquet(raw_dir / f"{t}.parquet")
        print(f"  [load] {t}: {len(r)} rows {r.index.min().date()} → {r.index.max().date()}")
        frames.append(r[["adj_close"]].rename(columns={"adj_close": f"{t}_adj_close"}))
    merged = pd.concat(frames, axis=1, join="inner")
    print(f"  [merge] inner join: {len(merged)} rows, {len(merged.columns)} cols")
    return merged


def build(
    raw_dir: Path = RAW_DIR,
    out_path: Path = PROCESSED_PATH,
) -> pd.DataFrame:
    """Read raw parquet files, compute features + spreads + z-scores,
    write ``features.parquet``, and return the frame.
    """
    print("[pipeline] build()")
    merged = _merge_adj_close(raw_dir)

    feats = compute_returns(merged)
    feats = compute_vol(feats, VOL_WINDOWS)
    print(f"  [features] +returns+vol: {len(feats.columns)} cols")

    spreads = compute_spreads(merged)
    for c in spreads.columns:
        feats[c] = spreads[c]
    print(f"  [features] +spreads: {len(feats.columns)} cols")

    zs = compute_zscores(spreads, SPREADS, Z_WINDOWS)
    for c in zs.columns:
        feats[c] = zs[c]
    print(f"  [features] +zscores: {len(feats.columns)} cols")

    # Buy-and-hold HYG benchmark: cumulative log return from inception.
    # First row is 0.0 by construction (log_ret[0] is NaN → fillna → 0).
    feats["HYG_buyhold_cum_log_ret"] = feats["HYG_log_ret"].fillna(0.0).cumsum()
    print(f"  [features] +buyhold: {len(feats.columns)} cols")

    flags = compute_flags(feats, SPREADS, window=DEFAULT_FLAG_WINDOW)
    for c in flags.columns:
        feats[c] = flags[c]
    print(f"  [features] +flags: {len(feats.columns)} cols")

    stubs = rv_stubs(feats.index)
    for c in stubs.columns:
        feats[c] = stubs[c]
    print(f"  [features] +rv_stubs: {len(feats.columns)} cols")

    ordered: list[str] = []
    for t in TICKERS:
        ordered.append(f"{t}_adj_close")
        ordered.append(f"{t}_log_ret")
        for w in VOL_WINDOWS:
            ordered.append(f"{t}_vol_{w}")
    ordered += SPREADS
    for s in SPREADS:
        for w in Z_WINDOWS:
            ordered.append(f"{s}_z{w}")
    ordered.append("HYG_buyhold_cum_log_ret")
    for s in SPREADS:
        for f in FLAG_NAMES:
            ordered.append(f"{s}_{f}")
    ordered += list(RV_STUB_COLUMNS)
    feats = feats[ordered]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out_path)
    print(f"  [write] {out_path}: {feats.shape}")
    return feats


if __name__ == "__main__":
    build()
