"""One-off utility: sync current Alpaca paper positions → Supabase.

Reads live positions and NAV from Alpaca (read-only, no trades) and
writes them to the Supabase positions table and live_nav setting so that
Panel H and Panel J in the dashboard reflect the true book state.

Run this whenever Supabase positions are stale (e.g. after the first
execution run that started from a flat account and wrote empty positions).

Usage:
    python scripts/sync_positions_from_alpaca.py

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


def main() -> int:
    import os

    today = date.today().isoformat()

    # -- Connect to Alpaca (read-only: no orders submitted)
    from execution.alpaca_paper import connect, get_current_positions, get_live_nav
    client = connect(dry_run=False)

    nav = get_live_nav(client)
    logger.info("live NAV from Alpaca: $%.2f", nav)

    positions = get_current_positions(client, dry_run=False)
    logger.info("Alpaca positions: %s", {k: round(v, 2) for k, v in positions.items()})

    if not positions:
        logger.warning("Alpaca reports no open positions for today. Nothing to sync.")
        return 0

    # -- Write to Supabase
    from dashboard.supabase_client import set_setting, write_positions

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

    logger.info("sync complete. Panel H and J will refresh within 5 minutes (cache TTL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
