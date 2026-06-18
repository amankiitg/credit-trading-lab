"""Supabase connection and table helpers for the v8.5 attribution lab.

Tables (created by the user in the Supabase dashboard):

  decisions  (trade_date date PK, decision text, created_at timestamptz)
             -- one approve/reject per day for the whole book

  positions  (trade_date date, ticker text, weight float8, notional float8,
              side text, PRIMARY KEY (trade_date, ticker))
             -- current positions; written by v8.6 execution job

  pnl_log    (trade_date date PK, gross_pnl float8, net_pnl float8,
              turnover_cost float8, borrow_cost float8, created_at timestamptz)
             -- daily aggregate P&L; written by v8.6 execution job

Reads SUPABASE_URL and SUPABASE_SECRET_KEY from the environment (.env).
The URL stored in .env includes the /rest/v1/ suffix; this module strips it
before constructing the client (create_client needs the project root URL).
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)
_client = None


def get_supabase_client():
    """Return the Supabase client, constructing it once on first call.

    Returns None if credentials are not set (callers degrade gracefully).
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


# ---------------------------------------------------------------- decisions

def write_decision(trade_date: str, decision: str) -> bool:
    """Insert or replace the day's decision ('approve' or 'reject').

    Uses upsert so re-submitting the same day overwrites the previous
    decision (trade_date is the primary key).
    """
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("decisions").upsert({
            "trade_date": trade_date,
            "decision": decision,
        }).execute()
        return True
    except Exception as exc:
        _log.error(
            "write_decision failed: %s -- ensure the 'decisions' table exists "
            "(see sprints/v8.5/supabase_schema.sql)",
            exc,
        )
        return False


def fetch_decision_for_date(trade_date: str) -> str | None:
    """Return 'approve', 'reject', or None if no decision recorded yet."""
    client = get_supabase_client()
    if client is None:
        return None
    try:
        resp = (
            client.table("decisions")
            .select("decision")
            .eq("trade_date", trade_date)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["decision"]
        return None
    except Exception:
        return None


# ---------------------------------------------------------------- positions

def fetch_positions(latest_only: bool = True) -> list[dict]:
    """Return position rows. When latest_only=True, returns only the most
    recent trade_date's rows (the current live book state).
    """
    client = get_supabase_client()
    if client is None:
        return []
    try:
        if latest_only:
            # Get the most recent trade_date first
            date_resp = (
                client.table("positions")
                .select("trade_date")
                .order("trade_date", desc=True)
                .limit(1)
                .execute()
            )
            if not date_resp.data:
                return []
            latest_date = date_resp.data[0]["trade_date"]
            resp = (
                client.table("positions")
                .select("*")
                .eq("trade_date", latest_date)
                .order("ticker")
                .execute()
            )
        else:
            resp = (
                client.table("positions")
                .select("*")
                .order("trade_date", desc=True)
                .execute()
            )
        return resp.data or []
    except Exception:
        return []


# ---------------------------------------------------------------- settings

def get_auto_approve() -> bool:
    """Return the current auto-approve standing setting (default False)."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        resp = (
            client.table("settings")
            .select("value")
            .eq("key", "auto_approve")
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["value"].lower() == "true"
        return False
    except Exception:
        return False


def set_auto_approve(enabled: bool) -> bool:
    """Upsert the auto-approve standing setting."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("settings").upsert({
            "key": "auto_approve",
            "value": "true" if enabled else "false",
        }).execute()
        return True
    except Exception as exc:
        _log.error("set_auto_approve failed: %s", exc)
        return False


# ---------------------------------------------------------------- pnl_log

def fetch_pnl_log(limit: int = 60) -> list[dict]:
    """Return daily P&L rows, most recent first."""
    client = get_supabase_client()
    if client is None:
        return []
    try:
        resp = (
            client.table("pnl_log")
            .select("*")
            .order("trade_date", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def write_pnl_log(row: dict) -> bool:
    """Upsert a daily P&L row (trade_date is the PK)."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("pnl_log").upsert(row).execute()
        return True
    except Exception as exc:
        _log.error("write_pnl_log failed: %s", exc)
        return False


def write_positions(rows: list[dict]) -> bool:
    """Upsert position snapshot rows (PK: trade_date + ticker)."""
    client = get_supabase_client()
    if client is None or not rows:
        return False
    try:
        client.table("positions").upsert(rows).execute()
        return True
    except Exception as exc:
        _log.error("write_positions failed: %s", exc)
        return False


# ---------------------------------------------------------------- generic settings

def get_setting(key: str) -> str | None:
    """Read a key-value setting from the settings table. Returns None if absent."""
    client = get_supabase_client()
    if client is None:
        return None
    try:
        resp = (
            client.table("settings")
            .select("value")
            .eq("key", key)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["value"]
        return None
    except Exception:
        return None


def set_setting(key: str, value: str) -> bool:
    """Upsert a key-value setting in the settings table."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("settings").upsert({"key": key, "value": value}).execute()
        return True
    except Exception as exc:
        _log.error("set_setting(%s) failed: %s", key, exc)
        return False


# ---------------------------------------------------------------- live attribution

def write_live_attribution(rows: list[dict]) -> bool:
    """Upsert live attribution rows (PK: run_date + ticker)."""
    client = get_supabase_client()
    if client is None or not rows:
        return False
    try:
        client.table("live_attribution").upsert(rows).execute()
        return True
    except Exception as exc:
        _log.error("write_live_attribution failed: %s", exc)
        return False


# ---------------------------------------------------------------- cron run log (idempotency)

def check_cron_run(job_name: str, run_date: str) -> bool:
    """Return True if this job already completed for run_date."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        resp = (
            client.table("cron_runs")
            .select("run_date")
            .eq("run_date", run_date)
            .eq("job_name", job_name)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


def write_cron_run(job_name: str, run_date: str) -> bool:
    """Record a completed cron run. Called at end of successful run only."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("cron_runs").upsert({
            "run_date": run_date,
            "job_name": job_name,
        }).execute()
        return True
    except Exception as exc:
        _log.error("write_cron_run failed: %s", exc)
        return False
