"""Evening signal cron -- sprint v8.6.

Schedule: "30 21 * * 1-5" UTC
  = 17:30 EDT (UTC-4, summer) = 16:30 EST (UTC-5, winter)
  Both are after the 16:00 ET NYSE close, regardless of DST.

What it does:
  1. NYSE calendar check -- skip if today is not a trading day.
  2. Idempotency check -- exit 0 if this job already ran for today.
  3. Reload universe closes and the distribution cache (yfinance). Missing closes
     abort the run, because the signal cannot be computed without them. Missing
     distributions do not: they are recorded, and the sleeve P&L refuses the session
     rather than booking a missing payout as a price loss.
  4. Run v8.2 signal pipeline to compute proposed weights.
  5. Write decision='proposed' to Supabase decisions table for as_of_date.
  6. Record the completed run in cron_runs.
  7. Refresh the cached position snapshot and live_nav from Alpaca.
  8. Decompose the session's book P&L by sleeve and into carry vs price, and
     reconcile it against the portfolio history's close equity.
  8b. Store every close that reconciliation read (D-1 and earlier) in
     close_equity, so the dashboard's evening NAV series is a table read and the
     dashboard needs no broker credential of its own.

Every step logs begin and done, and the whole job runs under a hard deadline
(execution/job_guard.py), so a hang is both locatable in the log and impossible
to leave running forever.

The operator then approves or rejects via the dashboard before the morning
execution cron fires.

Env vars required:
  SUPABASE_URL, SUPABASE_SECRET_KEY -- for writing decisions and cron_runs.
  ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY -- for the post-close reads in steps 7
    and 8. Both are READ-ONLY: positions, NAV, the session's fills and the equity
    history. No order is ever placed from this job, and it must never be given a client
    that could place one.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

import pandas as pd

from execution.daily_summary import RunSummary

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

# Budget for the daily sleeve P&L decomposition. It reads the previous session's
# snapshot and the local price cache and writes its own table; it can never change the
# signal, so it gets a bound of its own rather than a share of the job's remaining time.
# Override with SLEEVE_PNL_MAX_SECS.
SLEEVE_PNL_MAX_SECS: float = float(os.environ.get("SLEEVE_PNL_MAX_SECS", "120"))

# Budget for storing the close series. Bookkeeping with no reader on the trading path:
# one upsert of at most a couple of dozen rows, and a deadline of its own so a wedged
# PostgREST call cannot consume the run's remaining time. It is caught like every other
# bookkeeping step, so a timeout here is a warning in the log, never a failed run.
# Override with CLOSE_EQUITY_MAX_SECS.
CLOSE_EQUITY_MAX_SECS: float = float(os.environ.get("CLOSE_EQUITY_MAX_SECS", "30"))


def main(max_secs: float | None = None) -> int:
    """Entry point: run under the guards and send exactly one summary email.

    Nothing in this job is allowed to block forever. The deadline cuts off any
    step, including a pure-CPU spin that no network timeout could catch, and it
    reports which step was in flight. The exit code is always non-zero on a
    timeout so the scheduler records a failure instead of a silent success.

    All of the exit handling lives in execution.daily_summary.execute_job, so
    every outcome (success, skip, gate block, timeout, crash) produces one email.
    """
    from execution.daily_summary import execute_job

    budget = SIGNAL_JOB_MAX_SECS if max_secs is None else max_secs
    return execute_job(
        job="run_signal",
        budget_secs=budget,
        body=_run,
        log=logger,
    )


def _run(summary: RunSummary) -> int:
    from execution.job_guard import JobTimeout, step, step_budget

    today = date.today().isoformat()

    # -- 1. NYSE calendar check
    from execution.calendar_utils import is_trading_day
    with step("1 NYSE calendar check", logger):
        if not is_trading_day(today):
            logger.info("skipping: NYSE closed on %s", today)
            summary.mark_skip(f"NYSE closed on {today}")
            return 0

    # -- 2. Idempotency
    from execution.calendar_utils import check_already_ran, record_run
    with step("2 idempotency check", logger):
        if check_already_ran("run_signal", today):
            logger.info("already ran for %s -- exit 0 (idempotent)", today)
            summary.mark_skip(f"already ran for {today} (idempotent)")
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
    summary.signal_as_of = as_of_date

    # -- 3c. Refresh the distribution cache, on the same deadline and retry policy as the
    #        closes. Ex-dates are what matter: the unadjusted close drops on the ex-date,
    #        so a distribution the cache does not know about turns that drop into a fake
    #        price loss in the sleeve decomposition.
    #
    #        A failure here does NOT abort the run. The signal is the run's product and
    #        the distributions are not, and refusing to write a signal because Yahoo
    #        would not serve a dividend table would let bookkeeping veto the one thing
    #        this job exists to do. Step 8 refuses the session instead, and the reason
    #        reaches the email so the missing line is explained rather than mysterious.
    from signals.dividends import ingest as ingest_dividends

    with step("3c refresh distributions from yfinance (retry loop)", logger):
        div_attempt = 0
        div_report: dict = {"ok": False, "as_of": None, "failed": "not attempted"}
        while True:
            div_attempt += 1
            try:
                div_report = ingest_dividends(UNIVERSE)
                logger.info(
                    "distributions refreshed on attempt %d: %s across %s ticker(s)",
                    div_attempt,
                    div_report.get("distributions"),
                    div_report.get("tickers"),
                )
                break
            except Exception as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    div_report = {
                        "ok": False,
                        "as_of": None,
                        "failed": (
                            f"{type(exc).__name__}: {exc} after {div_attempt} "
                            f"attempt(s)"
                        ),
                    }
                    logger.error(
                        "distribution refresh failed after %d attempt(s): %s -- the "
                        "sleeve P&L for this session will be refused rather than "
                        "booking a missing payout as a price loss",
                        div_attempt, exc,
                    )
                    break
                wait = min(RETRY_INTERVAL_SECS, remaining)
                logger.warning(
                    "distribution refresh attempt %d failed (%s: %s) -- retrying in "
                    "%.0fs",
                    div_attempt, type(exc).__name__, exc, wait,
                )
                time.sleep(wait)

    if not div_report.get("ok"):
        summary.extra.append(
            f"Distributions not refreshed: {div_report.get('failed')}"
        )

    # What the hole repair filled during ingest, if anything. surfacing it here is
    # the point of the daily email: a fill is a silent data change otherwise.
    from signals.raw_repair import last_lookup_error, last_report
    _repair = last_report() or {}
    for _f in _repair.get("filled", []):
        summary.gap_fills.append(
            f"{_f['ticker']} {_f['date'].date()} (source=alpaca_iex)"
        )
    for _t, _why in sorted((_repair.get("refused") or {}).items()):
        summary.extra.append(f"Gap repair refused: {_t} ({_why})")
    # A repair that could not read the broker leaves no fills and no refusals, which
    # is exactly what a repair with nothing to do looks like. Say it was unavailable
    # instead of leaving the absence unexplained.
    _lookup_error = last_lookup_error()
    if _lookup_error:
        summary.extra.append(f"Gap repair: not available ({_lookup_error})")

    # -- 3d. Missing closes on the latest session: HOLD, never resize.
    #
    #        On 2026-07-28 four names (EFA, HYG, IEF, LQD) had no close for that
    #        session while the other four did. The signal was still computed: the
    #        four became NaN, `apply_rebalance_control` carried their previous
    #        weights forward, the joint gross cap renormalised the remaining four
    #        to their uncapped v/sigma (SPY 0.2727 -> 0.3530), and then the final
    #        re-cap scaled the whole row down. SPY was reported with a weight a
    #        third larger than its own signal implies, for one session, and
    #        nothing said so.
    #
    #        Sizing a book from a session that only seven eighths of the universe
    #        has traded is never right: the missing names' weights are stale and
    #        the present names' have been renormalised to fill the gap. So if any
    #        ticker lacks the latest close, no target is sized at all. The stored
    #        target weights are left exactly as they were, which is the same book
    #        the execution job sized from yesterday, and the email says [HOLD].
    from signals.data_quality import missing_latest_close

    missing = missing_latest_close(close)
    if missing:
        return _hold_previous_targets(
            summary, missing, as_of_date=as_of_date, today=today
        )

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
    from risk.stop_loss import build_stop_rows, compute_episodes
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
        shown = "; ".join(problems[:5])
        if len(problems) > 5:
            shown += f"; ... and {len(problems) - 5} more"
        summary.data_check = f"BLOCKED ({len(problems)} problem(s)): {shown}"
        summary.mark_skip(f"data-quality gate blocked the signal for {as_of_date}")
        return 1
    logger.info("data-quality gate passed for %s", as_of_date)
    summary.data_check = "passed"

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

        stop_rows = build_stop_rows(UNIVERSE, latest_state, latest_mult, latest_z)
        for row in stop_rows:
            logger.info(
                "  stop_state[%s]: %s (z=%.4f, m=%.2f)",
                row["ticker"], row["state"], row["z"] or 0.0, row["multiplier"],
            )

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

    _post_run_bookkeeping(summary, today, as_of_date)

    logger.info("run_signal complete for %s", today)
    return 0


def _hold_previous_targets(
    summary: RunSummary,
    missing: list[str],
    *,
    as_of_date: str,
    today: str,
) -> int:
    """Do not size anything: keep the stored targets and report a [HOLD].

    The stored `signal_target_weights`, `signal_as_of_date` and
    `signal_close_prices` are deliberately not written. They already describe the
    book the execution job is holding, and rewriting them from a session that is
    missing closes is exactly the failure this branch exists to prevent. No
    decision row is written either, so the morning job cannot approve a proposal
    that was never made.
    """
    import json

    from dashboard.supabase_client import get_setting

    names = ", ".join(sorted(missing))
    logger.error(
        "HOLD: %d ticker(s) have no close for %s: %s. Not sizing new targets; "
        "the stored target weights stay unchanged.",
        len(missing), as_of_date, names,
    )

    held_as_of = get_setting("signal_as_of_date")
    held_raw = get_setting("signal_target_weights")
    held: dict = {}
    if held_raw:
        try:
            held = json.loads(held_raw)
        except ValueError:
            logger.error("stored signal_target_weights is not valid JSON -- cannot hold")
            held = {}

    if not held:
        # Nothing to hold is not a hold. A first run with no closes is a real
        # failure, and pretending otherwise would leave the book unsized.
        logger.error(
            "no stored target weights to hold for %s -- failing the run instead",
            as_of_date,
        )
        summary.failure = (
            f"no close for {names} on {as_of_date} and no previous target weights "
            f"to hold"
        )
        return 1

    logger.info(
        "holding targets from %s unchanged: %s",
        held_as_of or "(unknown date)",
        {t: round(float(v), 4) for t, v in held.items() if v is not None},
    )

    summary.mark_hold(missing, held_as_of=str(held_as_of) if held_as_of else None)
    summary.held_targets = {
        ticker: round(float(weight), 4)
        for ticker, weight in held.items()
        if weight is not None
    }
    # The signal in force is the previous one, not the incomplete session.
    summary.signal_as_of = str(held_as_of) if held_as_of else None
    summary.data_check = f"HOLD (missing closes: {names})"

    # Bookkeeping still runs: the snapshot refresh and the sleeve decomposition are
    # reads that never size anything, and skipping them would put a hole in the
    # dashboard's evening series for every held session.
    _post_run_bookkeeping(summary, today, as_of_date)

    # The run is deliberately NOT recorded in cron_runs. A held session is often a
    # publication lag, and leaving it unrecorded is what lets the job be re-run by
    # hand once the closes land -- the same reason the stale-signal guard in
    # run_execution leaves it unwritten.
    logger.warning(
        "run not recorded in cron_runs: a re-run after the %s closes are published "
        "will size normally",
        as_of_date,
    )
    return 0


def _post_run_bookkeeping(summary: RunSummary, today: str, as_of_date: str) -> None:
    """Steps 7, 8 and 8b -- reads and writes that can never touch the signal.

    Split out so a held session still refreshes the sizing basis and stores its
    close series. Both callers have already written (or deliberately not written)
    their signal by the time this runs.
    """
    from execution.job_guard import JobTimeout, step, step_budget

    # -- 7. Refresh the cached position snapshot and live_nav from Alpaca.
    #        The proposal in Panel H and the next morning's execution are both sized
    #        from that snapshot, on purpose, so it has to be refreshed after the
    #        close or the basis ages for as long as nobody trades. Bookkeeping only:
    #        it must never fail a run whose signal is already written.
    alpaca = None
    with step("7 refresh position snapshot and live_nav from Alpaca", logger):
        from execution.snapshot import open_paper_client, refresh_snapshot

        # One read-only client for steps 7 and 8. Both read the same paper account, and
        # step 8 used to build its own with a bare connect() -- which returns no client at
        # all while DRY_RUN_DEFAULT is true, so every evening reported "Execution P&L:
        # not available (no Alpaca client)" for a read that was never gated by dry run.
        alpaca = open_paper_client(log=logger)
        refresh = refresh_snapshot(run_date=today, log=logger, client=alpaca)

    # Recorded on the summary rather than logged only, so the email carries the
    # outcome and a failure lifts the subject to [SKIP]. Never changes the exit code.
    summary.record_basis_refresh(refresh)
    if refresh.get("failed"):
        logger.warning("snapshot refresh failed: %s", refresh.get("skipped"))
    # -- 8. Daily sleeve P&L: this session's book P&L split into the four sleeves and,
    #        inside each, carry vs price. It belongs here rather than in the execution
    #        job because only this job runs after the close: at 10:31 ET the session's
    #        closing price does not exist yet, and this decomposition is defined on
    #        closes. Bookkeeping like the refresh above: it reports what it did, writes
    #        nothing unless the sleeves reconcile, and can never fail the run.
    from risk.sleeve_pnl import record_daily_sleeve_pnl

    with step("8 daily sleeve P&L (carry vs price)", logger):
        with step_budget(SLEEVE_PNL_MAX_SECS, logger):
            sleeve_report = record_daily_sleeve_pnl(
                as_of_date=as_of_date, log=logger, client=alpaca
            )
    summary.record_sleeve_pnl(sleeve_report)

    # -- 8b. Store the closes that reconciliation just read. The portfolio history
    #        labels each close with the FOLLOWING calendar day, so on the evening of
    #        D this read covers D-1 and earlier; published_closes has already turned
    #        those labels back into sessions and dropped D. One upsert per evening
    #        keeps the dashboard's NAV series current without giving the dashboard a
    #        broker credential or a second ingest path.
    #
    #        Bookkeeping, like step 8: a failed or timed-out write is a warning and
    #        the run still exits 0. The signal and the order file are already written
    #        by this point, and a chart must never be able to un-write them.
    closes = (sleeve_report.get("execution") or {}).get("close_equity") or {}
    if closes:
        from dashboard.supabase_client import write_close_equity

        with step("8b store close equity", logger):
            try:
                with step_budget(CLOSE_EQUITY_MAX_SECS, logger):
                    stored = write_close_equity(
                        closes, source="alpaca_portfolio_history"
                    )
                if stored:
                    logger.info(
                        "stored %d close_equity row(s) (%s..%s)",
                        len(closes), min(closes), max(closes),
                    )
                else:
                    logger.warning(
                        "close_equity write returned False (%d row(s)) -- the "
                        "dashboard keeps its stored series",
                        len(closes),
                    )
            except (Exception, JobTimeout) as exc:
                # JobTimeout is a BaseException, so a bare `except Exception` would
                # let a deadline here unwind through step() and fail the whole run.
                logger.warning(
                    "close_equity write failed (%s: %s) -- continuing; the signal "
                    "and the order file are unaffected",
                    type(exc).__name__, exc,
                )


if __name__ == "__main__":
    sys.exit(main())
