"""Recompute one session's daily sleeve P&L from the inputs that are stored now.

Step 8 of the signal job writes the session it has just closed and never looks back.
When an input was wrong on the day -- a vendor that had not yet published a payout, a
snapshot taken before a share-count change -- the row it wrote stays wrong, and the only
way to repair it was to re-run the whole job: re-derive the signal, rewrite cron_runs,
and take the trading decisions along with it. That is far too much machine for a
correction that touches four rows.

This script does the minimum instead. It reads the stored inputs (the position snapshot
for the prior session, the cached closes, the cached distributions plus the override
CSV), recomputes the decomposition with the same pure function the job uses, and upserts
the four rows for one date. It touches one table, one date, and nothing that can trade:

  - it never imports or calls the signal or execution jobs, and never calls Alpaca
  - it never writes cron_runs
  - it writes nothing at all for a session it cannot compute, or one that does not
    reconcile -- the gate is the same gate, not a copy of it

By default it writes. Pass --dry-run to print the recomputed rows beside the stored ones
and change nothing.

    python scripts/rerun_attribution.py --date 2026-11-02 --dry-run
    python scripts/rerun_attribution.py --date 2026-11-02

Check the date twice before running it for real: the row this replaces may have been
correct, and nothing here can tell the difference between a correction and a mistake.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from dashboard.supabase_client import (
    fetch_daily_sleeve_pnl,
    fetch_position_snapshot,
    write_daily_sleeve_pnl,
)
from risk.sleeve_pnl import (
    STATUS_OK,
    compute_sleeve_pnl,
)
from signals.dividends import DISTRIBUTIONS_OVERRIDE, load_distributions_override
from signals.dividends import load_dividend_matrix
from signals.etf_universe import UNIVERSE, load_universe_close


def _refused(as_of_date: str, reason: str) -> dict:
    """The report for a session that is not writable, with the reason it is not."""
    return {
        "trade_date": as_of_date,
        "status": "refused",
        "reason": reason,
        "rows": [],
        "written": False,
        "prior_date": None,
        "book_total": None,
        "residual": None,
        "tolerance": None,
    }


def _stored_rows(as_of_date: str) -> dict[str, dict]:
    """What the table holds for this session right now, keyed by sleeve."""
    try:
        rows = fetch_daily_sleeve_pnl()
    except Exception:
        return {}
    return {
        str(row.get("sleeve")): row
        for row in rows
        if str(row.get("trade_date")) == as_of_date
    }


def _apply_override(
    dividends: dict[str, float], as_of_date: str, override_path
) -> dict[str, float]:
    """Overlay the hand-curated distributions for this session on the cached matrix.

    The override exists because a vendor can be late. The nightly job merges it inside
    the fetch, which then writes the parquets; a rerun reads the parquets that already
    exist, so it has to apply the same overlay itself or it would recompute the old
    answer for the very session the override was added to fix.
    """
    overrides = load_distributions_override(override_path)
    day = pd.Timestamp(as_of_date)
    out = dict(dividends)
    for ticker, series in overrides.items():
        if day in series.index:
            out[ticker] = float(series.loc[day])
    return out


def recompute(
    as_of_date: str,
    *,
    write: bool = False,
    close: pd.DataFrame | None = None,
    override_path=DISTRIBUTIONS_OVERRIDE,
) -> dict:
    """Recompute and optionally upsert one session's four sleeve rows.

    Returns a report: the status, the reason when it refused, the rows that would be
    written (or were), and the stored rows they replace.
    """
    close = load_universe_close(UNIVERSE, column="close") if close is None else close
    sessions = {str(ts.date()): ts for ts in close.index}

    if as_of_date not in sessions:
        return _refused(as_of_date, f"the close cache has no row for {as_of_date}")

    # The prior session of THIS date, not of the whole cache: the nightly job always
    # measures the last session in the cache, and on a rerun of an older date those are
    # two different sessions.
    position = close.index.get_loc(sessions[as_of_date])
    if position == 0:
        return _refused(as_of_date, f"no earlier session in the close cache for {as_of_date}")
    prior_date = str(close.index[position - 1].date())

    positions = fetch_position_snapshot(prior_date)
    if not positions:
        return _refused(
            as_of_date,
            f"no position snapshot for {prior_date}, so the session's book is unknown",
        )

    close_prev = close.loc[sessions[prior_date]].dropna().to_dict()
    close_now = close.loc[sessions[as_of_date]].dropna().to_dict()
    dividends = load_dividend_matrix(UNIVERSE, close.index).loc[sessions[as_of_date]].to_dict()
    dividends = _apply_override(dividends, as_of_date, override_path)

    result = compute_sleeve_pnl(
        trade_date=as_of_date,
        prior_date=prior_date,
        positions=positions,
        close_prev=close_prev,
        close_now=close_now,
        dividends=dividends,
    )

    if result.status != STATUS_OK:
        refusal = _refused(as_of_date, result.reason or result.status)
        refusal["prior_date"] = prior_date
        return refusal

    report = {
        "trade_date": as_of_date,
        "status": STATUS_OK,
        "reason": None,
        "rows": result.rows,
        "prior_date": prior_date,
        "book_total": result.book_gross_pnl,
        "residual": result.residual,
        "tolerance": result.tolerance,
        "stored": _stored_rows(as_of_date),
        "written": False,
    }

    if write:
        report["written"] = bool(write_daily_sleeve_pnl(result.rows))

    return report


def _fmt(value) -> str:
    return "  --  " if value is None else f"{float(value):>9,.2f}"


def render(report: dict) -> str:
    """The report as a short table, stored beside recomputed, per sleeve."""
    lines = [
        f"session {report['trade_date']}  prior {report.get('prior_date') or '--'}"
        f"  status {report['status']}"
    ]
    if report["status"] != STATUS_OK:
        lines.append(f"  not written: {report['reason']}")
        return "\n".join(lines)

    lines.append(
        f"  book {_fmt(report['book_total'])}  "
        f"residual {_fmt(report['residual'])}  tolerance {_fmt(report['tolerance'])}"
    )
    lines.append(
        f"  {'sleeve':<10} {'stored carry':>13} {'new carry':>11}"
        f" {'stored price':>13} {'new price':>11} {'stored total':>13} {'new total':>11}"
    )
    stored = report.get("stored") or {}
    for row in report["rows"]:
        old = stored.get(row["sleeve"]) or {}
        lines.append(
            f"  {row['sleeve']:<10} {_fmt(old.get('carry_pnl')):>13} {_fmt(row['carry_pnl']):>11}"
            f" {_fmt(old.get('price_pnl')):>13} {_fmt(row['price_pnl']):>11}"
            f" {_fmt(old.get('total')):>13} {_fmt(row['total']):>11}"
        )
    if report.get("written"):
        lines.append("  written: 4 rows upserted")
    elif report["status"] == STATUS_OK:
        lines.append("  written: nothing (dry run)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute one session's daily sleeve P&L from stored inputs."
    )
    parser.add_argument("--date", required=True, help="session to recompute, YYYY-MM-DD")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the recomputed rows beside the stored ones and write nothing",
    )
    args = parser.parse_args(argv)

    try:
        as_of_date = date.fromisoformat(args.date).isoformat()
    except ValueError:
        print(f"--date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
        return 2

    report = recompute(as_of_date, write=not args.dry_run)
    print(render(report))

    if report["status"] != STATUS_OK:
        return 1
    if not args.dry_run and not report.get("written"):
        print("  the write was rejected by Supabase", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
