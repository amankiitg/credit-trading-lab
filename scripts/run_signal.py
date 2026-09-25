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

Every step logs begin and done, and the whole job runs under a hard deadline
(execution/job_guard.py), so a hang is both locatable in the log and impossible
to leave running forever.

The operator then approves or rejects via the dashboard before the morning
execution cron fires.

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY -- for writing decisions and cron_runs.
  ALPACA_* vars are NOT needed here; this script never touches Alpaca.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_signal")

# yfinance refresh budget. Closes are retried because Yahoo rate limits are
# transient and the execution cron is 17 hours away.
RETRY_INTERVAL_SECS: int = 10 * 60     # 10 minutes between ingest attempts
MAX_RETRY_SECS: int = 4 * 3600         # give up refreshing closes after 4 hours

# Hard deadline for the whole job. It must sit above the ingest budget so it
# cannot cut a legitimate retry short, and it exists so the job can never run
# forever: the 2026-09-24 run sat silent for over four hours and had to be
# killed by hand. Override with SIGNAL_JOB_MAX_SECS to tighten it.
SIGNAL_JOB_MAX_SECS: float = float(
    os.environ.get("SIGNAL_JOB_MAX_SECS", str(MAX_RETRY_SECS + 15 * 60))
)

# Budget for the advisory stop-ladder overlay. It is display-only (the v9.1 gate
# was REJECTED, so weights are never modified by it), which means it must never
# be able to stop the run from writing a fresh signal. Override with
# OVERLAY_MAX_SECS.
OVERLAY_MAX_SECS: float = float(os.environ.get("OVERLAY_MAX_SECS", "120"))


def main(max_secs: float | None = None) -> int:
    """Entry point: arm the guards, then hand off to _run().

    Nothing in this job is allowed to block forever. The deadline cuts off any
    step, including a pure-CPU spin that no network timeout could catch, and it
    reports which step was in flight. The exit code is always non-zero on a
    timeout so the scheduler records a failure instead of a silent success.
    """
    from execution.job_guard import JobTimeout, current_step, job_guards

    budget = SIGNAL_JOB_MAX_SECS if max_secs is None else max_secs
    try:
        with job_guards(budget, logger):
            return _run()
    except JobTimeout as exc:
        logger.error(
            "run_signal TIMED OUT: %s. Last step started: %s",
            exc, current_step() or "(none)",
        )
        return 3


def _run() -> int:
    from execution.job_guard import JobTimeout, step, step_budget

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
        if check_already_ran("run_signal", today):
            logger.info("already ran for %s -- exit 0 (idempotent)", today)
            return 0

    # -- 3. Refresh closes from yfinance with retry (up to the ingest budget).
    #        Yahoo rate limits are transient and the execution cron fires 17
    #        hours later, so retrying is deliberate. Each attempt is bounded by
    #        the explicit per-call yfinance timeout, so a stalled fetch raises
    #        and is logged rather than blocking the job in silence.
    import time
    from signals.etf_universe import UNIVERSE, ingest, load_universe_close

    deadline = time.monotonic() + MAX_RETRY_SECS
    attempt  = 0
    with step("3 refresh closes from yfinance (retry loop)", logger):
        while True:
            attempt += 1
            try:
                ingest(UNIVERSE)
                logger.info("ingest succeeded on attempt %d", attempt)
                break
            except Exception as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(
                        "ingest failed after %d attempts over %.0f minutes: %s "
                        "-- aborting",
                        attempt, MAX_RETRY_SECS / 60, exc,
                    )
                    return 1
                wait = min(RETRY_INTERVAL_SECS, remaining)
                logger.warning(
                    "ingest attempt %d failed (%s: %s) -- retrying in %.0fs",
                    attempt, type(exc).__name__, exc, wait,
                )
                time.sleep(wait)

    with step("3b load universe closes", logger):
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
    with step("4 v8.2 signal pipeline", logger):
        tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
        desired = to_position_matrix(tidy)
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
    from risk.stop_loss import canonical_state, compute_episodes
    sigma_matrix = tidy.pivot(index="date", columns="ticker", values="sigma").sort_index()
    close_matrix = close[list(held.columns)]  # align columns

    # -- 4c. Data-quality gate. Refuse to write a signal built from an incomplete
    #        close matrix. A ticker missing a session the others have gets a NaN
    #        return, so its sigma is NaN, its position is undefined, its weight is
    #        carried forward and then re-scaled by the gross cap. That is exactly
    #        what shipped on 2026-09-24: five names all moved by 0.765 while the
    #        other three were recomputed, and nothing flagged it.
    from signals.data_quality import check_signal_ready

    with step("4c data-quality gate", logger):
        problems = check_signal_ready(close, sigma_matrix, as_of_date)
    if problems:
        logger.error(
            "REFUSING to write a signal for %s: %d data-quality problem(s):",
            as_of_date, len(problems),
        )
        for problem in problems:
            logger.error("  %s", problem)
        logger.error(
            "Repair the raw closes (scripts/backfill_raw_gap.py) and re-run. "
            "Nothing was written, so the stored signal is unchanged."
        )
        return 1
    logger.info("data-quality gate passed for %s", as_of_date)

    try:
        with step("4b v9.1 stop overlay compute (advisory)", logger):
            with step_budget(OVERLAY_MAX_SECS, logger):
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
                s = canonical_state(latest_state.get(t))
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
    except JobTimeout as exc:
        logger.warning(
            "stop overlay exceeded its %.0fs budget (%s) -- skipping it. This "
            "step is advisory only and never modifies weights, so the signal is "
            "unaffected.",
            OVERLAY_MAX_SECS, exc,
        )
        stop_rows = []
        advisory_mode = False
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
    with step("5 write signal settings to Supabase", logger):
        ok_date    = set_setting("signal_as_of_date",     as_of_date)
        ok_weights = set_setting("signal_target_weights", json.dumps(target_weights))
        ok_prices  = set_setting("signal_close_prices",   json.dumps(close_prices))
    if ok_date and ok_weights and ok_prices:
        logger.info("stored target_weights and close_prices to Supabase for %s", as_of_date)
    else:
        logger.error("set_setting failed (date=%s weights=%s prices=%s) -- aborting", ok_date, ok_weights, ok_prices)
        return 1

    with step("5b write decision='proposed' to Supabase", logger):
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
            with step("5c write stop_states to Supabase", logger):
                client = get_supabase_client()
                if client is not None:
                    for row in stop_rows:
                        client.table("stop_states").upsert(row).execute()
                    logger.info("wrote %d stop_states rows to Supabase", len(stop_rows))
        except Exception as exc:
            logger.warning("stop_states write failed (%s) -- continuing", exc)

    # -- 6. Record run
    with step("6 record run", logger):
        record_run("run_signal", today)
    logger.info("run_signal complete for %s", today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
