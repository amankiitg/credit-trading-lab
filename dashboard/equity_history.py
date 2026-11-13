"""End-of-day account equity for the evening NAV series, read from Supabase.

The account-value chart used to reconstruct each evening's NAV from the morning
execution rows:

    evening_nav(t) = live_nav(t+1) - book_pnl(t+1)

That mixes two clocks. live_nav is the equity the execution job read at 10:32 on the
next morning; book_pnl is that session's close-to-close move. Subtracting one from the
other leaves every point carrying the next day's 10:32-to-close move -- a few dollars
on a quiet day, hundreds on a trend day -- so the series looked like the evening NAV
while tracking a different window.

The close itself is published, and the evening reconciliation reads it from the
portfolio history every run with a next-calendar-day stamp rule. It stores that read in
public.close_equity (scripts/run_signal.py step 8b), so this module is one table read:

  - no Alpaca credential on the dashboard, and no broker call from the page;
  - no second ingest path: the values are the same rows the reconciliation measured,
    labelled with the session they belong to (risk.execution_pnl.published_closes).

A read failure raises CloseEquityUnavailable and the page says the history is
unavailable. An empty table is not a failure: before the first evening run and before
the one-off backfill it simply has no rows yet, and the chart shows only the latest
stored NAV, which is a true statement about today.
"""

from __future__ import annotations

import streamlit as st

# The table changes once per evening, so a short cache keeps tab switches and widget
# reruns from re-reading it while still letting a page opened after the signal run see
# the new close.
CLOSE_EQUITY_TTL_SECS: int = 300


class CloseEquityUnavailable(RuntimeError):
    """The close NAV series could not be read.

    Distinct from "this session's close is not stored yet", which is a normal fact
    about the calendar and leaves that one session out of the series: the page keeps
    the stored evening setting for it instead of inventing a number.
    """


def close_equity_by_session(sessions: list[str]) -> dict[str, float]:
    """The stored close equity of every requested session that has one.

    Raises CloseEquityUnavailable when the table cannot be read at all. A session with
    no row is simply absent from the result, and the caller keeps its stored evening
    NAV for that point.
    """
    days = sorted({str(day)[:10] for day in sessions if day})
    if not days:
        return {}

    from dashboard.supabase_client import fetch_close_equity

    try:
        stored = fetch_close_equity(raise_on_error=True)
    except Exception as exc:  # missing table, missing credentials, transport fault
        raise CloseEquityUnavailable(str(exc)) from exc

    return {day: stored[day] for day in days if day in stored}


@st.cache_data(ttl=CLOSE_EQUITY_TTL_SECS, show_spinner=False)
def cached_close_equity_by_session(sessions: tuple[str, ...]) -> dict[str, float]:
    """close_equity_by_session, cached per session set for one TTL."""
    return close_equity_by_session(list(sessions))
