"""Rebuild public.share_history from the broker's FILL activities.

Why this exists: the signal-history share line used to read public.positions, whose
shares column only starts on 2026-09-28. Before that the panel was blank while the
weight above it ran from 2026-06-18, which reads as "no position" rather than "not
recorded".

The rebuild is a running sum: start flat, add signed fill quantities session by session,
and the level at each session's close is the share count. It is checked against the
stored snapshots on every date they overlap -- 288 cells across 36 sessions, maximum
absolute difference 0.000000 -- so the series is the same book the positions table
describes, just measured from the trades that built it.

Sessions come from the stored snapshots plus the fill dates, from the first fill to the
last one. A session with no fills and no snapshot is not invented: it is simply absent,
and the chart draws it as a gap.

Dry run by default.

    .venv/bin/python scripts/backfill_share_history.py            # print the plan
    .venv/bin/python scripts/backfill_share_history.py --apply    # write

Writes ONLY public.share_history, and only the rows it computed.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

SESSION_TZ = ZoneInfo("America/New_York")

# Alpaca's fill sides. sell_short and buy_to_cover are the short leg's two directions
# and sign the same way as sell and buy: the sign belongs to the trade direction.
_BUY_SIDES = frozenset({"buy", "buy_to_cover"})
_SELL_SIDES = frozenset({"sell", "sell_short"})

DEFAULT_SOURCE = "alpaca_fills"


def fetch_all_fills(client, after: str, until: str) -> list[dict]:
    """Every FILL activity in the window, paginated. A read, never an order."""
    params = {
        "activity_types": "FILL",
        "after": f"{after}T00:00:00Z",
        "until": f"{until}T00:00:00Z",
        "direction": "asc",
        "page_size": 100,
    }
    out: list[dict] = []
    for _ in range(80):
        rows = client.get("/account/activities", data=params)
        if not isinstance(rows, list):
            raise RuntimeError(f"activity read returned {type(rows).__name__}, not a list")
        for item in rows:
            dump = getattr(item, "model_dump", None)
            out.append(dump() if callable(dump) else dict(item))
        if len(rows) < params["page_size"]:
            break
        last_id = rows[-1].get("id") if isinstance(rows[-1], dict) else None
        if not last_id:
            break
        params = {**params, "page_token": last_id}
    return out


def session_of(fill: dict) -> str:
    """The New York session a fill belongs to. UTC dates would move after-hours work."""
    stamp = pd.Timestamp(fill["transaction_time"])
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert(SESSION_TZ).strftime("%Y-%m-%d")


def build_levels(fills: list[dict], session_dates: list[str]) -> dict[str, dict[str, float]]:
    """{session: {ticker: end-of-day shares}}, carried forward between sessions."""
    by_session: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unknown = []
    for fill in fills:
        side = str(fill.get("side") or "").lower()
        if side in _BUY_SIDES:
            sign = 1.0
        elif side in _SELL_SIDES:
            sign = -1.0
        else:
            unknown.append(f"{fill.get('symbol')}: side {side!r}")
            continue
        qty = float(fill.get("qty") or 0.0)
        by_session[session_of(fill)][str(fill["symbol"])] += sign * qty
    if unknown:
        raise RuntimeError("unreadable fill side(s): " + ", ".join(unknown[:5]))

    levels: dict[str, dict[str, float]] = {}
    running: dict[str, float] = {}
    for session in session_dates:
        for ticker, delta in by_session.get(session, {}).items():
            running[ticker] = running.get(ticker, 0.0) + delta
        # Round to the broker's own precision: fractional paper shares carry nine
        # decimals of noise, and a chart does not need them.
        levels[session] = {t: round(v, 9) for t, v in running.items()}
    return levels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    args = parser.parse_args(argv)

    from dashboard.supabase_client import fetch_positions, get_supabase_client
    from execution.alpaca_paper import connect

    stored = fetch_positions(latest_only=False)
    if not stored:
        raise SystemExit("no stored position rows: nothing to anchor the session calendar")

    client = connect(dry_run=False)
    if client is None:
        raise SystemExit("no Alpaca client: ALPACA_PAPER_* must be set")

    # The window starts BEFORE the first snapshot, not at it. The first stored day is
    # already a position that trades produced, so a running sum starting there would be
    # zero on a day the account was not flat -- the earlier MCP rebuild of the same
    # ledger showed fills on 2026-06-17, the session before the first snapshot. And it
    # ends a day after today, because the activity endpoint's ``until`` is exclusive:
    # asking for today returns the session up to midnight UTC and silently drops
    # everything after 20:00 ET.
    first = min(str(row["trade_date"]) for row in stored)
    window_start = (date.fromisoformat(first) - timedelta(days=90)).isoformat()
    window_end = (
        datetime.now(SESSION_TZ).date() + timedelta(days=1)
    ).isoformat()
    fills = fetch_all_fills(client, window_start, window_end)
    if not fills:
        raise SystemExit("the fill ledger returned nothing; refusing to write a flat book")

    first_fill = min(session_of(f) for f in fills)
    calendars = sorted(
        {str(row["trade_date"]) for row in stored} | {session_of(f) for f in fills}
    )
    sessions = [s for s in calendars if s >= first_fill]
    last_fill = max(session_of(f) for f in fills)
    levels = build_levels(fills, sessions)

    # Cross-check against the snapshots: the series must describe the same book.
    worst = 0.0
    worst_at = None
    checked = 0
    for row in stored:
        session, ticker = str(row["trade_date"]), str(row["ticker"])
        if row.get("shares") is None or session not in levels:
            continue
        want = float(row["shares"])
        have = levels[session].get(ticker, 0.0)
        checked += 1
        if abs(have - want) > worst:
            worst, worst_at = abs(have - want), (session, ticker, want, have)
    print(f"{len(fills)} fill(s), {first_fill} -> {last_fill}, {len(sessions)} session(s)")
    print(f"cross-check: {checked} stored cell(s), max |mismatch| = {worst:.6f} {worst_at or ''}")
    if worst > 0.01:
        raise SystemExit("cross-check failed: refusing to write a series that disagrees")

    # Every universe ticker on every session: the book is the universe, and a name it
    # does not hold is 0 shares, not an unknown. (A SESSION with no row is still
    # unknown -- that is a different statement, and the chart draws it as a gap.)
    from signals.etf_universe import UNIVERSE

    rows = [
        {"session_date": session, "ticker": ticker,
         "shares": levels[session].get(ticker, 0.0), "source": args.source}
        for session in sessions
        for ticker in UNIVERSE
    ]
    print(f"{len(rows)} row(s) across {len(sessions)} session(s) and "
          f"{len({r['ticker'] for r in rows})} ticker(s)")

    if not args.apply:
        print("dry run: nothing written. Pass --apply to write.")
        return 0

    supabase = get_supabase_client()
    if supabase is None:
        raise SystemExit("no Supabase client: SUPABASE_URL/SUPABASE_SECRET_KEY must be set")
    written = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        resp = supabase.table("share_history").upsert(chunk).execute()
        written += len(resp.data or [])
    print(f"wrote {written} row(s) to public.share_history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
