"""FRED ingest: BAML OAS series + Treasury yield curve.

Public CSV endpoint, no API key required:
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>

FRED serves '.' for missing values. We parse those as NaN, forward-fill
up to one business day, drop any row still missing, and log the drop
count. Index is tz-naive business-day DatetimeIndex.

Also builds two synthetic CDS proxy columns:
    synth_cds_hy = HYG trailing-12m dist yield (%) - DGS5
    synth_cds_ig = LQD trailing-12m dist yield (%) - DGS10

The TTM-yield proxy is used because iShares does not expose historical
yield-to-worst via a scriptable public endpoint. The fallback is
documented on the saved frame as `ytw_source_{hyg,lqd}` columns.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

FRED_CSV_URL: str = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
OUT_PATH: Path = Path("data/raw/credit_market_data.parquet")

SERIES: dict[str, str] = {
    "oas_hy": "BAMLH0A0HYM2",
    "oas_bbb": "BAMLC0A4CBBB",
    "oas_ig": "BAMLC0A0CM",
    "dgs1": "DGS1",
    "dgs2": "DGS2",
    "dgs3": "DGS3",
    "dgs5": "DGS5",
    "dgs7": "DGS7",
    "dgs10": "DGS10",
    "dgs20": "DGS20",
    "dgs30": "DGS30",
}


def fetch_series(series_id: str, timeout: int = 30) -> pd.Series:
    """Pull one FRED series by ID. Returns a float64 Series on a
    tz-naive DatetimeIndex. Missing values ('.') parsed as NaN.
    """
    r = requests.get(FRED_CSV_URL.format(sid=series_id), timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text), na_values=".")
    # FRED schema: date column is either 'DATE' or 'observation_date'
    date_col = "DATE" if "DATE" in df.columns else "observation_date"
    df[date_col] = pd.to_datetime(df[date_col])
    return df.set_index(date_col)[series_id].astype("float64").rename(series_id)


def fetch_fred(series: dict[str, str] | None = None) -> pd.DataFrame:
    """Pull all configured series into one wide frame."""
    series = series or SERIES
    frames = []
    for name, sid in series.items():
        s = fetch_series(sid).rename(name)
        print(f"  [fred] {name} ({sid}): {len(s)} rows {s.index.min().date()} → {s.index.max().date()}")
        frames.append(s)
    df = pd.concat(frames, axis=1).sort_index()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    # Align to business-day index across the union range
    bday = pd.bdate_range(df.index.min(), df.index.max())
    df = df.reindex(bday)
    df.index.name = "date"
    return df


def _ttm_yield(ticker: str, price_index: pd.DatetimeIndex) -> pd.Series:
    """Trailing-12-month dividend yield (%) for an ETF, aligned to
    ``price_index``. Uses yfinance dividend stream + adj_close.
    """
    t = yf.Ticker(ticker)
    divs = t.dividends
    if divs.empty:
        return pd.Series(np.nan, index=price_index, name=f"{ticker}_ttm_yield")
    divs.index = pd.DatetimeIndex(divs.index).tz_localize(None).normalize()
    # Daily cumulative dividend over trailing 252 business days
    daily = divs.reindex(price_index, fill_value=0.0)
    ttm_div = daily.rolling(252, min_periods=1).sum()
    price = pd.read_parquet(Path("data/raw") / f"{ticker}.parquet")["adj_close"]
    price.index = pd.DatetimeIndex(price.index).tz_localize(None).normalize()
    price = price.reindex(price_index).ffill()
    ttm_yield_pct = (ttm_div / price) * 100.0
    return ttm_yield_pct.rename(f"{ticker}_ttm_yield")


def build_synth_cds(df: pd.DataFrame) -> pd.DataFrame:
    """Append synthetic CDS proxy columns to the FRED frame.

    Uses TTM distribution yield as the YTW proxy (documented fallback;
    sets the ytw_source flags so downstream code can audit the choice).
    """
    idx = df.index
    hyg_y = _ttm_yield("HYG", idx)
    lqd_y = _ttm_yield("LQD", idx)
    df = df.copy()
    df["synth_cds_hy"] = hyg_y - df["dgs5"]
    df["synth_cds_ig"] = lqd_y - df["dgs10"]
    df["ytw_source_hyg"] = "ttm_div_proxy"
    df["ytw_source_lqd"] = "ttm_div_proxy"
    return df


def build(out_path: Path = OUT_PATH) -> pd.DataFrame:
    print("[fred] build()")
    df = fetch_fred()
    before = len(df)
    # Forward-fill up to 1 business day, then drop remaining NaN rows
    numeric = df.ffill(limit=1)
    numeric = numeric.dropna(how="any")
    dropped = before - len(numeric)
    print(f"  [fred] forward-fill + drop: {before} → {len(numeric)} rows ({dropped} dropped)")

    numeric = build_synth_cds(numeric)
    print(f"  [fred] +synth_cds: {len(numeric.columns)} cols")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    numeric.to_parquet(out_path)
    print(f"  [write] {out_path}: {numeric.shape}")
    return numeric


if __name__ == "__main__":
    build()
