"""Current closes for the risk panel, from the source run_signal refreshes daily.

The risk panel used to build its covariance from `dashboard.loader.load_close_matrix`,
which reads `data/raw/*.parquet` out of the repository. Those files are a build-time
snapshot: the copy committed on `main` ends in April/June 2026 and the dashboard
container never rewrites them, because the refresh lives in the signal cron on a
different Render service. So the panel measured today's book against a window that
closed months earlier, and the gap between the last cached price and today was
pad-filled into a run of exactly-zero returns.

`run_signal` step 3 refreshes the universe through `signals.load.fetch` and nothing
else. This module calls that same boundary for a trailing window, so the panel and the
signal are looking at the same prices, without a second ingest path and without
reading the committed cache.

Nothing here falls back to the committed parquet files: a failed fetch is raised, and
the panel says the risk view is unavailable rather than drawing a stale one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from risk.live_risk import COV_WINDOW_SESSIONS
from signals.etf_universe import UNIVERSE

# Calendar days of history to request for a COV_WINDOW_SESSIONS-session window.
# 120 sessions is about 172 calendar days; 260 leaves room for holidays and for a
# long weekend at either end without ever shortening the window in normal markets.
LIVE_LOOKBACK_DAYS: int = 260

# The fetch is a network call to the same source the signal cron reads. An hour is
# short enough that a dashboard opened after the close sees that close, and long
# enough that tab switches and widget reruns do not re-hit yfinance.
LIVE_PRICE_TTL_SECS: int = 3600


def close_matrix_from_frames(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join per-ticker frames into one date x ticker adjusted-close matrix."""
    out = {}
    for ticker, df in frames.items():
        if "adj_close" in df.columns:
            out[ticker] = df["adj_close"]
        else:
            out[ticker] = df["close"]
    merged = pd.DataFrame(out).sort_index()
    merged.index.name = "date"
    return merged


def window_returns(
    close: pd.DataFrame,
    sessions: int = COV_WINDOW_SESSIONS,
) -> pd.DataFrame:
    """Daily simple returns over the ``sessions`` sessions ending at the last
    session every ticker actually has a close for.

    Three things happen before the difference, and each one exists because the
    panel used to show "119 of 120 returns" for all eight tickers and exclude the
    whole book:

    1. Rows with no close for any ticker are dropped. The source still returns a
       row for the current calendar day (and sometimes for a holiday) before that
       session's data exists; the outer join in
       :func:`close_matrix_from_frames` then carries that date as a row of NaN.
       It is not a session.

    2. The window is anchored at the latest session present in *all* tickers, not
       at ``date.today()``. When today's close has not arrived for one ticker
       yet, the window ends at the previous session for every ticker; it does not
       exclude every ticker because one row is late.

    3. ``sessions + 1`` closes are taken before differencing, because N returns
       are ratios of N+1 consecutive closes. A 120-return window needs 121 aligned
       closes, never 120.

    ``fill_method=None`` is the point of the differencing call. pandas' default
    pads a missing price forward before differencing, which turns every session
    after the last observation into a return of exactly 0.0 -- indistinguishable,
    to a covariance, from a real quiet day. A padded return is not data, so it is
    left as NaN here and the eligible-ticker filter drops that column instead. A
    genuine hole *inside* the window therefore still names and excludes its
    ticker; it is never filled.
    """
    if close.empty:
        raise ValueError("no closes to build returns from")

    # (1) A date on which no ticker has a close is a calendar artefact, not a
    # session. Keeping it would place one NaN row inside the window and exclude
    # every column from a covariance that is otherwise complete.
    priced = close.dropna(how="all")
    if priced.empty:
        raise ValueError("no ticker has a close in the fetch")

    # (2) Anchor on the last session all tickers share. A close that has not
    # arrived yet shortens the window by one session instead of excluding the
    # book. A ticker with no data at all is left in the frame, so the eligibility
    # filter names it rather than silently dropping it here.
    priced_cols = priced.columns[priced.notna().any(axis=0)]
    if len(priced_cols) == 0:
        raise ValueError("no ticker has a close in the fetch")
    complete = priced[priced_cols].dropna(how="any")
    if complete.empty:
        raise ValueError("no session has a close for every ticker")
    anchor = complete.index[-1]
    aligned = priced.loc[:anchor]

    # (3) N returns are ratios of N+1 closes.
    need = sessions + 1
    if len(aligned) < need:
        raise ValueError(
            f"only {len(aligned)} aligned closes ending {anchor.date()} for a "
            f"{sessions}-return window ({need} closes needed): widen the fetch"
        )

    rets = aligned.tail(need).pct_change(fill_method=None).iloc[1:]
    return rets.copy()


@st.cache_data(ttl=LIVE_PRICE_TTL_SECS, show_spinner=False)
def load_live_closes(
    tickers: tuple[str, ...] = tuple(UNIVERSE),
    lookback_days: int = LIVE_LOOKBACK_DAYS,
    as_of: str | None = None,
) -> pd.DataFrame:
    """Fetch the universe's adjusted closes from the signal's own yfinance boundary.

    Deliberately *not* `dashboard.loader.load_close_matrix`: that reads the
    committed parquet snapshot and cannot be current. This reads the same
    `signals.load.fetch` that run_signal step 3 calls every evening.

    Raises on failure. A caller that cannot reach the source has no covariance to
    show, and must say so instead of falling back to the repository snapshot.
    """
    from signals.load import fetch

    end = (date.fromisoformat(as_of) if as_of else date.today()) + timedelta(days=1)
    start = end - timedelta(days=lookback_days)

    frames = fetch(list(tickers), start.isoformat(), end.isoformat())
    close = close_matrix_from_frames(frames)
    if close.empty:
        raise RuntimeError("yfinance returned an empty close matrix")
    return close


def load_live_returns(
    tickers: tuple[str, ...] = tuple(UNIVERSE),
    sessions: int = COV_WINDOW_SESSIONS,
    as_of: str | None = None,
) -> pd.DataFrame:
    """The covariance window: ``sessions`` returns ending at the latest session
    every requested ticker shares a close for.
    """
    return window_returns(load_live_closes(tickers=tickers, as_of=as_of), sessions=sessions)
