"""Backfill sessions missing from the raw per-ticker parquets.

`load_universe_close` outer-joins data/raw/{ticker}.parquet, so a session that one
ticker lacks becomes NaN for that ticker on a date the rest of the universe has.
That is not cosmetic: the NaN return makes the 63 day rolling vol NaN, sigma goes
NaN, the position for that name is undefined, and `apply_rebalance_control` carries
the previous weight forward while the gross cap re-scales the whole row. The
2026-09-24 signal run shipped exactly that, with EFA, EEM, IEF, HYG and LQD all
moving by 0.765.

This fills the hole from Alpaca's IEX daily bars, which cover every US session and
are available on the paper account's data subscription.

Deliberately NOT a forward fill: repeating the previous close invents a zero
return, which would silently flatten realised vol and leave the row looking
healthy. A real bar is inserted or the gap is reported and left alone.

The adjusted close is derived, not copied: Alpaca returns raw OHLC, and this
project's pipeline reads `adj_close`. The backfilled `adj_close` is the Alpaca
close scaled by that ticker's own cumulative adjustment factor (adj_close/close
from its most recent existing row), so the ticker's existing adjustment
convention is preserved rather than a second one being introduced.

Report-only by default. The write requires --apply.

Usage:
    python scripts/backfill_raw_gap.py                    # report the gaps
    python scripts/backfill_raw_gap.py --apply             # fill them

Env vars required:
    ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill_raw_gap")

RAW_DIR = Path("data/raw")
_UNIVERSE = ["SPY", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD"]

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            import os
            os.environ.setdefault(k.strip(), v.strip())


def load_raw(raw_dir: Path = RAW_DIR) -> dict[str, pd.DataFrame]:
    """Read each ticker's raw parquet, normalising the index to dates."""
    frames: dict[str, pd.DataFrame] = {}
    for ticker in _UNIVERSE:
        path = raw_dir / f"{ticker}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing")
        df = pd.read_parquet(path)
        df.index = pd.DatetimeIndex(df.index).normalize()
        frames[ticker] = df.sort_index()
    return frames


def close_matrix(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """adj_close per ticker, outer-joined, exactly as load_universe_close sees it."""
    return pd.concat(
        [df[["adj_close"]].rename(columns={"adj_close": t}) for t, df in frames.items()],
        axis=1,
        join="outer",
    ).sort_index()


def adjustment_ratio(frame: pd.DataFrame, before: pd.Timestamp) -> float:
    """That ticker's own cumulative adj_close/close factor, from its last row before `before`.

    Alpaca returns unadjusted OHLC. Scaling by the ticker's existing factor keeps
    the inserted row on the same basis as the rows around it. On the newest
    sessions the factor is 1.0 for these ETFs, but deriving it means the script
    stays correct if it is ever used to repair an older hole.
    """
    prior = frame.loc[frame.index < before]
    if prior.empty:
        return 1.0
    last = prior.iloc[-1]
    close = float(last.get("close", float("nan")))
    adj = float(last.get("adj_close", float("nan")))
    if not (close > 0) or pd.isna(adj):
        logger.warning("adjustment_ratio: unusable prior row, falling back to 1.0")
        return 1.0
    return adj / close


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill missing sessions in data/raw")
    p.add_argument("--apply", action="store_true",
                   help="write the parquets (default is report only)")
    p.add_argument("--raw-dir", default=str(RAW_DIR))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_dir = Path(args.raw_dir)

    from execution.alpaca_paper import apply_request_timeout
    from signals.data_quality import find_row_gaps

    frames = load_raw(raw_dir)
    close = close_matrix(frames)

    logger.info("row counts:")
    for ticker, df in frames.items():
        logger.info("  %-4s %5d rows  %s -> %s", ticker, len(df),
                    df.index.min().date(), df.index.max().date())

    gaps = find_row_gaps(close)
    if not gaps:
        logger.info(
            "no gaps: every ticker has an observation on every date in the union. "
            "Nothing to backfill."
        )
        return 0

    logger.warning("%d gap(s) found:", len(gaps))
    for gap in gaps:
        logger.warning(
            "  %s missing %s", gap["ticker"],
            gap["date"].date() if gap["date"] is not None else "(all dates)",
        )

    dated = [g for g in gaps if g["date"] is not None]
    if not dated:
        logger.error("only empty columns found, which this tool cannot repair")
        return 1

    # -- Fetch the real bars from Alpaca IEX.
    import os
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    dates = [g["date"] for g in dated]
    start = datetime.combine(min(dates).date(), time.min, tzinfo=timezone.utc)
    end = datetime.combine(max(dates).date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
    dc = StockHistoricalDataClient(
        os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
    )
    apply_request_timeout(dc)
    bars = dc.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=sorted({g["ticker"] for g in dated}),
        timeframe=TimeFrame.Day,
        feed=DataFeed.IEX,
        start=start,
        end=end,
    ))

    by_key = {
        (sym, bar.timestamp.date()): bar
        for sym, series in bars.data.items()
        for bar in series
    }

    # -- Build the replacement rows.
    inserts: dict[str, list[dict]] = {}
    unresolved: list[dict] = []
    for gap in dated:
        ticker, date = gap["ticker"], gap["date"]
        bar = by_key.get((ticker, date.date()))
        if bar is None:
            unresolved.append(gap)
            continue
        ratio = adjustment_ratio(frames[ticker], date)
        inserts.setdefault(ticker, []).append({
            "date": date,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "adj_close": float(bar.close) * ratio,
            "volume": int(bar.volume),
        })
        logger.info(
            "  %s %s <- Alpaca IEX close=%.4f adj_ratio=%.6f volume=%d",
            ticker, date.date(), float(bar.close), ratio, int(bar.volume),
        )

    if unresolved:
        logger.error(
            "Alpaca IEX has no bar for %d requested session(s): %s. Refusing to "
            "invent them.",
            len(unresolved),
            [(g["ticker"], g["date"].date()) for g in unresolved],
        )
        return 1

    if not args.apply:
        logger.info(
            "report only (default). %d row(s) would be inserted. Pass --apply to write.",
            sum(len(v) for v in inserts.values()),
        )
        return 0

    # -- Write, then verify by re-reading.
    for ticker, rows in inserts.items():
        df = frames[ticker]
        add = pd.DataFrame(rows).set_index("date")
        add.index = pd.DatetimeIndex(add.index).normalize()
        combined = pd.concat([df, add[df.columns]], axis=0).sort_index()
        if combined.index.has_duplicates:
            logger.error("%s: refusing to write, duplicate dates after insert", ticker)
            return 1
        combined.to_parquet(raw_dir / f"{ticker}.parquet")
        logger.info("  wrote %s: %d -> %d rows", ticker, len(df), len(combined))

    after = close_matrix(load_raw(raw_dir))
    remaining = find_row_gaps(after)
    if remaining:
        logger.error(
            "verification FAILED: %d gap(s) remain: %s",
            len(remaining),
            [(g["ticker"], g["date"].date() if g["date"] is not None else None)
             for g in remaining],
        )
        return 1

    logger.info(
        "verified: every ticker now covers every date in the union (%d dates).",
        len(after),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
