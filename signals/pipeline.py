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


RAW_CREDIT_PATH: Path = Path("data/raw/credit_market_data.parquet")
WARMUP: int = 252
Z_RV_WINDOW: int = 63

REGIME_COLS: tuple[str, ...] = ("vol_regime", "equity_regime", "equity_credit_lag")
Z_RV_COLS: tuple[str, ...] = ("z_rv_hy_ig", "z_rv_credit_rates", "z_rv_xterm")

# stub → z_rv naming
_PAIR_TO_STUB = {
    "rv_hy_ig": ("rv_hy_ig_residual", "hedge_ratio_hy_ig", "z_rv_hy_ig"),
    "rv_credit_rates": ("rv_credit_rates_residual", "hedge_ratio_cr", "z_rv_credit_rates"),
    "rv_xterm": ("rv_xterm_residual", None, "z_rv_xterm"),
}


def enrich_with_rv(
    feats: pd.DataFrame,
    credit_data_path: Path = RAW_CREDIT_PATH,
    out_path: Path = PROCESSED_PATH,
) -> pd.DataFrame:
    """Populate the 5 RV stub columns with the best-method residuals,
    add 3 regime label columns and 3 z_rv columns. Writes the
    56-column feature frame and returns it.
    """
    print("[pipeline] enrich_with_rv()")

    import pycredit  # noqa: F401  - imported lazily; required for V5 sweep

    from signals.regimes import equity_credit_lag, equity_regime, vol_regime
    from signals.rv_signals import (
        build_all_residuals,
        select_best_method,
        trailing_zscore,
    )

    cmd = pd.read_parquet(credit_data_path)

    # ---- regimes
    feats["vol_regime"] = pd.Categorical(vol_regime(feats), categories=["low", "high"])
    feats["equity_regime"] = pd.Categorical(equity_regime(feats), categories=["bear", "bull"])
    feats["equity_credit_lag"] = pd.Categorical(
        equity_credit_lag(feats),
        categories=["credit_first", "neither", "equity_first"],
    )
    print(f"  [enrich] +regimes: {feats.shape[1]} cols")

    # ---- 9 residuals + best-method selection
    results = build_all_residuals(feats, cmd, pycredit)
    best = select_best_method(results, warmup=WARMUP)
    for pair, (m, scores) in best.items():
        adf_str = " ".join(f"{k}={v:.3f}" for k, v in scores.items())
        print(f"  [select] {pair}: best={m}  (ADF p: {adf_str})")

    # ---- write best-method residuals + hedge ratios + z_rv
    for pair, (best_method, _) in best.items():
        resid, hr = results[pair][best_method]
        resid_col, hr_col, z_col = _PAIR_TO_STUB[pair]
        feats[resid_col] = resid
        if hr_col is not None:
            feats[hr_col] = hr
        feats[z_col] = trailing_zscore(resid, window=Z_RV_WINDOW)
    print(f"  [enrich] +rv populated + z_rv: {feats.shape[1]} cols")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out_path)
    print(f"  [write] {out_path}: {feats.shape}")
    return feats


def build_with_rv(
    raw_dir: Path = RAW_DIR,
    credit_data_path: Path = RAW_CREDIT_PATH,
    out_path: Path = PROCESSED_PATH,
) -> pd.DataFrame:
    feats = build(raw_dir=raw_dir, out_path=out_path)
    return enrich_with_rv(feats, credit_data_path=credit_data_path, out_path=out_path)


if __name__ == "__main__":
    build_with_rv()
