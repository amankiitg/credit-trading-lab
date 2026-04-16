"""Generate validation plots for sprint v1.

Run as: venv/bin/python sprints/v1/make_plots.py

Produces every plot referenced by TASKS.md under sprints/v1/plots/.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from statsmodels.graphics.tsaplots import plot_acf
from statsmodels.tsa.stattools import adfuller

from signals.zscore import compute_zscores

RAW_DIR = Path("data/raw")
PROCESSED_PATH = Path("data/processed/features.parquet")
PLOTS_DIR = Path("sprints/v1/plots")
TICKERS = ["HYG", "LQD", "SPY", "IEF"]
SPREADS = ["hy_spread", "ig_spread", "hy_ig"]
Z_WINDOWS = [63, 126, 252]
WARMUP = 252


def ensure_dir() -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------- 01 coverage

def plot_raw_coverage() -> None:
    fig, ax = plt.subplots(figsize=(10, 3.5))
    for i, t in enumerate(TICKERS):
        df = pd.read_parquet(RAW_DIR / f"{t}.parquet")
        ax.scatter(df.index, np.full(len(df), i), s=0.4, label=t)
    ax.set_yticks(range(len(TICKERS)))
    ax.set_yticklabels(TICKERS)
    start = min(pd.read_parquet(RAW_DIR / f"{t}.parquet").index.min() for t in TICKERS)
    end = max(pd.read_parquet(RAW_DIR / f"{t}.parquet").index.max() for t in TICKERS)
    ax.set_title(f"Raw parquet coverage · {start.date()} → {end.date()} · 4 tickers")
    ax.set_xlabel("date")
    ax.set_ylabel("ticker")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "01_raw_coverage.png", dpi=120)
    plt.close(fig)


# ------------------------------------------------------------- 02 ACF, 03 dist

def plot_returns_stats(features: pd.DataFrame) -> pd.DataFrame:
    rows = []
    fig_acf, axes_acf = plt.subplots(2, 2, figsize=(10, 7), sharex=True)
    fig_dist, axes_dist = plt.subplots(2, 2, figsize=(10, 7))
    for ax_a, ax_d, t in zip(axes_acf.flat, axes_dist.flat, TICKERS):
        r = features[f"{t}_log_ret"].dropna()
        rows.append(
            {
                "ticker": t,
                "n": len(r),
                "mean": r.mean(),
                "std": r.std(),
                "skew": r.skew(),
                "kurt": r.kurt(),
                "ac1": r.autocorr(lag=1),
            }
        )
        plot_acf(r, lags=30, ax=ax_a, title=f"{t}  (ac1={r.autocorr(lag=1):+.3f})")
        ax_a.set_ylim(-0.2, 0.2)
        ax_d.hist(r, bins=120, density=True, alpha=0.8)
        ax_d.set_title(f"{t}  μ={r.mean():.4f}  σ={r.std():.4f}  kurt={r.kurt():.1f}")
        ax_d.set_xlabel("log return")
    start, end = features.index.min().date(), features.index.max().date()
    fig_acf.suptitle(f"Log-return ACF (lags 0–30) · {start} → {end}")
    fig_dist.suptitle(f"Log-return distribution · {start} → {end}")
    fig_acf.tight_layout()
    fig_dist.tight_layout()
    fig_acf.savefig(PLOTS_DIR / "02_returns_acf.png", dpi=120)
    fig_dist.savefig(PLOTS_DIR / "03_returns_dist.png", dpi=120)
    plt.close(fig_acf)
    plt.close(fig_dist)
    return pd.DataFrame(rows).set_index("ticker")


# ------------------------------------------------------------- 04 zscore dist, 05 rolling stats

def plot_zscore_stats(features: pd.DataFrame) -> pd.DataFrame:
    post = features.iloc[WARMUP:]
    rows = []

    fig_d, axes_d = plt.subplots(3, 3, figsize=(12, 10))
    fig_r, axes_r = plt.subplots(3, 3, figsize=(12, 10), sharex=True)
    for i, s in enumerate(SPREADS):
        for j, w in enumerate(Z_WINDOWS):
            col = f"{s}_z{w}"
            z = post[col].dropna()
            adf_p = adfuller(z, autolag="AIC")[1]
            rows.append(
                {
                    "column": col,
                    "n": len(z),
                    "mean": z.mean(),
                    "std": z.std(),
                    "kurt": z.kurt(),
                    "adf_p": adf_p,
                    "ac1": z.autocorr(lag=1),
                }
            )
            axd = axes_d[i, j]
            axd.hist(z, bins=80, density=True, alpha=0.8)
            axd.set_title(
                f"{col}  μ={z.mean():+.2f} σ={z.std():.2f}\n"
                f"kurt={z.kurt():.1f} ADF p={adf_p:.3f}"
            )
            axd.axvline(0, color="k", lw=0.5)
            axr = axes_r[i, j]
            roll_mu = post[col].rolling(252).mean()
            roll_sd = post[col].rolling(252).std()
            axr.plot(roll_mu, label="rolling μ (252d)")
            axr.plot(roll_sd, label="rolling σ (252d)")
            axr.axhline(0, color="k", lw=0.5)
            axr.axhline(1, color="gray", lw=0.5, ls="--")
            axr.set_title(col)
            axr.legend(fontsize=7)
    start, end = post.index.min().date(), post.index.max().date()
    fig_d.suptitle(f"Z-score distributions (post-warmup) · {start} → {end}")
    fig_r.suptitle(f"Z-score rolling mean/std (252d window) · {start} → {end}")
    fig_d.tight_layout()
    fig_r.tight_layout()
    fig_d.savefig(PLOTS_DIR / "04_zscore_dist.png", dpi=120)
    fig_r.savefig(PLOTS_DIR / "05_zscore_rolling_stats.png", dpi=120)
    plt.close(fig_d)
    plt.close(fig_r)
    return pd.DataFrame(rows).set_index("column")


# ------------------------------------------------------------- baseline

def baseline_shuffled_zscore(features: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    """Compute z-score stats on a shuffled spread — calibrates what random data looks like."""
    rng = np.random.default_rng(seed)
    shuffled = pd.DataFrame(index=features.index)
    for s in SPREADS:
        vals = features[s].dropna().values.copy()
        rng.shuffle(vals)
        series = pd.Series(np.nan, index=features.index)
        series.iloc[-len(vals) :] = vals
        shuffled[s] = series
    z = compute_zscores(shuffled, SPREADS, [252])
    post = z.iloc[WARMUP:]
    rows = []
    for c in z.columns:
        x = post[c].dropna()
        adf_p = adfuller(x, autolag="AIC")[1]
        rows.append(
            {
                "column": c,
                "mean": x.mean(),
                "std": x.std(),
                "kurt": x.kurt(),
                "adf_p": adf_p,
            }
        )
    return pd.DataFrame(rows).set_index("column")


def main() -> None:
    ensure_dir()
    plot_raw_coverage()
    print(f"wrote {PLOTS_DIR / '01_raw_coverage.png'}")
    if not PROCESSED_PATH.exists():
        print("features.parquet not built yet — skipping feature-level plots")
        return
    feats = pd.read_parquet(PROCESSED_PATH)
    ret_stats = plot_returns_stats(feats)
    print("\n--- returns stats ---")
    print(ret_stats.to_string(float_format=lambda x: f"{x:+.5f}"))
    z_stats = plot_zscore_stats(feats)
    print("\n--- z-score stats (post-warmup) ---")
    print(z_stats.to_string(float_format=lambda x: f"{x:+.4f}"))
    base = baseline_shuffled_zscore(feats)
    print("\n--- baseline z-scores (shuffled spreads) ---")
    print(base.to_string(float_format=lambda x: f"{x:+.4f}"))


if __name__ == "__main__":
    main()
