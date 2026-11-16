"""Backfill daily_sleeve_pnl.execution_pnl for sessions that have fills but no value.

Why this exists: the execution component was added in sprint v9.9 and only started
storing its per-sleeve split once the column existed. Every session before that has the
traded part of its P&L measured in the e-mail and in the reconciliation but absent from
the table, so the dashboard bridge shows those sessions as "not stored" and their traded
part sits in the residual.

What it does, per session in the window:

  fills  -- the broker's FILL activities for that session (read-only GET)
  close  -- the session's UNADJUSTED close, from the same fetch run_signal uses
  split  -- shares traded x (close - fill), grouped by the sleeve of the ticker

and then writes ONLY the execution_pnl column of daily_sleeve_pnl. Every other column is
left exactly as it is: this is a repair of one missing measurement, not a recomputation of
the book, and a re-run of a session that reconciled must not disturb the numbers that
reconciled.

Dry run by default. --apply is required to write, and --window bounds the sessions.

    .venv/bin/python scripts/backfill_execution_pnl.py                  # print the table
    .venv/bin/python scripts/backfill_execution_pnl.py --apply          # write it

The rows this can touch are dumped to backups/ before any write, because the only
irreversible thing here is replacing a value that was already stored.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from dashboard.supabase_client import (
    execution_column_exists,
    fetch_daily_sleeve_pnl,
    get_supabase_client,
)
from execution.alpaca_paper import connect, fetch_session_fills
from risk.execution_pnl import execution_pnl
from signals.etf_universe import ASSET_CLASS, UNIVERSE
from signals.load import fetch as fetch_closes

DEFAULT_START = "2026-10-01"
DEFAULT_END = "2026-11-06"


def _closes(start: str, end: str) -> pd.DataFrame:
    frames = fetch_closes(list(UNIVERSE), start, end)
    matrix = pd.DataFrame({t: f["close"] for t, f in frames.items()}).sort_index()
    matrix.index = pd.to_datetime(matrix.index)
    return matrix


def build_plan(start: str, end: str, *, log=print) -> list[dict]:
    """The sessions that need a value and what it would be. Reads only."""
    stored = fetch_daily_sleeve_pnl(limit=4000, raise_on_error=True)
    by_session: dict[str, list[dict]] = {}
    for row in stored:
        session = str(row.get("trade_date"))
        if start <= session <= end:
            by_session.setdefault(session, []).append(row)

    client = connect(dry_run=False)
    if client is None:
        raise SystemExit("no Alpaca client: ALPACA_PAPER_* must be set")

    close = _closes(start, end)
    plan: list[dict] = []
    for session in sorted(by_session):
        rows = by_session[session]
        # A session that already has a value is not touched: it was measured by the run
        # that wrote it, and this repair must not overwrite a measurement with a
        # reconstruction from the same inputs months later.
        if all(row.get("execution_pnl") is not None for row in rows):
            continue
        stamp = pd.Timestamp(session)
        if stamp not in close.index:
            log(f"{session}: no close for that session, skipped")
            continue
        fills = fetch_session_fills(client, session)
        if not fills:
            plan.append({"session": session, "fills": 0, "total": 0.0, "by_sleeve": {},
                         "skipped": [], "reason": "no fills"})
            continue
        pnl = execution_pnl(
            fills, close.loc[stamp].dropna().to_dict(),
            sleeve_of=ASSET_CLASS, session=session,
        )
        plan.append({
            "session": session,
            "fills": pnl.fill_count,
            "total": pnl.total,
            "by_sleeve": pnl.by_sleeve,
            "skipped": pnl.skipped,
            "reason": None,
        })
        log(
            f"{session}: {pnl.fill_count} fill(s), "
            f"{pnl.total:+.2f} total {dict((k, round(v, 2)) for k, v in pnl.by_sleeve.items())}"
        )
    return plan


def apply_plan(plan: list[dict], *, log=print) -> int:
    """Write execution_pnl for the planned sessions. Returns the number of rows written."""
    client = get_supabase_client()
    if client is None:
        raise SystemExit("no Supabase client: SUPABASE_URL/SUPABASE_SECRET_KEY must be set")
    written = 0
    for entry in plan:
        if entry.get("reason") == "no fills" and entry.get("fills") == 0:
            pass  # a genuinely quiet session is still a measured zero, not a null
        by_sleeve = entry.get("by_sleeve") or {}
        for sleeve in ("equity", "rates", "credit", "commodity"):
            resp = (
                client.table("daily_sleeve_pnl")
                .update({"execution_pnl": float(by_sleeve.get(sleeve, 0.0))})
                .eq("trade_date", entry["session"])
                .eq("sleeve", sleeve)
                .execute()
            )
            written += len(resp.data or [])
        log(f"{entry['session']}: wrote execution_pnl for 4 sleeve(s)")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = parser.parse_args(argv)

    if not execution_column_exists():
        raise SystemExit(
            "daily_sleeve_pnl.execution_pnl does not exist -- run "
            "sprints/v9.9/supabase_schema.sql first"
        )

    plan = build_plan(args.start, args.end)
    rows = 4 * len(plan)
    total = sum(entry.get("total") or 0.0 for entry in plan)
    print()
    print(f"{len(plan)} session(s), {rows} row(s), execution P&L {total:+.2f}")

    if not plan:
        return 0
    if not args.apply:
        print("dry run: nothing written. Pass --apply to write.")
        return 0

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f"execution_pnl_backfill_{date.today().isoformat()}.json"
    # Back the CURRENT values up before replacing them: the only irreversible step here
    # is overwriting a number, and a null is still a number the table can be restored to.
    stored = [
        row for row in fetch_daily_sleeve_pnl(limit=4000, raise_on_error=True)
        if str(row.get("trade_date")) in {entry["session"] for entry in plan}
    ]
    backup.write_text(json.dumps(stored, indent=1, default=str))
    print(f"backed up {len(stored)} row(s) to {backup}")

    written = apply_plan(plan)
    print(f"wrote {written} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
