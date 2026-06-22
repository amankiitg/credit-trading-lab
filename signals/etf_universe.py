"""ETF universe definition and close-price loader for sprint v8.1.

Reuses signals.load as the sole yfinance I/O boundary -- no second
ingest path. Universe membership is documented in sprints/v8.1/PRD.md
and is chosen for liquidity and data availability only, not for trend
performance (v8 House Rule 4).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from signals.load import DEFAULT_START, fetch, write_raw

RAW_DIR: Path = Path("data/raw")

UNIVERSE: list[str] = ["SPY", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD"]

ASSET_CLASS: dict[str, str] = {
    "SPY": "equity",
    "EFA": "equity",
    "EEM": "equity",
    "TLT": "rates",
    "IEF": "rates",
    "HYG": "credit",
    "LQD": "credit",
    "GLD": "commodity",
}


def ingest(
    tickers: list[str],
    start: str = DEFAULT_START,
    end: str | None = None,
    raw_dir: Path = RAW_DIR,
) -> None:
    """Fetch tickers via the existing yfinance boundary and write to raw_dir.

    Falls back to committed parquet (stale but functional) if yfinance is
    rate-limited or unavailable, so the cron doesn't crash on a transient
    Yahoo Finance error.
    """
    end = end or date.today().isoformat()
    try:
        data = fetch(tickers, start, end)
        for t, df in data.items():
            print(f"{t}: {len(df)} rows, {df.index.min().date()} -> {df.index.max().date()}")
        write_raw(data, raw_dir)
    except Exception as exc:
        print(f"[ingest] yfinance failed ({type(exc).__name__}: {exc}) -- using committed parquet", flush=True)


def load_universe_close(
    tickers: list[str] = UNIVERSE,
    raw_dir: Path = RAW_DIR,
) -> pd.DataFrame:
    """Outer-joined adj_close matrix, date x ticker.

    Outer join is deliberate: staggered inception (e.g. GLD from 2004,
    SPY from 1993) is preserved as leading NaN per column, rather than
    truncated to the latest common start date. This is what makes the
    point-in-time universe-membership check meaningful downstream.
    """
    frames = []
    for t in tickers:
        path = raw_dir / f"{t}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing -- run etf_universe.ingest() first")
        df = pd.read_parquet(path)
        frames.append(df[["adj_close"]].rename(columns={"adj_close": t}))
    merged = pd.concat(frames, axis=1, join="outer").sort_index()
    merged.index.name = "date"
    return merged
