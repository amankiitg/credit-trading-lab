"""Random-entry Monte Carlo baseline.

For each of the three spreads, we extract the empirical holding-length
distribution from v1's signal-state flags (each entry→exit run gives
one length sample). Then for ``n_paths`` paths we simulate:

    for each trade:
        length    ~ empirical sample with replacement
        entry_ix  ~ uniform without replacement over eligible dates
        direction ~ uniform {+1, -1}
        pnl       = direction * (spread[entry+length] - spread[entry])

This is the calibration distribution for "random skill" — any v2
trading signal must beat it to be considered better than chance.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from signals.flags import DEFAULT_FLAG_WINDOW, FLAG_NAMES

FEATURES_PATH: Path = Path("data/processed/features.parquet")
OUT_PATH: Path = Path("data/benchmarks/random_baseline.parquet")
SPREADS: list[str] = ["hy_spread", "ig_spread", "hy_ig"]
WARMUP: int = 252


def _holding_lengths(flag_df: pd.DataFrame, spread: str) -> np.ndarray:
    """Return an array of holding-period lengths (in business days),
    one per entry→exit run inferred from the flags.

    A "run" starts on an ``entry_long`` or ``entry_short`` flag and
    ends on the next ``exit`` flag strictly after it.
    """
    entry = (flag_df[f"{spread}_entry_long"] | flag_df[f"{spread}_entry_short"]).to_numpy()
    exit_ = flag_df[f"{spread}_exit"].to_numpy()
    lens: list[int] = []
    i = 0
    n = len(flag_df)
    while i < n:
        if entry[i]:
            j = i + 1
            while j < n and not exit_[j]:
                j += 1
            if j < n:
                lens.append(j - i)
            i = j + 1
        else:
            i += 1
    return np.asarray(lens, dtype=np.int64)


def _summary(pnl: np.ndarray, n_trades: int) -> dict[str, float]:
    if n_trades == 0 or pnl.size == 0:
        return dict(
            n_trades=0, total_pnl=0.0, mean_trade_pnl=0.0,
            std_trade_pnl=0.0, sharpe=0.0, hit_rate=0.0,
        )
    mean = float(pnl.mean())
    std = float(pnl.std(ddof=1)) if pnl.size > 1 else 0.0
    sharpe = mean / std * np.sqrt(n_trades) if std > 0 else 0.0
    return dict(
        n_trades=int(n_trades),
        total_pnl=float(pnl.sum()),
        mean_trade_pnl=mean,
        std_trade_pnl=std,
        sharpe=float(sharpe),
        hit_rate=float((pnl > 0).mean()),
    )


def random_baseline(
    features_path: Path = FEATURES_PATH,
    n_paths: int = 1000,
    seed: int = 42,
) -> pd.DataFrame:
    """Simulate random-entry trading for each spread and return a
    long-format dataframe with one row per (path_id, spread).
    """
    feats = pd.read_parquet(features_path)
    feats = feats.iloc[WARMUP:]
    rng = np.random.default_rng(seed)

    rows: list[dict] = []
    for s in SPREADS:
        prices = feats[s].to_numpy()
        flag_cols = [f"{s}_{f}" for f in FLAG_NAMES]
        flags = feats[flag_cols]
        lengths = _holding_lengths(flags, s)
        n_trades = int(lengths.size)
        if n_trades == 0:
            raise RuntimeError(f"{s}: zero inferred entry→exit runs")
        T = prices.size
        # Pad length cap so entry+length never overruns the sample
        max_hold = int(lengths.max())
        eligible = np.arange(T - max_hold)
        if eligible.size < n_trades:
            raise RuntimeError(
                f"{s}: {eligible.size} eligible entries for {n_trades} trades"
            )

        for path_id in range(n_paths):
            # sample with replacement (pre-registered algorithm)
            hold = rng.choice(lengths, size=n_trades, replace=True)
            # uniform entry dates without replacement
            entries = rng.choice(eligible, size=n_trades, replace=False)
            directions = rng.choice([1.0, -1.0], size=n_trades)
            pnl = directions * (prices[entries + hold] - prices[entries])
            row = _summary(pnl, n_trades)
            row["path_id"] = int(path_id)
            row["spread"] = s
            rows.append(row)

    df = pd.DataFrame(rows)[
        ["path_id", "spread", "n_trades", "total_pnl",
         "mean_trade_pnl", "std_trade_pnl", "sharpe", "hit_rate"]
    ]
    return df


def build(
    features_path: Path = FEATURES_PATH,
    out_path: Path = OUT_PATH,
    n_paths: int = 1000,
    seed: int = 42,
) -> pd.DataFrame:
    print(f"[benchmarks] random_baseline(n_paths={n_paths}, seed={seed})")
    df = random_baseline(features_path, n_paths=n_paths, seed=seed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    print(f"  [write] {out_path}: {df.shape}")
    return df


if __name__ == "__main__":
    build()
