"""Per-ticker dividend/distribution history, sprint v8.3 (gate G2), refreshed daily in v9.9.

Sole new yfinance access path for this sprint -- signals.load.fetch uses
actions=False, deliberately excluding dividend rows, so this is a
genuinely new ingestion path, not a reuse of an existing fetch.

The series is indexed by EX-DATE, not pay date. That is the date the unadjusted close
drops by the distribution, so it is the only date on which a carry leg and a price leg
net out. A pay-date series would put the carry on a different session from the price
drop and leave the drop booked as a loss.

Refreshed by the signal cron every session, alongside the closes. The refresh date is
recorded next to the parquets so a consumer can tell a fresh cache from a stale one:
there is no way to tell from the data itself, because a ticker that paid nothing and a
ticker whose payout was never fetched are the same empty rows.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

RAW_DIR: Path = Path("data/raw")

# Per-call network timeout, the same env var signals/load.py and
# execution/job_guard.py read, so one setting bounds every fetch in the pipeline.
YF_TIMEOUT_SECS: float = float(os.environ.get("NETWORK_TIMEOUT_SECS", "30"))

# Records when the cache was last refreshed, next to the files it describes.
AS_OF_FILENAME: str = "dividends_as_of.json"


@contextlib.contextmanager
def _socket_timeout(seconds: float):
    """Bound the socket layer for the duration of a fetch, then restore it.

    yfinance's `Ticker.dividends` takes no timeout argument in the installed version and
    is a plain requests fetch underneath, so the bound is applied where it does bind:
    the process-wide socket default. That is the same mechanism job_guard installs for
    the cron jobs, and it is restored on the way out so a fetch here cannot change the
    timeout anything else runs under.
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)


def fetch_dividends(
    tickers: list[str], timeout: float = YF_TIMEOUT_SECS
) -> dict[str, pd.Series]:
    """Pull per-share distribution history for each ticker from yfinance.

    Returns a tz-naive, normalized daily Series per ticker, indexed by ex-date
    (zero-length Series for a ticker with no distributions, e.g. GLD -- physical gold
    pays no income, this is the correct expected value, not a data gap).

    Raises on any ticker failing, and writes nothing: a partial cache is worse than an
    old one, because the tickers that did not answer would read as having paid nothing.
    """
    out: dict[str, pd.Series] = {}
    with _socket_timeout(timeout):
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


def ingest(tickers: list[str], raw_dir: Path = RAW_DIR) -> dict:
    """Refresh the whole distribution cache for `tickers`, all or nothing.

    Raises if any ticker fails to fetch, having written nothing, so the caller's retry
    loop can try again rather than leaving a half-updated cache behind.

    On success writes every ticker's parquet and then records the refresh date. The
    order matters: the marker is written last, so a crash between the two leaves the old
    date with new files, which reads as stale and is refused, rather than a fresh date
    with old files, which would read as current and be trusted.

    Returns a report: {ok, tickers, distributions, as_of}.
    """
    data = fetch_dividends(tickers)
    for t, s in data.items():
        nonzero = int((s > 0).sum())
        first = s.index.min().date() if len(s) else None
        last = s.index.max().date() if len(s) else None
        print(f"{t}: {nonzero} distributions, {first} -> {last}")

    write_dividends(data, raw_dir)

    total = int(sum(int((s > 0).sum()) for s in data.values()))
    as_of = date.today().isoformat()
    write_as_of(raw_dir, as_of, len(data), total)
    return {
        "ok": True,
        "tickers": len(data),
        "distributions": total,
        "as_of": as_of,
    }


def write_as_of(raw_dir: Path, as_of: str, tickers: int, distributions: int) -> None:
    """Record the refresh date beside the cache it describes."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / AS_OF_FILENAME).write_text(
        json.dumps(
            {
                "as_of": as_of,
                "tickers": tickers,
                "distributions": distributions,
            }
        )
        + "\n"
    )


def dividends_as_of(raw_dir: Path = RAW_DIR) -> str | None:
    """The date the distribution cache was last refreshed, or None if unrecorded.

    None means "never refreshed here", which is not the same as "fresh": a container
    that has never run the ingest has the committed cache, whose coverage is unknown.
    """
    path = Path(raw_dir) / AS_OF_FILENAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text()).get("as_of")
    except Exception:
        return None
    # A marker with no date must read as no date. `str(None)` would be the string
    # "None", which sorts above every ISO date and would quietly pass a freshness check
    # that compares dates as strings.
    return str(value) if value else None


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
