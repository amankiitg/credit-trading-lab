"""Refresh the cached position snapshot and live_nav from Alpaca -- sprint v9.6.

Why this exists. Both the proposal in Panel H and the next morning's execution are
sized from the cached Supabase snapshot, on purpose, so that what gets approved is
exactly what gets executed. But that snapshot was only ever written at the end of a
successful execution run. A day that was rejected, skipped, or refused for a stale
signal returned before the write, so the basis a proposal was built from could be
arbitrarily old, and execution would then size against that old basis.

The fix is to refresh on every path that could otherwise leave it stale:

  - run_signal refreshes after the close, so the proposal is built from a book at
    most one session old;
  - run_execution refreshes on the reject, no-approval and stale-signal paths, so a
    day that does not trade still advances the basis.

The snapshot is written with share counts as well as notionals, because the drift
check compares shares. See execution/alpaca_paper.check_position_drift.

Nothing here raises. A snapshot refresh is bookkeeping around the real work: it must
never be able to fail a run that has already succeeded, nor one that deliberately
did nothing.

Note on dry-run: this does NOT go through DRY_RUN_DEFAULT, which defaults to true and
makes connect() return no client at all. Reading positions and NAV is not a trading
action, so the read itself needs no dry-run gate; the `dry_run` argument here gates
only the Supabase writes.
"""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger("snapshot")


def refresh_snapshot(
    *,
    run_date: str | None = None,
    dry_run: bool = False,
    log: logging.Logger | None = None,
) -> dict:
    """Read live positions and NAV from Alpaca and write the cached snapshot.

    Returns a report: {"written": bool, "nav": float | None, "tickers": int,
    "skipped": str | None, "failed": bool}. `skipped` names the reason nothing was
    written, and is None when the refresh completed. `failed` separates a real
    failure from a deliberate skip: a dry run is skipped but is not a failure, and
    the summary email shows those two cases very differently.
    """
    log = log or logger
    report: dict = {
        "written": False,
        "nav": None,
        "tickers": 0,
        "skipped": None,
        "failed": False,
    }

    if dry_run:
        report["skipped"] = "dry run"
        log.info("snapshot refresh skipped: dry run (no write, no Alpaca read)")
        return report

    try:
        from dashboard.supabase_client import set_setting, write_positions
        from execution.alpaca_paper import connect, get_live_book, get_live_nav

        # dry_run=False explicitly: this only reads from the broker, and the
        # DRY_RUN_DEFAULT flag defaults to on, which would return no client and
        # silently turn this whole feature into a no-op on the signal cron.
        client = connect(dry_run=False)
        if client is None:
            report["skipped"] = "no Alpaca client"
            report["failed"] = True
            log.warning("snapshot refresh skipped: connect() returned no client")
            return report

        today = run_date or date.today().isoformat()
        nav_live = get_live_nav(client)
        book = get_live_book(client, dry_run=False)

        nav_written = set_setting("live_nav", str(round(nav_live, 2)))

        rows = []
        for ticker, entry in book.items():
            signed_n = entry["notional"]
            rows.append({
                "trade_date": today,
                "ticker": ticker,
                # Dollar basis for the delta math...
                "signed_notional": signed_n,
                # ...and the share count the drift check compares.
                "shares": entry["shares"],
                # Weights against the NAV read at the same moment as the positions.
                "weight": signed_n / nav_live if nav_live > 0 else 0.0,
                "side": "long" if signed_n > 0 else "short",
            })

        rows_written = write_positions(rows) if rows else False
        if not rows:
            log.warning(
                "snapshot refresh: Alpaca reported no universe positions -- wrote "
                "live_nav only, leaving the previous position rows in place"
            )

        report.update(
            written=bool(nav_written or rows_written),
            nav=nav_live,
            tickers=len(rows),
        )
        log.info(
            "snapshot refreshed for %s: live_nav=%.2f, %d position row(s) "
            "(live_nav_written=%s positions_written=%s)",
            today, nav_live, len(rows), nav_written, rows_written,
        )
        return report
    except Exception as exc:
        # Deliberately broad, including the import above: this is bookkeeping and a
        # failure must not propagate into a run whose real work already finished.
        report["skipped"] = f"{type(exc).__name__}: {exc}"
        report["failed"] = True
        log.warning(
            "snapshot refresh failed (%s: %s) -- the cached snapshot is unchanged; "
            "the run is unaffected",
            type(exc).__name__, exc,
        )
        return report
