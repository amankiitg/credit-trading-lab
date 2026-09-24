"""Backfill the records a crashed execution run never wrote.

A crash inside run_execution.main() (before step 11) means the run wrote
NOTHING, even for the legs that filled: no fill records, no pnl_log row, no
attribution rows, and no rejection audit. `record_run` also never fired, so the
run is absent from cron_runs and the next tick retries it. This script
reconstructs those missing records from the broker's own order history, which is
the ground truth, and marks every row `backfilled` so a reconstructed row can
never be mistaken for one written live.

What it reconstructs and from where:
  - the intended legs: the frozen signal weights (Supabase settings) applied to
    the frozen position snapshot the crashed run computed deltas from, with the
    nav that run used. All three are arguments because the live settings row
    gets overwritten by the next run.
  - the legs that filled: real fill price and quantity from Alpaca's order
    history for that date, marked through the v6.5 cost model. The attribution
    mark uses the real same-day close from Alpaca's IEX feed, which is better
    than the live path can do (the live path only has the previous session's
    signal closes available at run time).
  - the legs that did not: a reason code. A sell_to_open leg whose asset is not
    shortable today is recorded as ASSET_NOT_SHORTABLE_AT_SUBMIT (the live
    42210000), anything else as SKIPPED_AFTER_HALT. The heuristic is printed
    with its result so the classification is visible, not silent.
  - the two lost legs of the specific incident below. On 2026-09-24 that rule
    reproduces the known facts exactly: LQD was rejected as not shortable
    (shortable=false) and GLD was never attempted (shortable=true).

This script never submits an order and never records cron_runs. It reads Alpaca
and, with --apply, writes Supabase.

Usage:
    python scripts/backfill_execution_run.py \
        --date 2026-09-24 \
        --frozen-snapshot-date 2026-09-23 \
        --frozen-nav 101013.45                      # report only
    ... --apply                                     # write the records

Env vars required:
    ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY
    SUPABASE_URL, SUPABASE_SECRET_KEY
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill_execution")

ATTRIBUTION_PARQUET = "data/processed/attribution.parquet"

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            import os
            os.environ.setdefault(k.strip(), v.strip())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", required=True, help="crashed run date (YYYY-MM-DD)")
    p.add_argument(
        "--frozen-snapshot-date",
        required=True,
        help="Supabase positions trade_date the crashed run computed deltas from",
    )
    p.add_argument(
        "--frozen-nav",
        type=float,
        required=True,
        help="nav the crashed run used (Supabase settings live_nav at that time)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="write the reconstructed records (default is report only)",
    )
    p.add_argument(
        "--parquet",
        default=ATTRIBUTION_PARQUET,
        help=f"attribution parquet path (default {ATTRIBUTION_PARQUET})",
    )
    return p.parse_args(argv)


def _fetch_run_date_closes(tickers: list[str], run_date: date) -> dict[str, float]:
    """Daily closes for run_date from Alpaca's IEX feed.

    The live path marks fills against the signal's close prices, which are the
    PREVIOUS session's closes and therefore stale for an intraday fill. A
    backfill can do better, so it uses the real same-day close. The IEX feed is
    what the paper account's data subscription permits.

    Returns only tickers that actually have a bar for run_date.
    """
    import os as _os
    from datetime import datetime, timezone

    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    dc = StockHistoricalDataClient(
        _os.environ["ALPACA_PAPER_API_KEY"],
        _os.environ["ALPACA_PAPER_SECRET_KEY"],
    )
    start = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    req = StockBarsRequest(
        symbol_or_symbols=list(tickers),
        timeframe=TimeFrame.Day,
        feed=DataFeed.IEX,
        start=start,
        end=start + timedelta(days=1),
    )
    bars = dc.get_stock_bars(req)
    closes: dict[str, float] = {}
    for ticker in tickers:
        for bar in bars.data.get(ticker, []):
            if bar.timestamp.date() == run_date:
                closes[ticker] = float(bar.close)
    return closes


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_date = date.fromisoformat(args.date)

    from dashboard.supabase_client import get_setting, get_supabase_client
    from execution.alpaca_paper import (
        GUARD_SKIPPED_NOT_SHORTABLE,
        REASON_NOT_SHORTABLE_AT_SUBMIT,
        REASON_SKIPPED_AFTER_HALT,
        FillRecord,
        apply_guards,
        build_rejection_rows,
        compute_delta_orders,
        connect,
        feed_attribution,
        get_shortable_flags,
        mark_costs,
        reconcile,
    )
    from alpaca.trading.enums import OrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    # -- 1. What did the broker actually do that day?
    client = connect(dry_run=False)
    after = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    req = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=after,
        until=after + timedelta(days=1),
        limit=200,
        direction="asc",
    )
    actual = client.get_orders(filter=req)
    # Alpaca returns str-Enums whose str() is the qualified name
    # ("PositionIntent.BUY_TO_OPEN"), so compare on .value.
    actual_by_key = {
        (o.symbol, getattr(o.position_intent, "value", str(o.position_intent))): o
        for o in actual
    }
    filled = [o for o in actual if o.status == OrderStatus.FILLED]
    logger.info(
        "Alpaca order history for %s: %d order(s), %d filled",
        run_date, len(actual), len(filled),
    )
    for o in actual:
        logger.info(
            "    %s %s %s qty=%s notional=%s status=%s",
            o.symbol, o.side, o.position_intent, o.qty, o.notional, o.status,
        )

    # -- 2. Rebuild the intended leg list from the run's frozen inputs.
    weights_raw = get_setting("signal_target_weights")
    if not weights_raw:
        logger.error("signal_target_weights missing from Supabase settings")
        return 1
    target_weights = json.loads(weights_raw)

    sb = get_supabase_client()
    snap = (
        sb.table("positions")
        .select("*")
        .eq("trade_date", args.frozen_snapshot_date)
        .execute()
        .data
    )
    if not snap:
        logger.error("no Supabase snapshot for %s", args.frozen_snapshot_date)
        return 1
    current_notionals = {r["ticker"]: float(r["signed_notional"]) for r in snap}

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=args.frozen_nav)
    guarded = apply_guards(orders, dry_run=False, _nav=args.frozen_nav)
    logger.info("reconstructed %d intended leg(s)", len(guarded))

    # -- 3. Shortability today, used only to explain an unmatched short leg.
    short_tickers = sorted({
        s.ticker for s in guarded
        if s.guard_status == "PENDING" and s.position_intent == "sell_to_open"
    })
    shortable = get_shortable_flags(client, short_tickers) if short_tickers else {}
    logger.info("shortable today: %s", shortable)

    # -- 4. Build fill records: real fills where they exist, reason codes where not.
    records: list[FillRecord] = []
    for spec in guarded:
        key = (spec.ticker, spec.position_intent)
        order = actual_by_key.get(key)

        if order is not None and order.status == OrderStatus.FILLED:
            fill_price = float(order.filled_avg_price or 0)
            filled_notional = float(order.filled_qty or 0) * fill_price
            records.append(FillRecord(
                ticker=spec.ticker,
                order_id=str(order.id),
                side=spec.side,
                position_intent=spec.position_intent,
                intended_notional=spec.notional,
                filled_notional=filled_notional,
                fill_price=fill_price,
                simulated_cost=0.0,
                status="FILLED",
                guard_status=spec.guard_status,
                leg=spec.leg,
                backfilled=True,
            ))
            continue

        if spec.guard_status != "PENDING":
            reason = spec.guard_status
            detail = "guard rejected before submission"
        elif spec.position_intent == "sell_to_open" and not shortable.get(spec.ticker, True):
            reason = REASON_NOT_SHORTABLE_AT_SUBMIT
            detail = (
                "absorbed from the live 42210000 rejection: asset cannot be sold short "
                f"(shortable=false on {run_date})"
            )
        else:
            reason = REASON_SKIPPED_AFTER_HALT
            detail = "run crashed before this leg was reached"

        logger.info(
            "  unmatched leg %s %s -> %s", spec.ticker, spec.position_intent, reason,
        )
        records.append(FillRecord(
            ticker=spec.ticker,
            order_id="",
            side=spec.side,
            position_intent=spec.position_intent,
            intended_notional=spec.notional,
            filled_notional=0.0,
            fill_price=0.0,
            simulated_cost=0.0,
            status="REJECTED" if reason != GUARD_SKIPPED_NOT_SHORTABLE else "SKIPPED",
            guard_status=spec.guard_status,
            leg=spec.leg,
            reason_code=reason,
            detail=detail,
            backfilled=True,
        ))

    # -- 5. Mark costs. Borrow uses the pre-trade short book, as a live run would.
    pre_short = {t: abs(n) for t, n in current_notionals.items() if n < 0}
    records = mark_costs(records, current_short_notionals=pre_short)

    n_filled = sum(1 for r in records if r.status == "FILLED")
    n_other = len(records) - n_filled
    logger.info(
        "reconstructed records: %d filled, %d rejected/skipped", n_filled, n_other,
    )
    for r in records:
        logger.info(
            "  %-4s %-14s %-8s intended=%9.2f filled=%9.2f cost=%7.4f %s",
            r.ticker, r.position_intent, r.status, r.intended_notional,
            r.filled_notional, r.simulated_cost, r.reason_code,
        )

    # -- 6. Reconcile (always writes the local JSON; that is the point).
    report = reconcile(guarded, records, run_date=run_date)
    logger.info(
        "reconciliation written: %d legs, %d flagged, %d rejected/skipped, %d backfilled",
        report["total_fills_captured"], report["flagged_discrepancies"],
        report["rejected_or_skipped_legs"], report["backfilled_legs"],
    )

    rejection_rows = build_rejection_rows(records, run_date=run_date, backfilled=True)
    logger.info("order_rejections rows: %d", len(rejection_rows))
    for row in rejection_rows:
        logger.info(
            "  %s %s %s %s", row["ticker"], row["position_intent"],
            row["reason_code"], row["detail"],
        )

    if not args.apply:
        logger.info("report only (default). Pass --apply to write the records.")
        return 0

    # -- 7. Write the missing Supabase records, all flagged as backfilled.
    from dashboard.supabase_client import (
        write_live_attribution,
        write_order_rejections,
        write_pnl_log,
    )

    if rejection_rows:
        # Idempotent re-run: drop only this script's earlier attempt for the
        # same date, never a row a real run wrote.
        prior = (
            sb.table("order_rejections")
            .select("id")
            .eq("run_date", str(run_date))
            .eq("backfilled", True)
            .execute()
            .data
        )
        if prior:
            logger.info(
                "removing %d prior backfilled order_rejections row(s) for %s",
                len(prior), run_date,
            )
            (
                sb.table("order_rejections")
                .delete()
                .eq("run_date", str(run_date))
                .eq("backfilled", True)
                .execute()
            )
        if not write_order_rejections(rejection_rows):
            logger.error("order_rejections write FAILED")
            return 1
        logger.info("wrote %d order_rejections row(s)", len(rejection_rows))

    total_filled = sum(r.filled_notional for r in records if r.status == "FILLED")
    total_cost = sum(r.simulated_cost for r in records if r.status == "FILLED")
    pnl_row = {
        "trade_date": str(run_date),
        "gross_pnl": round(total_filled, 4),
        "net_pnl": round(-total_cost, 4),
        "turnover_cost": round(total_cost, 4),
        "borrow_cost": 0.0,
        "backfilled": True,
        "backfill_note": (
            f"reconstructed on {date.today().isoformat()} from Alpaca order history "
            f"after the {run_date} run crashed on an unhandled 42210000"
        ),
    }
    if not write_pnl_log(pnl_row):
        logger.error("pnl_log write FAILED")
        return 1
    logger.info("wrote pnl_log for %s: gross=%.2f net=%.2f", run_date, total_filled, -total_cost)

    # Attribution needs a close for every ticker that filled, or feed_attribution
    # silently drops the row. Fail loudly instead of writing a partial day.
    filled_tickers = sorted({r.ticker for r in records if r.status == "FILLED"})
    closes = _fetch_run_date_closes(filled_tickers, run_date) if filled_tickers else {}
    missing = [t for t in filled_tickers if t not in closes]
    if missing:
        logger.error("no %s close available for %s -- refusing a partial backfill", run_date, missing)
        return 1
    logger.info("run-date closes (IEX): %s", {k: round(v, 4) for k, v in closes.items()})

    n_rows = 0
    already_present = False
    if os.path.exists(args.parquet):
        import pandas as pd
        already = pd.read_parquet(args.parquet)
        dup = int((already["date"].astype(str).str.startswith(str(run_date))).sum())
        if dup:
            already_present = True
            logger.warning(
                "attribution parquet already holds %d row(s) for %s -- not appending again",
                dup, run_date,
            )

    if not already_present:
        n_rows = feed_attribution(
            records,
            close_prices=closes,
            run_date=run_date,
            nav=args.frozen_nav,
            parquet_path=args.parquet,
            backfilled=True,
        )
    logger.info("appended %d attribution row(s) to %s", n_rows, args.parquet)

    if n_rows:
        import pandas as pd
        attr = pd.read_parquet(args.parquet)
        today_rows = attr[attr["date"].astype(str).str.startswith(str(run_date))]
        live_rows = []
        for _, r in today_rows.iterrows():
            live_rows.append({
                "run_date": str(run_date),
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
                "backfilled": True,
            })
        if not write_live_attribution(live_rows):
            logger.error("live_attribution write FAILED")
            return 1
        logger.info("wrote %d live_attribution row(s)", len(live_rows))

    logger.info(
        "backfill complete for %s. cron_runs deliberately NOT recorded: the run "
        "did not complete, and leaving it unrecorded lets the next tick retry.",
        run_date,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
