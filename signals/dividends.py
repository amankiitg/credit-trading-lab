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

# A hand-curated escape hatch for payouts the vendor has not published yet. Committed
# on purpose so it ships inside the Render image: the cron has no persistent disk, so a
# file that is not in git does not exist in the container.
DISTRIBUTIONS_OVERRIDE: Path = Path("data") / "distributions_override.csv"

# Monthly payers: the iShares funds whose ex-date is the first business day of every
# month. Only these are held to the month-start check. Quarterly and semiannual payers
# (SPY, EFA, EEM) are deliberately absent: their gap is three to six months, so the same
# check would refuse them every month that is not a pay month.
MONTHLY_PAYERS: frozenset[str] = frozenset({"TLT", "IEF", "HYG", "LQD"})

# Funds that never distribute. An empty history is the correct value for these, not a
# data gap: GLD holds metal and pays no income.
NON_PAYERS: frozenset[str] = frozenset({"GLD"})

# How long the vendor has to publish the month-start payout before its absence counts as
# a missing row. The ex-date is the first business day; asking for it on the day itself
# would refuse that same session every month.
MONTHLY_GRACE_BUSINESS_DAYS: int = 2

# January is exempt. iShares paid on 2025-12-19 and then on 2026-02-02 -- a 45-day gap
# with no January ex-date at all -- so requiring one refuses a month that has no payout
# by construction, and would refuse it every year.
EXEMPT_MONTHS: frozenset[int] = frozenset({1})


class StaleDividendCacheError(RuntimeError):
    """A monthly payer's history stops short of a payout it should already have had.

    Named so the retry loop and the log can tell it apart from a transport failure: a
    429 might clear on the next attempt, while a vendor that simply has not published
    October yet never will inside the same run. Raised before anything is written, so
    the previous cache and its marker survive and the stale-marker refusal in
    risk/sleeve_pnl.py decides the session instead of a fresh date vouching for a hole.
    """


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
    tickers: list[str],
    timeout: float = YF_TIMEOUT_SECS,
    override_path: Path | None = DISTRIBUTIONS_OVERRIDE,
    as_of: date | None = None,
) -> dict[str, pd.Series]:
    """Pull per-share distribution history for each ticker from yfinance.

    Returns a tz-naive, normalized daily Series per ticker, indexed by ex-date
    (zero-length Series for a ticker with no distributions, e.g. GLD -- physical gold
    pays no income, this is the correct expected value, not a data gap).

    Raises on any ticker failing, and writes nothing: a partial cache is worse than an
    old one, because the tickers that did not answer would read as having paid nothing.

    The override CSV is merged in before validation, so a payout the vendor has not
    published yet can be supplied by hand and counted. Then every monthly payer is held
    to its own schedule: a history that stops before a month-start ex-date that was
    already due raises StaleDividendCacheError here, ahead of any write.
    """
    out: dict[str, pd.Series] = {}
    with _socket_timeout(timeout):
        for t in tickers:
            div = yf.Ticker(t).dividends
            idx = pd.DatetimeIndex(div.index).tz_localize(None).normalize()
            s = pd.Series(div.to_numpy(dtype="float64"), index=idx, name="dividend")
            s = s.groupby(s.index).sum().sort_index()
            out[t] = s

    # Only the tickers the caller asked for: an override row for a fund that is not in
    # this run's universe must not appear in the result as if it had been fetched.
    override = {
        t: s for t, s in load_distributions_override(override_path).items() if t in out
    }
    out = merge_distribution_sources(out, override)

    validate_monthly_coverage(out, as_of=as_of, tickers=list(out))
    return out


def load_distributions_override(
    path: Path | None = DISTRIBUTIONS_OVERRIDE,
) -> dict[str, pd.Series]:
    """Per-ticker ex-date series from the hand-curated override CSV, or {} if absent.

    Columns are ticker, ex_date, amount, source. The source column is not used by the
    pipeline: it is there so whoever reads the file can see where a number came from and
    whether anyone has checked it against the issuer.
    """
    if path is None:
        return {}
    path = Path(path)
    if not path.exists():
        return {}

    frame = pd.read_csv(path)
    required = {"ticker", "ex_date", "amount"}
    absent = required - set(frame.columns)
    if absent:
        raise ValueError(
            f"{path} is missing column(s): {', '.join(sorted(absent))} "
            f"-- expected ticker, ex_date, amount, source"
        )

    rows: dict[str, dict[pd.Timestamp, float]] = {}
    for _, row in frame.iterrows():
        ticker = str(row["ticker"]).strip().upper()
        day = pd.Timestamp(str(row["ex_date"]).strip()).normalize()
        rows.setdefault(ticker, {})[day] = float(row["amount"])

    return {
        ticker: pd.Series(values, name="dividend").sort_index()
        for ticker, values in rows.items()
    }


def merge_distribution_sources(
    fetched: dict[str, pd.Series], override: dict[str, pd.Series]
) -> dict[str, pd.Series]:
    """Merge a fetched history with an override, the override winning on a shared date.

    The override is meant to add what the vendor has not published, but a wrong vendor
    row is also worth correcting without waiting for it, so a conflict resolves to the
    curated amount rather than the fetched one.
    """
    merged = dict(fetched)
    for ticker, extra in override.items():
        extra = extra.rename("dividend")
        base = merged.get(ticker)
        if base is None or len(base) == 0:
            merged[ticker] = extra
            continue
        kept = base[~base.index.isin(extra.index)]
        merged[ticker] = pd.concat([kept, extra]).sort_index().rename("dividend")
    return merged


def _first_business_day_of(month: date) -> date:
    """The first business day of `month`: a monthly payer's scheduled ex-date."""
    return pd.bdate_range(start=month.replace(day=1), periods=1)[0].date()


def _business_days_after(day: date, count: int) -> date:
    return (pd.Timestamp(day) + pd.offsets.BDay(count)).date()


def missing_month_start_ex_dates(
    series: pd.Series,
    as_of: date,
    grace_business_days: int = MONTHLY_GRACE_BUSINESS_DAYS,
) -> list[date]:
    """The month-start ex-dates a monthly payer owed by `as_of` and does not show.

    The walk starts at the month of the last recorded ex-date rather than at the fund's
    inception: everything earlier is settled history, and the failure this catches is a
    feed that has stopped, which only ever shows up at the tail. A month is only due once
    the grace period has passed, so a session on the ex-date itself is not a failure.
    """
    paid = (
        pd.DatetimeIndex(series.index).normalize()
        if len(series)
        else pd.DatetimeIndex([])
    )

    if len(paid):
        cursor = paid.max().date().replace(day=1)
    else:
        cursor = as_of.replace(day=1)

    missing: list[date] = []
    stop = as_of.replace(day=1)
    while cursor <= stop:
        if cursor.month not in EXEMPT_MONTHS:
            expected = _first_business_day_of(cursor)
            start = pd.Timestamp(cursor)
            if cursor < stop:
                # A finished month is judged on the whole of it. The December 2025 payout
                # landed on the 19th rather than the first business day, so a window
                # narrower than the month would refuse a month that did pay.
                end = start + pd.offsets.MonthEnd(0)
                window = paid[(paid >= start) & (paid <= end)]
            else:
                # The current month is judged on the schedule: due once the grace period
                # after the month-start ex-date has passed, absent from then on.
                due = _business_days_after(expected, grace_business_days)
                if as_of < due:
                    break
                window = paid[(paid >= start) & (paid <= pd.Timestamp(due))]
            if len(window) == 0:
                missing.append(expected)
        cursor = (pd.Timestamp(cursor) + pd.offsets.MonthBegin(1)).date()
    return missing


def validate_monthly_coverage(
    data: dict[str, pd.Series],
    as_of: date | None = None,
    tickers: list[str] | None = None,
) -> None:
    """Raise StaleDividendCacheError when a monthly payer has stopped being paid.

    Schedule-aware on purpose. A flat "newest row is recent" rule would refuse SPY every
    month that is not a quarter end, and a flat "any row this year" rule would pass a
    feed that died in June -- which is exactly the state this exists to catch.
    """
    as_of = as_of or date.today()
    checked = list(data) if tickers is None else list(tickers)
    problems: list[str] = []

    for ticker in checked:
        if ticker in NON_PAYERS or ticker not in MONTHLY_PAYERS:
            continue
        series = data.get(ticker)
        if series is None:
            series = pd.Series(dtype="float64")
        missing = missing_month_start_ex_dates(series, as_of)
        if missing:
            dates = ", ".join(d.isoformat() for d in missing)
            problems.append(f"{ticker} has no ex-date for {dates}")

    if problems:
        raise StaleDividendCacheError(
            "the distribution feed has not caught up with the payout schedule: "
            + "; ".join(problems)
            + f" (as of {as_of.isoformat()})"
        )


def write_dividends(data: dict[str, pd.Series], raw_dir: Path = RAW_DIR) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for t, s in data.items():
        s.to_frame().to_parquet(raw_dir / f"{t}_dividends.parquet")


def ingest(
    tickers: list[str],
    raw_dir: Path = RAW_DIR,
    override_path: Path | None = DISTRIBUTIONS_OVERRIDE,
) -> dict:
    """Refresh the whole distribution cache for `tickers`, all or nothing.

    Raises if any ticker fails to fetch, having written nothing, so the caller's retry
    loop can try again rather than leaving a half-updated cache behind.

    On success writes every ticker's parquet and then records the refresh date. The
    order matters: the marker is written last, so a crash between the two leaves the old
    date with new files, which reads as stale and is refused, rather than a fresh date
    with old files, which would read as current and be trusted.

    Returns a report: {ok, tickers, distributions, as_of}.
    """
    data = fetch_dividends(tickers, override_path=override_path)
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
