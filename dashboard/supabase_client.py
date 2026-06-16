"""Supabase connection singleton for the v8.5 attribution lab.

Reads SUPABASE_URL and SUPABASE_SECRET_KEY from the environment (.env).
The URL stored in .env includes the /rest/v1/ suffix; this module strips
it before constructing the client (the supabase-py constructor needs the
project root URL, not the REST endpoint).

Returns None when credentials are not configured so callers can degrade
gracefully instead of raising at import time.
"""

from __future__ import annotations

import os

_client = None


def get_supabase_client():
    """Return the Supabase client, constructing it once on first call.

    Returns None if SUPABASE_URL or SUPABASE_SECRET_KEY are not set.
    """
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        return None

    url = url.removesuffix("/rest/v1/")

    from supabase import create_client
    _client = create_client(url, key)
    return _client


def write_decision(
    signal_date: str,
    decision: str,
    ticker: str,
    proposed_target_weight: float,
    proposed_delta_notional: float,
    approved_by: str,
) -> bool:
    """Insert one row into the decisions table. Returns True on success."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("decisions").insert({
            "signal_date": signal_date,
            "decision": decision,
            "ticker": ticker,
            "proposed_target_weight": float(proposed_target_weight),
            "proposed_delta_notional": float(proposed_delta_notional),
            "approved_by": approved_by,
        }).execute()
        return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "write_decision failed: %s -- ensure the 'decisions' table exists "
            "(run sprints/v8.5/supabase_schema.sql in the Supabase SQL editor first)",
            exc,
        )
        return False


def fetch_decisions_for_date(signal_date: str) -> list[dict]:
    """Return all decisions already recorded for the given signal date."""
    client = get_supabase_client()
    if client is None:
        return []
    try:
        resp = (
            client.table("decisions")
            .select("ticker, decision, approved_by, created_at")
            .eq("signal_date", signal_date)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def fetch_positions() -> list[dict]:
    """Return all current open positions from the positions table."""
    client = get_supabase_client()
    if client is None:
        return []
    try:
        resp = (
            client.table("positions")
            .select("*")
            .order("ticker")
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def fetch_pnl_log(limit: int = 200) -> list[dict]:
    """Return the most recent fill records from pnl_log."""
    client = get_supabase_client()
    if client is None:
        return []
    try:
        resp = (
            client.table("pnl_log")
            .select("*")
            .order("fill_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []
