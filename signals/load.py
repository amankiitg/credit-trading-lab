"""yfinance ingest → data/raw/{ticker}.parquet.

Sole I/O boundary between the pipeline and yfinance / the raw parquet
layer. Downstream modules must never talk to yfinance directly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yfinance as yf

# Per-call network timeout for this boundary. yfinance already defaults to 10s,
# but 10s is short for a full-history fetch and was never a deliberate choice
# here, so it is set explicitly. Uses the same env var as
# execution/job_guard.DEFAULT_NETWORK_TIMEOUT_SECS, read directly to keep this
# module free of any dependency on the execution package.
YF_TIMEOUT_SECS: float = float(os.environ.get("NETWORK_TIMEOUT_SECS", "30"))

DEFAULT_TICKERS: list[str] = ["HYG", "LQD", "SPY", "IEF"]
DEFAULT_START: str = "2007-04-11"
DEFAULT_RAW_DIR: Path = Path("data/raw")

_COLUMN_MAP: dict[str, str] = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def fetch(
    tickers: list[str],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    """Pull daily OHLCV + adjusted close for each ticker from yfinance."""
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        hist = yf.Ticker(t).history(
            start=start, end=end, auto_adjust=False, actions=False,
            timeout=YF_TIMEOUT_SECS,
        )
        if hist.empty:
            raise RuntimeError(f"yfinance returned empty frame for {t}")
        df = hist[list(_COLUMN_MAP.keys())].rename(columns=_COLUMN_MAP)
        for c in ("open", "high", "low", "close", "adj_close"):
            df[c] = df[c].astype("float64")
        df["volume"] = df["volume"].astype("int64")
        idx = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
        df.index = pd.DatetimeIndex(idx, name="date")
        out[t] = df
    return out


def write_raw(
    data: dict[str, pd.DataFrame],
    out_dir: Path = DEFAULT_RAW_DIR,
) -> None:
    """Write one parquet file per ticker to ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ticker, df in data.items():
        df.to_parquet(out_dir / f"{ticker}.parquet")


if __name__ == "__main__":
    import argparse
    from datetime import date

    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--out", default=str(DEFAULT_RAW_DIR))
    args = p.parse_args()

    data = fetch(DEFAULT_TICKERS, args.start, args.end)
    for t, df in data.items():
        print(f"{t}: {len(df)} rows, {df.index.min().date()} → {df.index.max().date()}")
    write_raw(data, Path(args.out))
    print(f"wrote {len(data)} parquet files to {args.out}")
