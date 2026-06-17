"""Paper execution layer for the v8.2 long/short book, sprint v8.4.

Translates signed target weights from the v8.2 signal into Alpaca paper-
account market orders, enforces fail-safe guards, captures fills, marks
them through the v6.5 cost model, and reconciles fill vs intention.

Zero-crossing (long-to-short or short-to-long) is handled as an explicit
two-leg sequence: one order to close the existing position, one order to
open the new position in the opposite direction. Both legs are submitted
separately so each can be guarded, logged, and reconciled independently.

Credentials: ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY must be set
as environment variables. They must never appear in any committed file,
log output, or test fixture.

DRY_RUN_DEFAULT = True guarantees the paper account cannot be touched
without an explicit override. This is the safe default; flip to False only
inside a supervised live paper session.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import (
        OrderSide,
        OrderStatus,
        PositionIntent,
        PositionSide,
        TimeInForce,
    )
    from alpaca.trading.requests import MarketOrderRequest
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False
    TradingClient = None  # type: ignore

from execution.costs import CostParams
from signals.etf_universe import UNIVERSE

LOG_DIR = Path("execution/logs")
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"

# Guard 1 (position-size cap) is NAV-relative: it limits how large a single
# position can be as a fraction of the book.  With vol-targeted weights across
# 8 liquid ETFs, the largest observed single-name weight is ~36% (TLT); 0.40
# (40% of NAV) admits that weight with modest headroom.  The cap is evaluated
# against the TARGET notional, not the delta, and covers longs and shorts
# symmetrically via abs().
#
# Guard 2 (traded-notional brake) is ABSOLUTE: it is a fat-finger throughput
# limit on the total dollar volume a single execution run can transact,
# independent of book size.  $16,000 caps a run at 2x the largest expected
# single-name notional at $100k NAV with a 40% cap.
#
# An order must pass BOTH guards.  The two guards intentionally use different
# units: the cap scales with the book, the brake does not.

MAX_POSITION_PCT_OF_NAV: float = 0.40
MAX_TRADED_NOTIONAL_PER_RUN: float = 16_000.0
MAX_ORDERS_PER_RUN: int = 20
DELTA_MIN_NOTIONAL: float = 10.0
DRY_RUN_DEFAULT: bool = True
PAPER_NAV_DEFAULT: float = 100_000.0
FILL_POLL_TIMEOUT_SECS: int = 30
FILL_POLL_INTERVAL_SECS: float = 1.0
RECONCILE_ABS_TOL: float = 10.0
RECONCILE_REL_TOL: float = 0.005

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- data model

@dataclass(frozen=True)
class OrderSpec:
    """One intended order leg -- immutable after creation."""

    ticker: str
    side: str              # "buy" or "sell"
    notional: float        # unsigned magnitude in USD
    position_intent: str   # "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"
    target_notional: float # signed final target for this ticker (used for the cap guard)
    guard_status: str      # "PENDING" | "REJECTED_CAP" | "REJECTED_TRADED_NOTIONAL" | "REJECTED_MAX_ORDERS" | "DRY_RUN"
    leg: int               # 1 for single-order or close-leg; 2 for open-leg of a crossing


@dataclass
class FillRecord:
    """One executed (or attempted) order leg with cost markings."""

    ticker: str
    order_id: str
    side: str
    position_intent: str
    intended_notional: float
    filled_notional: float
    fill_price: float
    simulated_cost: float
    status: str            # "FILLED" | "TIMEOUT" | "REJECTED_ALPACA" | "DRY_RUN" | "REJECTED_CAP" | "REJECTED_TRADED_NOTIONAL" | "REJECTED_MAX_ORDERS"
    guard_status: str


# ---------------------------------------------------------------- connection

def connect(dry_run: bool = DRY_RUN_DEFAULT) -> Optional[object]:
    """Build and return a TradingClient pointed at the paper endpoint.

    Returns None in dry-run mode (no Alpaca calls are ever made when
    dry_run=True, so no client is needed).

    Raises EnvironmentError if credentials are missing.
    Raises ImportError if alpaca-py is not installed.
    """
    if dry_run:
        return None

    if not _ALPACA_AVAILABLE:
        raise ImportError(
            "alpaca-py is required for live paper execution: pip install alpaca-py"
        )

    key = os.environ.get("ALPACA_PAPER_API_KEY")
    secret = os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        raise EnvironmentError(
            "ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY must be set "
            "as environment variables -- never hardcoded or read from a file"
        )

    return TradingClient(
        api_key=key,
        secret_key=secret,
        paper=True,
        url_override=PAPER_ENDPOINT,
    )


# ---------------------------------------------------------------- positions

def get_current_positions(
    client, dry_run: bool = DRY_RUN_DEFAULT
) -> dict[str, float]:
    """Return {ticker: signed_notional} for the 8-name universe.

    Positive = long, negative = short, absent tickers = 0.
    In dry-run mode returns an empty dict without calling Alpaca.
    """
    if dry_run:
        return {}

    positions = client.get_all_positions()
    result: dict[str, float] = {}
    for pos in positions:
        sym = pos.symbol
        if sym not in UNIVERSE:
            continue
        mv = abs(float(pos.market_value))
        if pos.side == PositionSide.LONG or str(pos.side) == "long":
            result[sym] = mv
        else:
            result[sym] = -mv
    return result


# ---------------------------------------------------------------- order translation

def compute_delta_orders(
    target_weights: dict[str, float],
    current_notionals: dict[str, float],
    paper_nav: float = PAPER_NAV_DEFAULT,
) -> list[OrderSpec]:
    """Translate signed target weights into OrderSpec legs.

    Zero-crossing generates two legs: close the existing position first,
    then open the new position in the opposite direction (P4: target weights
    must use data through yesterday's close -- this function receives them
    already computed).

    guard_status is set to 'PENDING' here; the guard layer updates it.
    """
    orders: list[OrderSpec] = []

    for ticker in UNIVERSE:
        target_w = target_weights.get(ticker, 0.0)
        if target_w != target_w:  # NaN check (warmup)
            target_w = 0.0

        target_n = target_w * paper_nav
        current_n = current_notionals.get(ticker, 0.0)
        delta_n = target_n - current_n

        if abs(delta_n) < DELTA_MIN_NOTIONAL:
            continue

        same_sign = (current_n > 0 and target_n > 0) or (current_n < 0 and target_n < 0)
        crossing = current_n != 0.0 and not same_sign and target_n != 0.0
        closing_flat = current_n != 0.0 and target_n == 0.0

        if crossing:
            # Leg 1: close existing position
            if current_n > 0:
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="sell",
                    notional=abs(current_n),
                    position_intent="sell_to_close",
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=1,
                ))
                # Leg 2: open new short
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="sell",
                    notional=abs(target_n),
                    position_intent="sell_to_open",
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=2,
                ))
            else:
                # current_n < 0: close existing short
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="buy",
                    notional=abs(current_n),
                    position_intent="buy_to_close",
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=1,
                ))
                # open new long
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="buy",
                    notional=abs(target_n),
                    position_intent="buy_to_open",
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=2,
                ))

        elif closing_flat:
            side = "sell" if current_n > 0 else "buy"
            intent = "sell_to_close" if current_n > 0 else "buy_to_close"
            orders.append(OrderSpec(
                ticker=ticker,
                side=side,
                notional=abs(current_n),
                position_intent=intent,
                target_notional=0.0,
                guard_status="PENDING",
                leg=1,
            ))

        else:
            # Same-sign adjustment or fresh open (no zero-crossing)
            if delta_n > 0:
                # buying more: opening a fresh long, or adding to an existing long,
                # or reducing an existing short (current < 0, target < 0, delta > 0)
                if current_n < 0:
                    intent = "buy_to_close"  # reducing a short
                else:
                    intent = "buy_to_open"   # fresh or adding to long
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="buy",
                    notional=abs(delta_n),
                    position_intent=intent,
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=1,
                ))
            else:
                # selling: opening a fresh short, adding to existing short,
                # or reducing a long (current > 0, target > 0, delta < 0)
                if current_n > 0:
                    intent = "sell_to_close"  # reducing a long
                else:
                    intent = "sell_to_open"   # fresh or adding to short
                orders.append(OrderSpec(
                    ticker=ticker,
                    side="sell",
                    notional=abs(delta_n),
                    position_intent=intent,
                    target_notional=target_n,
                    guard_status="PENDING",
                    leg=1,
                ))

    return orders


# ---------------------------------------------------------------- guard layer

def apply_guards(
    orders: list[OrderSpec],
    dry_run: bool = DRY_RUN_DEFAULT,
    _cap_pct: float = MAX_POSITION_PCT_OF_NAV,
    _nav: float = PAPER_NAV_DEFAULT,
    _max_traded: float = MAX_TRADED_NOTIONAL_PER_RUN,
    _max_orders: int = MAX_ORDERS_PER_RUN,
) -> list[OrderSpec]:
    """Apply fail-safe guards to a pending order list, in order.

    Crossings (leg=1 close + leg=2 open for the same ticker) are evaluated
    as a unit so that a guard rejection is always all-or-nothing: you cannot
    end up half-crossed (one leg fired, the other blocked) under any guard.

    Guard 1 (position-size cap) and Guard 2 (traded-notional brake) use
    deliberately different units:
      - The cap (_cap_pct * _nav) is NAV-relative: it limits how large a
        single position can be as a fraction of the book.  Pass the current
        account equity or configured paper NAV as _nav so the cap scales
        with actual book size.
      - The brake (_max_traded) is absolute: it is a fat-finger throughput
        limit on the total dollar volume a single run can transact, regardless
        of book size.

    Guards applied in priority order:

    1. POSITION-SIZE CAP (_cap_pct * _nav): checks abs(target_notional).
       Both legs of a crossing share the same target_notional, so one check
       covers both.  Rejection reason: REJECTED_CAP.

    2. TRADED-NOTIONAL BRAKE (_max_traded): checks whether adding this
       group's summed leg notionals to the run's accumulated total would
       exceed the limit.  For a crossing the group total is abs(close_leg) +
       abs(open_leg), which is larger than the target alone.  This is the
       guard the position-size cap alone would miss.  Rejection reason:
       REJECTED_TRADED_NOTIONAL.

    3. MAX-ORDERS: checks whether adding this group's leg count to the run's
       submitted count would exceed MAX_ORDERS_PER_RUN.  Rejection reason:
       REJECTED_MAX_ORDERS.

    4. DRY_RUN: any group that passed all content guards becomes DRY_RUN.
       Applied last so guards 1-3 are still visible and auditable in dry-run
       (a rejected group shows its real rejection reason, not DRY_RUN).

    Override arguments (_cap_pct, _nav, _max_traded, _max_orders) default to
    module-level constants and can be set in tests without patching globals.
    In production, pass the live account equity as _nav.
    """
    # Group consecutive PENDING specs for the same ticker. Crossings produce
    # leg=1 then leg=2 consecutively for the same ticker; non-crossings
    # produce a single leg=1. Pre-rejected specs are always singleton groups.
    groups: list[list[OrderSpec]] = []
    for spec in orders:
        if (
            groups
            and groups[-1][0].guard_status == "PENDING"
            and spec.guard_status == "PENDING"
            and groups[-1][0].ticker == spec.ticker
        ):
            groups[-1].append(spec)
        else:
            groups.append([spec])

    result: list[OrderSpec] = []
    submitted_count: int = 0
    traded_notional_total: float = 0.0

    for group in groups:
        # Pass through specs that were already rejected upstream.
        if group[0].guard_status != "PENDING":
            result.extend(group)
            continue

        target_notional = group[0].target_notional  # same for all legs in group
        group_traded = sum(spec.notional for spec in group)
        group_size = len(group)
        cap_notional = _cap_pct * _nav

        # Guard 1: position-size cap (NAV-relative destination position limit)
        if abs(target_notional) > cap_notional:
            for spec in group:
                logger.warning(
                    "REJECTED_CAP: %s target_notional=%.2f exceeds cap=%.2f"
                    " (%.0f%% of NAV=%.0f)",
                    spec.ticker, target_notional, cap_notional,
                    _cap_pct * 100, _nav,
                )
                result.append(_replace_status(spec, "REJECTED_CAP"))
            continue

        # Guard 2: traded-notional brake (transaction throughput limit)
        # Crossing evaluated as a unit: if either leg would push the run
        # total over the limit, neither fires.
        if traded_notional_total + group_traded > _max_traded:
            for spec in group:
                logger.warning(
                    "REJECTED_TRADED_NOTIONAL: %s group_traded=%.2f would push "
                    "run_total=%.2f over brake=%.2f",
                    spec.ticker, group_traded, traded_notional_total, _max_traded,
                )
                result.append(_replace_status(spec, "REJECTED_TRADED_NOTIONAL"))
            continue

        # Guard 3: max orders per run (also all-or-nothing for crossings)
        if submitted_count + group_size > _max_orders:
            for spec in group:
                logger.warning(
                    "REJECTED_MAX_ORDERS: max %d orders per run reached, blocking %s",
                    _max_orders, spec.ticker,
                )
                result.append(_replace_status(spec, "REJECTED_MAX_ORDERS"))
            continue

        # Guard 4: dry-run -- applied last so guards 1-3 remain auditable.
        # Accumulate counts even in dry-run so subsequent groups see the
        # correct running totals (dry-run simulates live ordering faithfully).
        if dry_run:
            for spec in group:
                result.append(_replace_status(spec, "DRY_RUN"))
            submitted_count += group_size
            traded_notional_total += group_traded
            continue

        for spec in group:
            result.append(spec)
        submitted_count += group_size
        traded_notional_total += group_traded

    return result


def _replace_status(spec: OrderSpec, status: str) -> OrderSpec:
    from dataclasses import replace
    return replace(spec, guard_status=status)


# ---------------------------------------------------------------- submission

def submit_orders(client, orders: list[OrderSpec]) -> list[str]:
    """Submit PENDING orders to Alpaca paper and return a list of order IDs.

    Only orders with guard_status='PENDING' are submitted. All others are
    skipped. Returns [] in dry-run (no PENDING orders should exist if
    apply_guards was called with dry_run=True).
    """
    order_ids: list[str] = []
    for spec in orders:
        if spec.guard_status != "PENDING":
            continue

        pi = PositionIntent(spec.position_intent)
        side = OrderSide.BUY if spec.side == "buy" else OrderSide.SELL

        req = MarketOrderRequest(
            symbol=spec.ticker,
            notional=round(spec.notional, 2),
            side=side,
            time_in_force=TimeInForce.DAY,
            position_intent=pi,
        )
        order = client.submit_order(order_data=req)
        logger.info(
            "submitted %s %s %.2f notional, order_id=%s",
            spec.side, spec.ticker, spec.notional, order.id,
        )
        order_ids.append(str(order.id))

    return order_ids


def poll_fills(client, order_ids: list[str]) -> dict[str, object]:
    """Poll Alpaca until all orders reach a terminal state or timeout.

    Returns {order_id: Order} for all polled orders.
    """
    import time

    remaining = set(order_ids)
    filled: dict[str, object] = {}
    deadline = time.monotonic() + FILL_POLL_TIMEOUT_SECS

    while remaining and time.monotonic() < deadline:
        for oid in list(remaining):
            order = client.get_order_by_id(oid)
            if order.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
            ):
                filled[oid] = order
                remaining.discard(oid)
        if remaining:
            time.sleep(FILL_POLL_INTERVAL_SECS)

    for oid in remaining:
        logger.warning("order %s timed out waiting for terminal state", oid)
        filled[oid] = None

    return filled


# ---------------------------------------------------------------- fill records

def build_fill_records(
    orders: list[OrderSpec],
    submitted_ids: list[str],
    fill_data: dict[str, object],
) -> list[FillRecord]:
    """Construct FillRecord entries for all orders (including rejected ones).

    P1: every order -- submitted, rejected, or dry-run -- appears in the
    fill records. Silent drops are the most dangerous failure mode and are
    explicitly guarded here.
    """
    records: list[FillRecord] = []
    id_iter = iter(submitted_ids)

    for spec in orders:
        if spec.guard_status == "PENDING":
            oid = next(id_iter, "UNKNOWN")
            order = fill_data.get(oid)

            if order is None:
                status = "TIMEOUT"
                filled_n = 0.0
                fill_price = 0.0
            elif hasattr(order, "status") and order.status == OrderStatus.FILLED:
                status = "FILLED"
                fill_price = float(order.filled_avg_price or 0)
                filled_n = float(order.filled_qty or 0) * fill_price
            else:
                status = "REJECTED_ALPACA"
                filled_n = 0.0
                fill_price = 0.0

            records.append(FillRecord(
                ticker=spec.ticker,
                order_id=oid,
                side=spec.side,
                position_intent=spec.position_intent,
                intended_notional=spec.notional,
                filled_notional=filled_n,
                fill_price=fill_price,
                simulated_cost=0.0,  # marked in next step
                status=status,
                guard_status=spec.guard_status,
            ))
        else:
            # rejected or dry-run: appears in record with zero fill
            records.append(FillRecord(
                ticker=spec.ticker,
                order_id="",
                side=spec.side,
                position_intent=spec.position_intent,
                intended_notional=spec.notional,
                filled_notional=0.0,
                fill_price=0.0,
                simulated_cost=0.0,
                status=spec.guard_status,
                guard_status=spec.guard_status,
            ))

    return records


# ---------------------------------------------------------------- cost marking

def mark_costs(
    fills: list[FillRecord],
    current_short_notionals: dict[str, float],
    cost_params: CostParams = CostParams(),
) -> list[FillRecord]:
    """Apply v6.5 cost model to each fill (P5).

    simulated_cost = (half_spread_bp + slippage_bp) * 1e-4 * filled_notional
                     + borrow_annual / 252 * short_notional_held_today
    """
    _BP = 1e-4
    for fill in fills:
        if fill.filled_notional == 0.0:
            continue
        trading_cost = (
            (cost_params.half_spread_bp + cost_params.slippage_bp)
            * _BP
            * fill.filled_notional
        )
        short_n = abs(current_short_notionals.get(fill.ticker, 0.0))
        borrow = cost_params.borrow_annual / 252 * short_n
        fill.simulated_cost = trading_cost + borrow

    return fills


# ---------------------------------------------------------------- reconciliation

def reconcile(
    orders: list[OrderSpec],
    fills: list[FillRecord],
    run_date: Optional[date] = None,
) -> dict:
    """Compare fill results against intended orders and write a log file (P1).

    Returns a per-ticker summary dict. Every order appears -- no silent
    drops (P1).
    """
    run_date = run_date or date.today()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    by_ticker: dict[str, dict] = {}

    for fill in fills:
        t = fill.ticker
        if t not in by_ticker:
            by_ticker[t] = {
                "legs": [],
                "total_intended": 0.0,
                "total_filled": 0.0,
                "total_cost": 0.0,
            }
        discrepancy = fill.filled_notional - fill.intended_notional
        abs_tol = max(RECONCILE_ABS_TOL, RECONCILE_REL_TOL * abs(fill.intended_notional))
        flagged = abs(discrepancy) > abs_tol
        by_ticker[t]["legs"].append({
            "order_id": fill.order_id,
            "side": fill.side,
            "position_intent": fill.position_intent,
            "intended_notional": round(fill.intended_notional, 4),
            "filled_notional": round(fill.filled_notional, 4),
            "fill_price": round(fill.fill_price, 4),
            "discrepancy": round(discrepancy, 4),
            "flagged": flagged,
            "simulated_cost": round(fill.simulated_cost, 6),
            "status": fill.status,
            "guard_status": fill.guard_status,
        })
        by_ticker[t]["total_intended"] += fill.intended_notional
        by_ticker[t]["total_filled"] += fill.filled_notional
        by_ticker[t]["total_cost"] += fill.simulated_cost

    flagged_count = sum(
        1 for td in by_ticker.values() for leg in td["legs"] if leg["flagged"]
    )

    report = {
        "date": str(run_date),
        "generated_at": datetime.utcnow().isoformat(),
        "total_legs_intended": len(orders),
        "total_fills_captured": len(fills),
        "flagged_discrepancies": flagged_count,
        "by_ticker": by_ticker,
    }

    log_path = LOG_DIR / f"reconciliation_{run_date}.json"
    with open(log_path, "w") as fh:
        json.dump(report, fh, indent=2)

    if flagged_count > 0:
        logger.warning(
            "reconciliation: %d flagged discrepancies -- inspect %s",
            flagged_count, log_path,
        )
    else:
        logger.info("reconciliation: clean run, 0 flagged discrepancies -> %s", log_path)

    return report
