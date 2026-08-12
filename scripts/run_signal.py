"""Evening signal cron -- sprint v8.6.

Schedule: "30 21 * * 1-5" UTC
  = 17:30 EDT (UTC-4, summer) = 16:30 EST (UTC-5, winter)
  Both are after the 16:00 ET NYSE close, regardless of DST.

What it does:
  1. NYSE calendar check -- skip if today is not a trading day.
  2. Idempotency check -- exit 0 if this job already ran for today.
  3. Reload universe closes (yfinance cache).
  4. Run v8.2 signal pipeline to compute proposed weights.
  5. Write decision='proposed' to Supabase decisions table for as_of_date.
  6. Record the completed run in cron_runs.

The operator then approves or rejects via the dashboard before the morning
execution cron fires.

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY -- for writing decisions and cron_runs.
  ALPACA_* vars are NOT needed here; this script never touches Alpaca.
"""

from __future__ import annotations

import logging
import sys
from datetime import date

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_signal")


def main() -> int:
    today = date.today().isoformat()

    # -- 1. NYSE calendar check
    from execution.calendar_utils import is_trading_day
    if not is_trading_day(today):
        logger.info("skipping: NYSE closed on %s", today)
        return 0

    # -- 2. Idempotency
    from execution.calendar_utils import check_already_ran, record_run
    if check_already_ran("run_signal", today):
        logger.info("already ran for %s -- exit 0 (idempotent)", today)
        return 0

    # -- 3. Refresh closes from yfinance with retry (up to 4 hours).
    #        Rate limits from Yahoo Finance are transient; the execution cron
    #        fires 17 hours later so we have plenty of runway to retry.
    import time
    from signals.etf_universe import UNIVERSE, ingest, load_universe_close

    RETRY_INTERVAL_SECS = 10 * 60   # 10 minutes between attempts
    MAX_RETRY_SECS      = 4 * 3600  # give up after 4 hours
    deadline = time.monotonic() + MAX_RETRY_SECS
    attempt  = 0
    while True:
        attempt += 1
        try:
            ingest(UNIVERSE)
            break
        except Exception as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "ingest failed after %d attempts over 4 hours: %s -- aborting",
                    attempt, exc,
                )
                return 1
            wait = min(RETRY_INTERVAL_SECS, remaining)
            logger.warning(
                "ingest attempt %d failed (%s: %s) -- retrying in %.0fs",
                attempt, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)

    close = load_universe_close()
    as_of_date = str(close.index[-1].date())
    logger.info("as_of_date: %s (latest close available)", as_of_date)

    # -- 4. Run v8.2 signal
    from signals.trend_signal import (
        apply_rebalance_control,
        compute_trend,
        shift_to_next_day,
        to_position_matrix,
    )
    desired = to_position_matrix(
        compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    )
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    target = shift_to_next_day(held)
    weights = target.iloc[-1]

    logger.info(
        "proposed weights for %s: %s",
        as_of_date,
        {t: round(float(weights.get(t) or 0), 4) for t in UNIVERSE},
    )

    # -- 4b. v9.1: Stop-ladder overlay (ADVISORY MODE ONLY -- gate REJECTED).
    #        Compute stop states for display in Panel H but NEVER modify weights.
    #        Per T6 gate decision: F2/F3/F4/F5 all fail → advisory mode.
    from risk.stop_loss import compute_episodes
    sigma_matrix = tidy.pivot(index="date", columns="ticker", values="sigma").sort_index()
    close_matrix = close[list(held.columns)]  # align columns

    try:
        mults_df, states_df, z_df = compute_episodes(
            held, close_matrix, sigma_matrix,
        )
        # Advisory mode: final weights = held weights unchanged (multiplier NOT applied).
        # Target weights already computed above as target = shift_to_next_day(held).
        advisory_mode = True

        # Latest states for each ticker (for Supabase stop_states)
        latest_mult = mults_df.iloc[-1]
        latest_state = states_df.iloc[-1]
        latest_z = z_df.iloc[-1]

        stop_rows = []
        for t in UNIVERSE:
            if t in latest_mult.index:
                m = float(latest_mult.get(t)) if pd.notna(latest_mult.get(t)) else 1.0
                s = str(latest_state.get(t)) if latest_state.get(t) is not None else "NORMAL"
                z_val = float(latest_z.get(t)) if pd.notna(latest_z.get(t)) else None
                stop_rows.append({
                    "ticker": t,
                    "state": s,
                    "z": round(z_val, 6) if z_val is not None else None,
                    "multiplier": m,
                    "advisory": True,
                })
                logger.info("  stop_state[%s]: %s (z=%.4f, m=%.2f)", t, s, z_val or 0, m)

        logger.info("v9.1 stop overlay: advisory_mode=True (gate REJECTED, weights unchanged)")
    except Exception as exc:
        logger.warning("stop overlay computation failed (%s) -- skipping, no weights modified", exc)
        stop_rows = []
        advisory_mode = False

    # -- 5. Write signal output + close prices to Supabase so the execution
    #        cron can read them directly without calling yfinance again.
    import json
    target_weights = {t: round(float(weights.get(t) or 0), 6) for t in UNIVERSE}
    close_prices   = {
        t: round(float(close[t].iloc[-1]), 6)
        for t in UNIVERSE
        if t in close.columns and float(close[t].iloc[-1]) == float(close[t].iloc[-1])
    }

    from dashboard.supabase_client import get_supabase_client, set_setting, write_decision
    ok_date    = set_setting("signal_as_of_date",        as_of_date)
    ok_weights = set_setting("signal_target_weights",    json.dumps(target_weights))
    ok_prices  = set_setting("signal_close_prices",      json.dumps(close_prices))
    if ok_date and ok_weights and ok_prices:
        logger.info("stored target_weights and close_prices to Supabase for %s", as_of_date)
    else:
        logger.error("set_setting failed (date=%s weights=%s prices=%s) -- aborting", ok_date, ok_weights, ok_prices)
        return 1

    ok = write_decision(as_of_date, "proposed")
    if not ok:
        logger.warning(
            "Supabase write failed for %s -- run will NOT be recorded as "
            "complete so it will retry next time",
            as_of_date,
        )
        return 1

    logger.info("wrote decision='proposed' for %s", as_of_date)

    # -- 5b. Write v9.1 stop_states to Supabase (advisory display only).
    if stop_rows:
        try:
            client = get_supabase_client()
            if client is not None:
                for row in stop_rows:
                    client.table("stop_states").upsert(row).execute()
                logger.info("wrote %d stop_states rows to Supabase", len(stop_rows))
        except Exception as exc:
            logger.warning("stop_states write failed (%s) -- continuing", exc)

    # -- 6. Record run
    record_run("run_signal", today)
    logger.info("run_signal complete for %s", today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
