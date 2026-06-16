"""Per-ticker dividend/distribution history, sprint v8.3 (gate G2).

Sole new yfinance access path for this sprint -- signals.load.fetch uses
actions=False, deliberately excluding dividend rows, so this is a
genuinely new ingestion path, not a reuse of an existing fetch.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

RAW_DIR: Path = Path("data/raw")


def fetch_dividends(tickers: list[str]) -> dict[str, pd.Series]:
    """Pull per-share distribution history for each ticker from yfinance.

    Returns a tz-naive, normalized daily Series per ticker (zero-length
    Series for a ticker with no distributions, e.g. GLD -- physical gold
    pays no income, this is the correct expected value, not a data gap).
    """
    out: dict[str, pd.Series] = {}
    for t in tickers:
        div = yf.Ticker(t).dividends
        idx = pd.DatetimeIndex(div.index).tz_localize(None).normalize()
        s = pd.Series(div.to_numpy(dtype="float64"), index=idx, name="dividend")
        s = s.groupby(s.index).sum().sort_index()
        out[t] = s
    return out


def write_dividends(data: dict[str, pd.Series], raw_dir: Path = RAW_DIR) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for t, s in data.items():
        s.to_frame().to_parquet(raw_dir / f"{t}_dividends.parquet")


def ingest(tickers: list[str], raw_dir: Path = RAW_DIR) -> None:
    data = fetch_dividends(tickers)
    for t, s in data.items():
        nonzero = int((s > 0).sum())
        first = s.index.min().date() if len(s) else None
        last = s.index.max().date() if len(s) else None
        print(f"{t}: {nonzero} distributions, {first} -> {last}")
    write_dividends(data, raw_dir)


def load_dividend_matrix(
    tickers: list[str],
    close_index: pd.DatetimeIndex,
    raw_dir: Path = RAW_DIR,
) -> pd.DataFrame:
    """Date x ticker matrix of per-share distributions, reindexed onto
    close_index (the trading calendar), zero on every non-distribution day.
    """
    cols = {}
    for t in tickers:
        path = raw_dir / f"{t}_dividends.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing -- run signals.dividends.ingest() first")
        s = pd.read_parquet(path)["dividend"]
        cols[t] = s.reindex(close_index, fill_value=0.0)
    out = pd.DataFrame(cols, index=close_index)
    out.index.name = "date"
    return out
