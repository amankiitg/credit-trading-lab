"""One-off backfill of public.close_equity from the Alpaca portfolio history.

Run ONCE, after the table exists (sprints/v9.9/supabase_schema.sql), from the project
root:

    .venv/bin/python scripts/backfill_close_equity.py --dry-run   # read only
    .venv/bin/python scripts/backfill_close_equity.py             # write

Why this exists: the evening job started storing its close read in close_equity only
when the table was added. The dashboard's account-value chart wants the series from
2026-10-01, and no earlier run kept it. This walks the same read-only GET the
reconciliation makes (execution.alpaca_paper.fetch_equity_history) back to that date
and stores what it finds.

Three things it deliberately does NOT do:

  - it never places, replaces or cancels an order. The only broker call is the
    portfolio-history GET, the same one risk/execution_pnl.py reconciles with;
  - it writes to close_equity and to nothing else. No decision, signal, sleeve or
    position row is touched;
  - it never prints a credential. SUPABASE_SECRET_KEY is read by the app's own
    dashboard.supabase_client (the same client the dashboard and the crons use) and
    the output names only the date range and the row count.

The stamp convention is the reconciliation's: the endpoint labels a close with the
FOLLOWING calendar day, so the close of session D is the point labelled D+1.
risk.execution_pnl.published_closes() turns those labels back into sessions; a
session with no label of its own is skipped rather than filled from a later point.

The nightly run keeps the table current from here on, so this is safe to re-run (the
upsert is on session_date) but it is not scheduled.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Runnable as ".venv/bin/python scripts/backfill_close_equity.py" from the repo root
# without an editable install: the interpreter puts scripts/ -- and only scripts/ -- at
# sys.path[0], so execution/, risk/ and dashboard/ are not importable from here. The
# cron jobs never hit that because render.yaml builds them with "pip install -e .";
# this one-off, documented to run from a checkout, may not be installed. Adding the
# project root is the bootstrap scripts/measure_dashboard_memory.py already uses.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_close_equity")

# The dashboard's period control starts here, and the stored series should reach back
# at least as far as the chart can be asked to draw.
DEFAULT_START: str = "2026-10-01"

# Each walk step reads about twelve calendar days. A cap keeps a broker that ignores
# the request window from turning this into an unbounded loop.
MAX_WINDOWS: int = 24

# One value for both writers: it describes where the number came from, not which job
# wrote it. fetched_at is what distinguishes a backfilled row from tonight's.
SOURCE: str = "alpaca_portfolio_history"


def _read_history(client, start: str, end: str) -> dict[str, float]:
    """End-of-day equity stamps covering start..end, walking the read backwards."""
    from execution.alpaca_paper import fetch_equity_history

    equity: dict[str, float] = {}
    anchor = end
    for _ in range(MAX_WINDOWS):
        window = fetch_equity_history(client, anchor)
        if not window:
            break
        equity.update(window)
        earliest = min(window)
        if earliest <= start:
            break
        next_anchor = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        if next_anchor >= anchor:
            # No progress: the endpoint returned the same window for an earlier
            # anchor. Stop rather than spin until MAX_WINDOWS.
            break
        anchor = next_anchor
    return equity


def collect_closes(
    client, *, start: str = DEFAULT_START, end: str | None = None
) -> dict[str, float]:
    """{session: close} for every session from start through the latest published close.

    `end` bounds the sessions considered (default today); the latest row is whatever
    the history has published, which on a session evening is D-1.
    """
    from execution.calendar_utils import nyse_sessions
    from risk.execution_pnl import published_closes

    end = end or date.today().isoformat()
    if end < start:
        return {}
    equity = _read_history(client, start, end)
    if not equity:
        return {}
    sessions = nyse_sessions(start, end)
    return published_closes(equity, sessions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="first session to fill")
    parser.add_argument(
        "--end", default=None, help="last session to consider (default: today)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the history and report the range; write nothing",
    )
    args = parser.parse_args(argv)

    from execution.alpaca_paper import connect

    client = connect(dry_run=False)
    if client is None:
        logger.error(
            "no Alpaca client: set ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY"
        )
        return 1

    closes = collect_closes(client, start=args.start, end=args.end)
    if not closes:
        logger.error(
            "the portfolio history had no published close between %s and %s",
            args.start, args.end or date.today().isoformat(),
        )
        return 1

    first, last = min(closes), max(closes)
    if args.dry_run:
        logger.info(
            "dry run: %d close(s), %s..%s -- nothing written", len(closes), first, last
        )
        return 0

    from dashboard.supabase_client import write_close_equity

    if not write_close_equity(closes, source=SOURCE):
        logger.error(
            "close_equity write failed (%d row(s), %s..%s) -- is the table there? "
            "see sprints/v9.9/supabase_schema.sql",
            len(closes), first, last,
        )
        return 1
    logger.info(
        "wrote %d close_equity row(s), %s..%s (source=%s)",
        len(closes), first, last, SOURCE,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
