"""State reconciliation: Alpaca paper (reality) vs the Supabase positions cache.

Reads live positions and NAV from Alpaca (read-only, this script never submits
an order), reads the cached Supabase snapshot, prints the per-ticker diff, and
with --apply overwrites the cache with Alpaca's actual book under today's
trade_date so that Panel H and Panel J reflect the true book state.

Run this whenever Supabase positions are stale: after a crashed execution run
(record_run only fires at the very end, so a mid-run crash leaves no snapshot),
after the first execution run from a flat account, or after any manual change to
the paper account.

Default is report-only. The write requires an explicit --apply.

Usage:
    python scripts/sync_positions_from_alpaca.py            # report the diff only
    python scripts/sync_positions_from_alpaca.py --apply     # correct the cache

Env vars required:
    ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY
    SUPABASE_URL, SUPABASE_SECRET_KEY
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("sync_positions")

# Load .env if present (local runs)
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            import os
            os.environ.setdefault(k.strip(), v.strip())


def main(argv: list[str] | None = None) -> int:
    import os

    if argv is None:
        argv = sys.argv[1:]
    dry_run = "--apply" not in argv

    today = date.today().isoformat()

    # -- Connect to Alpaca (read-only: no orders submitted)
    from execution.alpaca_paper import (
        DELTA_MIN_NOTIONAL,
        connect,
        diff_positions,
        get_current_positions,
        get_live_nav,
    )
    client = connect(dry_run=False)

    nav = get_live_nav(client)
    logger.info("live NAV from Alpaca: $%.2f", nav)

    positions = get_current_positions(client, dry_run=False)

    # -- Cached Supabase snapshot (latest trade_date) for comparison
    from dashboard.supabase_client import fetch_positions

    cached_rows = fetch_positions(latest_only=True)
    cached_date = cached_rows[0]["trade_date"] if cached_rows else None
    cached_notionals = {
        r["ticker"]: float(r["signed_notional"]) for r in cached_rows
    }
    logger.info(
        "cached snapshot: trade_date=%s (%d rows)", cached_date, len(cached_rows)
    )

    # -- Diff: Alpaca (reality) vs Supabase (cache)
    diff_rows = diff_positions(positions, cached_notionals)

    logger.info("reconciliation diff for %s (live minus cached):", today)
    logger.info(
        "  {:6s} {:>16s} {:>18s} {:>14s}  {}".format(
            "ticker", "Alpaca(live)", "Supabase(cached)", "diff", "material"
        )
    )
    for row in diff_rows:
        logger.info(
            "  {:6s} {:>16,.2f} {:>18,.2f} {:>14,.2f}  {}".format(
                row["ticker"], row["live"], row["cached"], row["diff"],
                "YES" if row["material"] else "no",
            )
        )

    material = [r for r in diff_rows if r["material"]]
    logger.info(
        "%d of %d tickers differ by at least DELTA_MIN_NOTIONAL ($%.0f)",
        len(material), len(diff_rows), DELTA_MIN_NOTIONAL,
    )

    if dry_run:
        logger.info(
            "report-only (default): no Supabase write. Pass --apply to correct the cache."
        )
        return 0

    # -- Correct the cache: Alpaca's actual book under today's trade_date
    from dashboard.supabase_client import set_setting, write_positions

    if not positions:
        logger.warning(
            "Alpaca reports no open positions. Refusing to write an empty "
            "snapshot as a correction; a genuinely flat book is recorded by "
            "the execution job's own path."
        )
        return 0

    set_setting("live_nav", str(round(nav, 2)))
    logger.info("updated live_nav in Supabase: %.2f", nav)

    position_rows = []
    for ticker, signed_n in positions.items():
        position_rows.append({
            "trade_date": today,
            "ticker": ticker,
            "signed_notional": signed_n,
            "weight": signed_n / nav if nav > 0 else 0.0,
            "side": "long" if signed_n > 0 else "short",
        })

    ok = write_positions(position_rows)
    if ok:
        logger.info("wrote %d position rows to Supabase for %s", len(position_rows), today)
    else:
        logger.error("write_positions failed -- check Supabase credentials")
        return 1

    # -- Verify the correction actually landed (read back, do not trust the upsert)
    verify_notionals = {
        r["ticker"]: float(r["signed_notional"])
        for r in fetch_positions(latest_only=True)
    }
    residual = diff_positions(positions, verify_notionals)
    worst = max((abs(r["diff"]) for r in residual), default=0.0)
    if worst >= 1.0:
        logger.error(
            "verification FAILED: Supabase still differs from Alpaca by up to $%.2f",
            worst,
        )
        return 1
    logger.info(
        "verified: Supabase matches Alpaca on %d tickers (max residual $%.4f)",
        len(residual), worst,
    )

    logger.info("sync complete. Panel H and J will refresh within 5 minutes (cache TTL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
