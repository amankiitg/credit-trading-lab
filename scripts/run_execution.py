"""Morning execution cron -- sprint v8.6.

Schedule: "30 14 * * 1-5" UTC
  = 10:30 EDT (UTC-4, summer) = 09:30 EST (UTC-5, winter)
  Both fall in the clean execution window after the 09:30 ET NYSE open.

What it does:
  1. NYSE calendar check -- skip if today is not a trading day.
  2. Idempotency check -- exit 0 if this job already ran for today.
  3. Read the decision from Supabase for as_of_date (most recent close).
  4. Decide whether to execute (based on decision + auto_approve setting).
  5. Connect to Alpaca paper account.
  6. Get live NAV, current positions, run v8.2 signal.
  7. Compute delta orders, apply guards (with live NAV).
  8. Submit orders: longs via notional, shorts via whole-share qty.
  9. Poll fills, build fill records, mark costs.
  10. Close any dust positions.
  11. Reconcile. Run feed_attribution. Write fills to Supabase.
  12. Record completed run in cron_runs.

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY
  ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY

The dashboard service must NOT have the Alpaca keys. This script is the
only path to the paper account.
"""

from __future__ import annotations

import logging
import sys
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_execution")


def main() -> int:
    today = date.today().isoformat()

    # -- 1. NYSE calendar check
    from execution.calendar_utils import is_trading_day
    if not is_trading_day(today):
        logger.info("skipping: NYSE closed on %s", today)
        return 0

    # -- 2. Idempotency
    from execution.calendar_utils import check_already_ran, record_run
    if check_already_ran("run_execution", today):
        logger.info("already ran for %s -- exit 0 (idempotent)", today)
        return 0

    # -- 3. Load signal output written by run_signal.py (no yfinance call needed).
    #        Falls back to ingest() only if Supabase data is missing/stale.
    import json
    from signals.etf_universe import UNIVERSE
    from dashboard.supabase_client import get_setting

    _stored_date    = get_setting("signal_as_of_date")
    _stored_weights = get_setting("signal_target_weights")
    _stored_prices  = get_setting("signal_close_prices")

    if _stored_date and _stored_weights and _stored_prices:
        as_of_date   = _stored_date
        target_weights: dict[str, float] = json.loads(_stored_weights)
        close_prices:   dict[str, float] = json.loads(_stored_prices)
        logger.info("loaded signal from Supabase: as_of_date=%s", as_of_date)
    else:
        logger.warning("signal not in Supabase -- falling back to yfinance ingest")
        from signals.etf_universe import ingest, load_universe_close
        ingest(UNIVERSE)
        close = load_universe_close()
        as_of_date = str(close.index[-1].date())
        close_prices = {
            t: float(close[t].iloc[-1])
            for t in UNIVERSE
            if t in close.columns and not close[t].iloc[-1] != close[t].iloc[-1]
        }
        target_weights = {}  # computed below in step 6
    logger.info("as_of_date: %s", as_of_date)

    # -- 4. Decision gate
    from dashboard.supabase_client import (
        fetch_decision_for_date,
        get_auto_approve,
        set_setting,
        write_cron_run,
        write_live_attribution,
        write_pnl_log,
        write_positions,
    )
    from execution.alpaca_paper import DRY_RUN_DEFAULT

    decision = fetch_decision_for_date(as_of_date)
    auto_approve = get_auto_approve()

    if decision == "reject":
        logger.info("decision=reject for %s -- skipping execution", as_of_date)
        record_run("run_execution", today)
        return 0

    if not auto_approve and decision != "approve":
        logger.info(
            "auto_approve=False and no explicit approve for %s -- skipping",
            as_of_date,
        )
        record_run("run_execution", today)
        return 0

    logger.info(
        "executing: decision=%s auto_approve=%s for %s",
        decision, auto_approve, as_of_date,
    )

    # -- 5. Connect to Alpaca
    from execution.alpaca_paper import (
        DRY_RUN_DEFAULT,
        close_dust_positions,
        compute_delta_orders,
        apply_guards,
        build_fill_records,
        connect,
        feed_attribution,
        get_current_positions,
        get_live_nav,
        mark_costs,
        poll_fills,
        reconcile,
        submit_orders,
    )

    dry_run = DRY_RUN_DEFAULT
    client = connect(dry_run=dry_run)
    logger.info("dry_run=%s", dry_run)

    # -- 6. Live NAV and current positions
    nav = get_live_nav(client) if not dry_run else 100_000.0
    logger.info("nav=%.2f", nav)

    # Write live NAV to Supabase so dashboard sidebar can display it
    set_setting("live_nav", str(round(nav, 2)))

    current_notionals = get_current_positions(client, dry_run=dry_run)
    logger.info("current positions: %s", current_notionals)

    # -- Run v8.2 signal only if not already loaded from Supabase
    if not target_weights:
        from signals.etf_universe import load_universe_close
        from signals.trend_signal import (
            apply_rebalance_control,
            compute_trend,
            shift_to_next_day,
            to_position_matrix,
        )
        close = load_universe_close()
        desired = to_position_matrix(
            compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
        )
        held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
        target = shift_to_next_day(held)
        target_weights = {
            t: float(target.iloc[-1].get(t) or 0.0)
            for t in UNIVERSE
        }
    logger.info("target weights: %s", {k: round(v, 4) for k, v in target_weights.items()})

    # -- 7. Compute orders, apply guards
    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=nav)
    guarded = apply_guards(orders, dry_run=dry_run, _nav=nav)

    pending_count = sum(1 for o in guarded if o.guard_status == "PENDING")
    logger.info(
        "orders: %d total, %d pending, %d rejected",
        len(guarded), pending_count, len(guarded) - pending_count,
    )

    # -- 8. Submit orders (longs notional, shorts whole-share qty)
    submitted_ids = submit_orders(client, guarded, close_prices=close_prices)

    # -- 9. Poll fills
    real_ids = [oid for oid in submitted_ids if oid not in ("", "SKIPPED_QTY_ZERO")]
    fill_data = poll_fills(client, real_ids) if real_ids else {}

    fills = build_fill_records(guarded, submitted_ids, fill_data)

    # Mark costs using post-execution short notionals
    short_notionals = {
        t: abs(n) for t, n in current_notionals.items() if n < 0
    }
    fills = mark_costs(fills, current_short_notionals=short_notionals)

    # -- 10. Dust cleanup
    dust_closed = close_dust_positions(client, dry_run=dry_run)
    if dust_closed:
        logger.info("dust cleanup: closed %s", dust_closed)

    # -- 11. Reconcile
    run_date_obj = date.fromisoformat(today)
    report = reconcile(guarded, fills, run_date=run_date_obj)
    logger.info(
        "reconcile: %d legs, %d flagged discrepancies",
        report["total_fills_captured"], report["flagged_discrepancies"],
    )

    # feed_attribution: append filled rows to attribution.parquet + Supabase
    n_appended = feed_attribution(fills, close_prices, run_date=run_date_obj, nav=nav)
    logger.info("feed_attribution: %d rows appended", n_appended)

    # Write live attribution rows to Supabase as well
    if n_appended > 0:
        import pandas as pd
        from pathlib import Path
        p = Path("data/processed/attribution.parquet")
        if p.exists():
            attr_df = pd.read_parquet(p)
            today_rows = attr_df[attr_df["date"].astype(str).str.startswith(today)]
            live_rows = [
                {
                    "run_date": today,
                    "ticker": r["ticker"],
                    "asset_class": r["asset_class"],
                    "weight": r["weight"],
                    "pnl": r["pnl"],
                    "carry": r["carry"],
                    "price_change": r["price_change"],
                    "gross_pnl": r["gross_pnl"],
                    "net_pnl": r["net_pnl"],
                    "turnover_cost": r["turnover_cost"],
                    "borrow_cost": r["borrow_cost"],
                }
                for _, r in today_rows.iterrows()
            ]
            write_live_attribution(live_rows)

    # Write positions snapshot to Supabase -- fetch AFTER fills so the first
    # run (flat account before trades) still records real post-fill positions.
    post_notionals = get_current_positions(client, dry_run=dry_run)
    logger.info("post-trade positions: %s", post_notionals)
    position_rows = []
    for ticker, signed_n in post_notionals.items():
        position_rows.append({
            "trade_date": today,
            "ticker": ticker,
            "signed_notional": signed_n,
            "weight": signed_n / nav if nav > 0 else 0.0,
            "side": "long" if signed_n > 0 else "short",
        })
    if position_rows:
        write_positions(position_rows)

    # Write P&L log row
    total_gross = sum(f.filled_notional for f in fills if f.status == "FILLED")
    total_net_pnl = -sum(f.simulated_cost for f in fills if f.status == "FILLED")
    total_cost = sum(f.simulated_cost for f in fills if f.status == "FILLED")

    write_pnl_log({
        "trade_date": today,
        "gross_pnl": round(total_gross, 4),
        "net_pnl": round(total_net_pnl, 4),
        "turnover_cost": round(total_cost, 4),
        "borrow_cost": 0.0,
    })

    # -- 12. Record run
    record_run("run_execution", today)
    logger.info("run_execution complete for %s", today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
