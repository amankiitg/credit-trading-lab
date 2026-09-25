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

    After fetching, any session that one ticker is missing but the others have is
    repaired from Alpaca IEX daily bars before the parquets are written (see
    signals/raw_repair.py). yfinance does drop individual sessions for a subset of
    the universe, and an unrepaired hole silently changes the signal rather than
    failing, so the repair belongs here rather than in a manual step.

    The repair is holes only, and is capped per ticker. Anything it refuses is left
    for the data-quality gate in run_signal to block on, with the reason logged.
    """
    from datetime import timedelta
    # yfinance end is exclusive, so pass today+1 to include today's close
    end = end or (date.today() + timedelta(days=1)).isoformat()
    data = fetch(tickers, start, end)
    for t, df in data.items():
        print(f"{t}: {len(df)} rows, {df.index.min().date()} -> {df.index.max().date()}")

    from signals.raw_repair import repair_frames
    data, report = repair_frames(data)
    if report["filled"]:
        print(
            f"hole repair: filled {len(report['filled'])} session(s) from "
            f"alpaca_iex: "
            + ", ".join(
                f"{f['ticker']}@{f['date'].date()}" for f in report["filled"]
            )
        )

    write_raw(data, raw_dir)


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
