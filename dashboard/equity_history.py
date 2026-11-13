"""End-of-day account equity for the evening NAV series, read from Alpaca.

The account-value chart used to reconstruct each evening's NAV from the morning
execution rows:

    evening_nav(t) = live_nav(t+1) - book_pnl(t+1)

That mixes two clocks. live_nav is the equity the execution job read at 10:32 on the
next morning; book_pnl is that session's close-to-close move. Subtracting one from the
other leaves every point carrying the next day's 10:32-to-close move -- a few dollars
on a quiet day, hundreds on a trend day -- so the series looked like the evening NAV
while tracking a different window.

The close itself is published: the portfolio history has one end-of-day equity point
per session. This module reads it with the reconciliation's own read and stamp rule,
not a second ingest path:

  - execution.alpaca_paper.fetch_equity_history -- one read-only GET per ~10-day
    window, no order endpoint, the same call risk/execution_pnl.py reconciles with;
  - risk.execution_pnl.close_equity -- the next-calendar-day stamp convention that
    module re-tests on every run.

Nothing here submits, sizes or cancels an order. A failed read raises: an empty
mapping would be indistinguishable from a history that has no stamp for the latest
session yet, and the page needs to say which of the two it is.
"""

from __future__ import annotations

import streamlit as st

# The close is final once the session ends, so an hour only keeps tab switches and
# widget reruns from re-hitting the broker; a page opened after a close sees it.
CLOSE_EQUITY_TTL_SECS: int = 3600

# fetch_equity_history() asks for a ~12-calendar-day window. Walking back one window
# at a time bounds the read to a handful of GETs for the default period; the cap keeps
# a very old period control from turning one page load into dozens of calls.
MAX_WINDOWS: int = 24


class CloseEquityUnavailable(RuntimeError):
    """The close NAV series could not be read.

    Distinct from "this session's close is not stamped yet", which is a normal fact
    about the calendar and leaves that one session out of the series.
    """


def _walk_close_equity(client, start_session: str, end_session: str) -> dict[str, float]:
    """End-of-day equity stamps covering start_session..end_session.

    Each fetch returns a window around its anchor; the walk moves the anchor back to
    the earliest stamp it has until the window reaches the first session wanted.
    """
    from execution.alpaca_paper import fetch_equity_history

    equity: dict[str, float] = {}
    anchor = end_session
    for _ in range(MAX_WINDOWS):
        window = fetch_equity_history(client, anchor)
        if not window:
            break
        equity.update(window)
        earliest = min(window)
        if earliest <= start_session:
            break
        anchor = earliest
    if not equity:
        raise CloseEquityUnavailable(
            f"the portfolio history has no equity points around {end_session}"
        )
    return equity


def close_equity_by_session(sessions: list[str]) -> dict[str, float]:
    """The close equity of every session whose close has been published.

    Raises CloseEquityUnavailable when the broker cannot be read at all. A session
    whose close is not stamped yet is simply absent from the result: the caller keeps
    its stored evening NAV for that point instead.
    """
    from risk.execution_pnl import close_equity

    days = sorted({str(day)[:10] for day in sessions if day})
    if not days:
        return {}

    try:
        from execution.alpaca_paper import connect

        client = connect(dry_run=False)
        if client is None:
            raise CloseEquityUnavailable("no Alpaca client")
    except CloseEquityUnavailable:
        raise
    except Exception as exc:  # missing keys, SDK absent, transport fault
        raise CloseEquityUnavailable(
            f"Alpaca client unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    equity = _walk_close_equity(client, days[0], days[-1])
    out: dict[str, float] = {}
    for day in days:
        value = close_equity(equity, day)
        if value is not None:
            out[day] = value
    return out


@st.cache_data(ttl=CLOSE_EQUITY_TTL_SECS, show_spinner=False)
def cached_close_equity_by_session(sessions: tuple[str, ...]) -> dict[str, float]:
    """close_equity_by_session, cached per session set for one TTL."""
    return close_equity_by_session(list(sessions))
