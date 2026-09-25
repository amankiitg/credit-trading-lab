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
  7b. Pre-check the Alpaca shortable flag for any name the signal wants short.
      A non-shortable name has its sell_to_open leg skipped and logged; legs
      that reduce an existing position still go through.
  8. Submit orders one leg at a time: longs via notional, shorts via whole-share
      qty. A rejected leg is classified, logged with a reason code, and skipped,
      so one bad order cannot abort the run. A transport failure (unknown
      submission state) halts submission cleanly instead.
  9. Poll fills, build fill records, mark costs. Persist every rejected or
      skipped leg to Supabase order_rejections, which is the only durable audit
      trail for a leg Alpaca never saw.
  10. Close any dust positions.
  11. Reconcile. Run feed_attribution. Write fills to Supabase.
  12. Record the completed run in cron_runs. A halted run is deliberately NOT
      recorded so the next tick retries the legs that never landed.

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY
  ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY

The dashboard service must NOT have the Alpaca keys. This script is the
only path to the paper account.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_execution")


# Hard deadline for the whole job. A healthy run is minutes of work, since fill
# polling is bounded at 30s, so anything past this is a stalled call rather than
# a slow book. Override with EXECUTION_JOB_MAX_SECS.
EXECUTION_JOB_MAX_SECS: float = float(
    os.environ.get("EXECUTION_JOB_MAX_SECS", str(30 * 60))
)


def main(max_secs: float | None = None) -> int:
    """Entry point: arm the guards, then hand off to _run().

    Nothing in this job may block forever. The deadline cuts off any step,
    including a pure-CPU spin that no network timeout could catch, and it names
    the step in flight. Alpaca calls additionally get a real per-request timeout
    via execution.alpaca_paper.apply_request_timeout.
    """
    from execution.job_guard import JobTimeout, current_step, job_guards

    budget = EXECUTION_JOB_MAX_SECS if max_secs is None else max_secs
    try:
        with job_guards(budget, logger):
            return _run()
    except JobTimeout as exc:
        logger.error(
            "run_execution TIMED OUT: %s. Last step started: %s",
            exc, current_step() or "(none)",
        )
        return 3


def _run() -> int:
    from execution.job_guard import step

    today = date.today().isoformat()

    # -- 1. NYSE calendar check
    from execution.calendar_utils import is_trading_day
    with step("1 NYSE calendar check", logger):
        if not is_trading_day(today):
            logger.info("skipping: NYSE closed on %s", today)
            return 0

    # -- 2. Idempotency
    from execution.calendar_utils import check_already_ran, record_run
    with step("2 idempotency check", logger):
        if check_already_ran("run_execution", today):
            logger.info("already ran for %s -- exit 0 (idempotent)", today)
            return 0

    # -- 3. Load signal output written by run_signal.py (no yfinance call needed).
    #        Falls back to ingest() only if Supabase data is missing/stale.
    import json
    from signals.etf_universe import UNIVERSE
    from dashboard.supabase_client import get_setting

    with step("3 load stored signal from Supabase", logger):
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
        with step("3b fallback yfinance ingest", logger):
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
        write_live_attribution,
        write_order_rejections,
        write_pnl_log,
        write_positions,
    )
    from execution.alpaca_paper import DRY_RUN_DEFAULT

    with step("4 decision gate (Supabase)", logger):
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
        apply_shortable_filter,
        build_fill_records,
        build_rejection_rows,
        close_dust_positions,
        compute_delta_orders,
        apply_guards,
        connect,
        feed_attribution,
        get_current_positions,
        get_live_nav,
        get_shortable_flags,
        mark_costs,
        poll_fills,
        reconcile,
        shorts_requiring_check,
        submit_orders,
    )

    with step("5 connect to Alpaca", logger):
        dry_run = DRY_RUN_DEFAULT
        client = connect(dry_run=dry_run)
    logger.info("dry_run=%s", dry_run)

    # -- 6. NAV and current positions
    # Use the frozen Supabase NAV and positions for delta computation so that
    # execution matches exactly what Panel H showed when the user approved.
    # Live Alpaca NAV is read separately to update Supabase after the run.
    with step("6 read NAV and current positions", logger):
        nav_live = get_live_nav(client) if not dry_run else 100_000.0

        _nav_str = get_setting("live_nav")
        nav = float(_nav_str) if _nav_str else nav_live
        logger.info("nav=%.2f (frozen Supabase; live Alpaca=%.2f)", nav, nav_live)

        from dashboard.supabase_client import fetch_positions as _fetch_positions
        _pos_rows = _fetch_positions(latest_only=True)
        current_notionals: dict[str, float] = {
            r["ticker"]: float(r["signed_notional"]) for r in _pos_rows
        }
        if not current_notionals:
            # First ever run -- no Supabase snapshot yet; read live from Alpaca
            logger.info("no Supabase positions found -- reading live from Alpaca (first run)")
            current_notionals = get_current_positions(client, dry_run=dry_run)
        logger.info("current positions: %s", current_notionals)

    # -- 6b. Drift check: does the frozen Supabase snapshot above still match
    #        the real Alpaca account? Delta orders are computed from the
    #        snapshot on purpose (P4: execute exactly what Panel H showed at
    #        approval time), so a divergence here would otherwise compute
    #        deltas against a phantom book with no visible signal. Alert
    #        only -- execution below still uses current_notionals as-is.
    from execution.alpaca_paper import check_position_drift
    with step("6b position drift check", logger):
        drift = check_position_drift(client, current_notionals, dry_run=dry_run)
    if drift:
        logger.warning(
            "POSITION DRIFT: cached (Supabase) vs live (Alpaca) disagree for %d ticker(s): %s",
            len(drift), {t: round(d["diff"], 2) for t, d in drift.items()},
        )
        set_setting("position_drift_alert", json.dumps({
            "detected_at": today,
            "detail": {
                t: {k: round(v, 2) for k, v in d.items()} for t, d in drift.items()
            },
        }))

        from execution.alerts import send_alert_email
        drift_lines = "\n".join(
            f"  {t}: cached ${d['cached']:,.2f}  live ${d['live']:,.2f}  diff ${d['diff']:,.2f}"
            for t, d in drift.items()
        )
        send_alert_email(
            subject=f"[credit-trading-lab] Position drift detected for {today}",
            body=(
                f"The cached position snapshot disagreed with live Alpaca "
                f"positions before today's run_execution computed deltas:\n\n"
                f"{drift_lines}\n\n"
                f"This run still executed against the cached (pre-drift) "
                f"snapshot, per the frozen-snapshot execution design -- see "
                f"Panel H on the dashboard for the corrected current state "
                f"and full context."
            ),
        )
    else:
        # Only a real run may clear the alert: in dry-run no broker comparison
        # happened, so there is nothing to clear.
        if not dry_run:
            set_setting("position_drift_alert", "")

    # -- Run v8.2 signal only if not already loaded from Supabase
    if not target_weights:
        from signals.etf_universe import load_universe_close
        from signals.trend_signal import (
            apply_rebalance_control,
            compute_trend,
            shift_to_next_day,
            to_position_matrix,
        )
        with step("6c fallback: recompute v8.2 signal locally", logger):
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
    with step("7 compute delta orders and apply guards", logger):
        orders = compute_delta_orders(target_weights, current_notionals, paper_nav=nav)
        guarded = apply_guards(orders, dry_run=dry_run, _nav=nav)

    # -- 7b. Shortability pre-check, live, before anything is submitted.
    #        Only sell_to_open legs can be affected: buy_to_close (reduce a
    #        short) and sell_to_close (reduce a long) must still go through, so
    #        a broker restriction on opening shorts never traps an existing one.
    #        A skipped leg is logged and persisted to order_rejections.
    if not dry_run:
        check_tickers = shorts_requiring_check(guarded)
        if check_tickers:
            with step("7b shortability pre-check (Alpaca assets)", logger):
                shortable = get_shortable_flags(client, check_tickers)
            logger.info("shortable flags: %s", shortable)
            guarded = apply_shortable_filter(guarded, shortable)

    pending_count = sum(1 for o in guarded if o.guard_status == "PENDING")
    logger.info(
        "orders: %d total, %d pending, %d blocked before submit",
        len(guarded), pending_count, len(guarded) - pending_count,
    )

    # -- 8. Submit orders one leg at a time (longs notional, shorts whole-share
    #       qty). A rejected leg is classified, logged, and skipped; the run
    #       continues through the remaining legs so one bad order cannot strand a
    #       partial book. A transport failure halts submission and is turned into
    #       a clean halt below, never an unhandled traceback.
    with step("8 submit orders", logger):
        outcomes = submit_orders(client, guarded, close_prices=close_prices)
    run_halted = any(o.halts_run for o in outcomes)
    halted_legs = sum(1 for o in outcomes if o.halts_run)
    if run_halted:
        logger.error(
            "submission halted: %d leg(s) failed with unknown state; remaining "
            "legs were not attempted. State will be written and cron_runs left "
            "unrecorded so the next tick retries.",
            halted_legs,
        )

    # -- 9. Poll fills
    with step("9 poll fills and build fill records", logger):
        real_ids = [o.order_id for o in outcomes if o.submitted and o.order_id]
        fill_data = poll_fills(client, real_ids) if real_ids else {}

        fills = build_fill_records(guarded, outcomes, fill_data)

        # Mark costs using post-execution short notionals
        short_notionals = {
            t: abs(n) for t, n in current_notionals.items() if n < 0
        }
        fills = mark_costs(fills, current_short_notionals=short_notionals)

    # -- 9b. Persist the rejection audit trail. This is the ONLY durable record
    #        of a leg that never became an order: Alpaca keeps nothing for a
    #        submit-time rejection, and this box's filesystem is ephemeral.
    rejection_rows = build_rejection_rows(fills, run_date=date.fromisoformat(today))
    if rejection_rows:
        with step("9b persist rejection audit to Supabase", logger):
            wrote = write_order_rejections(rejection_rows)
        if wrote:
            logger.info(
                "order_rejections: persisted %d leg(s): %s",
                len(rejection_rows),
                [(r["ticker"], r["reason_code"]) for r in rejection_rows],
            )
        else:
            # Render's disk is ephemeral, so if the table write fails these log
            # lines are the ONLY remaining record of a rejected leg. Print every
            # field needed to reconstruct the row, not just a count.
            detail_lines = "\n".join(
                "    ticker={ticker} intent={position_intent} leg={leg} "
                "notional=${requested_notional:,.2f} status={status} "
                "reason_code={reason_code} detail={detail}".format(**row)
                for row in rejection_rows
            )
            logger.error(
                "order_rejections write FAILED for %d leg(s). The reconciliation "
                "JSON is local and the disk is temporary, so these details are "
                "the only remaining record:\n%s",
                len(rejection_rows), detail_lines,
            )
    else:
        logger.info("order_rejections: no rejected or skipped legs this run")

    # -- 10. Dust cleanup
    with step("10 dust cleanup", logger):
        dust_closed = close_dust_positions(client, dry_run=dry_run)
    if dust_closed:
        logger.info("dust cleanup: closed %s", dust_closed)

    # -- 11. Reconcile
    run_date_obj = date.fromisoformat(today)
    with step("11 reconcile and feed attribution", logger):
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
            with step("11a write live attribution to Supabase", logger):
                write_live_attribution(live_rows)

    # Update live_nav with real post-trade Alpaca NAV so tonight's Panel H is accurate.
    # A dry run must not: Alpaca was never queried there, so nav_live is the
    # placeholder constant and writing it would silently replace the account NAV
    # with 100,000 in the settings table that Panel H sizes against.
    with step("11b write live_nav, positions and pnl_log to Supabase", logger):
        if dry_run:
            logger.info(
                "dry run: NOT writing live_nav (nav_live=%.2f is the placeholder, "
                "Alpaca was not queried)",
                nav_live,
            )
        else:
            set_setting("live_nav", str(round(nav_live, 2)))

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

        # Write P&L log row. Skipped in dry-run: nothing executed, so a zero row
        # would be a fabricated P&L record for a day that did not trade.
        total_gross = sum(f.filled_notional for f in fills if f.status == "FILLED")
        total_net_pnl = -sum(f.simulated_cost for f in fills if f.status == "FILLED")
        total_cost = sum(f.simulated_cost for f in fills if f.status == "FILLED")

        if dry_run:
            logger.info("dry run: NOT writing pnl_log (no fills executed)")
        else:
            write_pnl_log({
                "trade_date": today,
                "gross_pnl": round(total_gross, 4),
                "net_pnl": round(total_net_pnl, 4),
                "turnover_cost": round(total_cost, 4),
                "borrow_cost": 0.0,
            })

    # -- 12. Record run. A halted run is deliberately NOT recorded: cron_runs is
    #        the idempotency gate, so leaving it unwritten lets the next tick
    #        retry the legs that never landed.
    if run_halted:
        logger.error(
            "run_execution HALTED cleanly for %s: state written to Supabase, "
            "cron_runs not recorded so the next tick retries",
            today,
        )
        return 2

    # A dry run must not record either. Writing cron_runs would mark the date as
    # done and the real scheduled run that day would skip itself.
    if dry_run:
        logger.info(
            "dry run: NOT recording cron_runs (nothing executed; %s stays open "
            "for the real run)",
            today,
        )
        return 0

    with step("12 record run", logger):
        record_run("run_execution", today)
    logger.info("run_execution complete for %s", today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
