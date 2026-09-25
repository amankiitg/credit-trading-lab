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
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
    from alpaca.common.exceptions import APIError
    from alpaca.trading.requests import MarketOrderRequest
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False
    TradingClient = None  # type: ignore
    APIError = None  # type: ignore

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
# Override via env var MAX_TRADED_NOTIONAL_PER_RUN for the initial portfolio build
# (all positions from zero requires ~200k gross notional on a 100k book).
# Default 16_000 is right for incremental rebalancing; raise to 250_000 once.
MAX_TRADED_NOTIONAL_PER_RUN: float = float(
    os.environ.get("MAX_TRADED_NOTIONAL_PER_RUN", "16000")
)
MAX_ORDERS_PER_RUN: int = 20
DELTA_MIN_NOTIONAL: float = 250.0  # matches Panel H display threshold
DUST_THRESHOLD_USD: float = 1.0  # positions below this are closed via close_position
DRY_RUN_DEFAULT: bool = os.environ.get("DRY_RUN_DEFAULT", "true").lower() != "false"
PAPER_NAV_DEFAULT: float = 100_000.0
FILL_POLL_TIMEOUT_SECS: int = 30
FILL_POLL_INTERVAL_SECS: float = 1.0
RECONCILE_ABS_TOL: float = 10.0
RECONCILE_REL_TOL: float = 0.005

# Short-side position intents: Alpaca paper rejects fractional notional sell_to_open
# ("fractional orders cannot be sold short"). These intents MUST use integer qty.
# Long-side intents (buy_to_open, sell_to_close closing a long) continue to use
# notional. The resulting asymmetry is expected and intentional: longs are
# notional-precise, shorts are quantized to whole shares.
_SHORT_QTY_INTENTS: frozenset[str] = frozenset({"sell_to_open", "buy_to_close"})

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- reason codes
#
# Every leg that does not end up as a submitted order carries one of these
# stable codes. They ARE the audit trail: Alpaca does not persist a submit-time
# rejection as an order record (verified 2026-09-24 -- the rejected LQD
# sell_to_open left no trace in the order history at all), so this code, written
# to Supabase order_rejections and to the reconciliation JSON, is the only
# durable evidence that the leg was ever intended.

REASON_QTY_ZERO: str = "QTY_ROUNDS_TO_ZERO"
GUARD_SKIPPED_NOT_SHORTABLE: str = "SKIPPED_NOT_SHORTABLE"  # blocked before submit
GUARD_CROSSING_FLAT_NOT_SHORTABLE: str = "CROSSING_FLAT_NOT_SHORTABLE"  # open leg of a crossing
REASON_NOT_SHORTABLE_AT_SUBMIT: str = "ASSET_NOT_SHORTABLE_AT_SUBMIT"
REASON_SHORTABLE_CHECK_FAILED: str = "SHORTABLE_CHECK_FAILED"
REASON_ALPACA_ERROR: str = "ALPACA_API_ERROR"
REASON_SUBMIT_EXCEPTION: str = "SUBMIT_EXCEPTION_UNKNOWN_STATE"
REASON_SKIPPED_AFTER_HALT: str = "SKIPPED_AFTER_HALT"


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
    status: str            # "FILLED" | "TIMEOUT" | "DRY_RUN" | a guard_status
                           # (REJECTED_CAP, ...) | a reason code for a leg that
                           # failed or was skipped before or at submission
    guard_status: str
    leg: int = 1
    reason_code: str = ""   # stable code for a non-submitted leg; "" if submitted
    detail: str = ""        # broker message or skip explanation for that code
    backfilled: bool = False  # True when reconstructed after a crash, not at run time


@dataclass(frozen=True)
class SubmitOutcome:
    """Result of attempting exactly one PENDING order leg.

    One outcome is produced per PENDING spec, in submission order, so a caller
    can never lose a leg (P1). `order_id` is set only when `status` is
    "SUBMITTED"; for every other status `reason_code` explains why not.
    """

    ticker: str
    leg: int
    side: str
    position_intent: str
    requested_notional: float
    order_id: str = ""
    status: str = "SUBMITTED"   # SUBMITTED | SKIPPED | REJECTED | UNKNOWN | NOT_ATTEMPTED
    reason_code: str = ""
    detail: str = ""
    halts_run: bool = False     # True only for unknown-state (transport) failures

    @property
    def submitted(self) -> bool:
        return self.status == "SUBMITTED"


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


# ---------------------------------------------------------------- live NAV

def get_live_nav(client) -> float:
    """Read the live account equity from Alpaca and return it as a float.

    Used to anchor the NAV-relative position-size cap to the actual book size.
    Falls back to PAPER_NAV_DEFAULT on any error so the guard still runs.
    """
    try:
        account = client.get_account()
        equity = float(account.equity)
        if equity <= 0:
            raise ValueError(f"non-positive equity: {equity}")
        logger.info("live NAV from Alpaca: %.2f", equity)
        return equity
    except Exception as exc:
        logger.warning(
            "get_live_nav failed, falling back to PAPER_NAV_DEFAULT=%.0f: %s",
            PAPER_NAV_DEFAULT, exc,
        )
        return PAPER_NAV_DEFAULT


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


def diff_positions(
    live_notionals: dict[str, float],
    cached_notionals: dict[str, float],
) -> list[dict]:
    """Per-ticker diff of live vs cached signed notionals over the whole UNIVERSE.

    Pure function, no I/O, no threshold applied to the row set: returns one row
    per UNIVERSE ticker in UNIVERSE order so a reconciliation report can show
    the entire book rather than only the gaps. Each row is
    {ticker, cached, live, diff (live - cached), material}.

    `material` uses DELTA_MIN_NOTIONAL -- the same threshold execution uses to
    decide whether a delta is worth trading -- so a row marked material is one
    the next run would treat as a real position, not day-to-day price noise.
    """
    rows: list[dict] = []
    for ticker in UNIVERSE:
        cached = float(cached_notionals.get(ticker, 0.0))
        live = float(live_notionals.get(ticker, 0.0))
        diff = live - cached
        rows.append({
            "ticker": ticker,
            "cached": cached,
            "live": live,
            "diff": diff,
            "material": abs(diff) >= DELTA_MIN_NOTIONAL,
        })
    return rows


def check_position_drift(
    client,
    cached_notionals: dict[str, float],
    dry_run: bool = DRY_RUN_DEFAULT,
) -> dict[str, dict[str, float]]:
    """Diff the frozen Supabase position snapshot against live Alpaca positions.

    compute_delta_orders() intentionally uses the frozen Supabase snapshot,
    not a fresh Alpaca read, so that what a user approved in Panel H is
    exactly what executes (see run_execution.py step 6). That means if the
    broker account ever diverges from that cache outside of normal order
    flow -- a manual paper-account reset, a dropped write, anything -- the
    delta math silently computes against a phantom book with no signal that
    anything was wrong. This surfaces that drift *before* delta computation
    so it can be alerted on, without changing execution behaviour itself.

    Returns {ticker: {"cached": ..., "live": ..., "diff": live - cached}}
    for every UNIVERSE ticker where the two disagree by at least
    DELTA_MIN_NOTIONAL. Empty when nothing has drifted, including when
    dry_run=True (no live account to compare against).
    """
    if dry_run:
        return {}

    live_notionals = get_current_positions(client, dry_run=False)
    return {
        row["ticker"]: {
            "cached": row["cached"],
            "live": row["live"],
            "diff": row["diff"],
        }
        for row in diff_positions(live_notionals, cached_notionals)
        if row["material"]
    }


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


# ---------------------------------------------------------------- shortability

def shorts_requiring_check(orders: list[OrderSpec]) -> list[str]:
    """Tickers with a PENDING leg that would open or increase a short.

    Only sell_to_open does that. buy_to_close reduces or closes a short, and
    sell_to_close reduces or closes a long, so neither depends on the name
    being shortable: a restriction on opening shorts must never block getting
    out of one.
    """
    tickers: list[str] = []
    for spec in orders:
        if spec.guard_status != "PENDING":
            continue
        if spec.position_intent != "sell_to_open":
            continue
        if spec.ticker not in tickers:
            tickers.append(spec.ticker)
    return tickers


def get_shortable_flags(client, tickers: list[str]) -> dict[str, bool]:
    """Read the Alpaca `shortable` flag for each ticker, one asset call each.

    Returns {ticker: bool}. A ticker whose lookup fails is reported as False:
    this is a guard, and guards in this module are conservative. A skipped
    short is logged, tracked, and recoverable on the next run, whereas
    submitting an unverified short risks the 42210000 abort that killed the
    2026-09-24 run.

    Live evidence that this cannot be cached as a static list: LQD filled
    sell_to_open on 2026-09-01, 09-03 and 09-14, then reported
    shortable=false on 2026-09-24.
    """
    flags: dict[str, bool] = {}
    for ticker in tickers:
        try:
            asset = client.get_asset(ticker)
            flags[ticker] = bool(getattr(asset, "shortable", False))
        except Exception as exc:
            logger.error(
                "%s: %s -- asset lookup failed, treating as not shortable: %s",
                REASON_SHORTABLE_CHECK_FAILED, ticker, exc,
            )
            flags[ticker] = False
    return flags


def _is_crossing_open_leg(spec: OrderSpec, orders: list[OrderSpec]) -> bool:
    """True when this sell_to_open leg is the second half of a long-to-short crossing.

    A crossing closes an existing long (leg 1, sell_to_close) before opening the
    short (leg 2, sell_to_open). If the open leg is blocked, leg 1 has usually
    already run or will run, so the name ends up FLAT rather than short.
    """
    if spec.position_intent != "sell_to_open":
        return False
    return any(
        o.ticker == spec.ticker
        and o.position_intent == "sell_to_close"
        and o.leg == 1
        for o in orders
    )


def apply_shortable_filter(
    orders: list[OrderSpec],
    shortable: dict[str, bool],
) -> list[OrderSpec]:
    """Block PENDING legs that would open or increase a short in a name that is
    not shortable, and leave every other leg untouched.

    Pure function, no I/O, so the policy is testable without a broker. A blocked
    leg keeps its position in the order list, so it still reaches the fill
    records, the reconciliation JSON, and the Supabase order_rejections audit
    trail.

    The reason code distinguishes the two outcomes, because they leave the book
    in different places and attribution has to be able to tell them apart:
      - SKIPPED_NOT_SHORTABLE: an ordinary short leg. The name simply does not
        get shorter (or does not get short at all).
      - CROSSING_FLAT_NOT_SHORTABLE: the open leg of a long-to-short crossing,
        whose sell_to_close leg still runs. The name ends FLAT, which is closer
        to a short target than staying long, so it is the smaller deviation.

    Policy consequence, recorded in the sprint notes as a known live constraint:
    the wanted short is missing and tracked, never silently approximated.
    """
    result: list[OrderSpec] = []
    for spec in orders:
        if (
            spec.guard_status == "PENDING"
            and spec.position_intent == "sell_to_open"
            and not shortable.get(spec.ticker, False)
        ):
            crossing = _is_crossing_open_leg(spec, orders)
            reason = (
                GUARD_CROSSING_FLAT_NOT_SHORTABLE if crossing
                else GUARD_SKIPPED_NOT_SHORTABLE
            )
            logger.warning(
                "%s: %s cannot be sold short -- skipping sell_to_open leg=%d "
                "(~$%.0f)%s",
                reason, spec.ticker, spec.leg, spec.notional,
                "; its sell_to_close leg still runs, so the name ends FLAT not short"
                if crossing else "",
            )
            result.append(_replace_status(spec, reason))
            continue
        result.append(spec)
    return result


# ---------------------------------------------------------------- submission

def classify_submit_failure(exc: BaseException) -> tuple[str, bool]:
    """Map a submit-time exception to (reason_code, halts_run).

    A business rejection (the broker evaluated the order and declined it) is a
    per-leg fact: the rest of the book should still trade, so halts_run=False.
    Anything else, meaning a connection reset, timeout or auth failure, leaves
    the order's fate unknown because Alpaca may or may not have received it.
    Halting is then the only state we can reason about afterwards, so it returns
    halts_run=True. alpaca-py exposes no distinct transport exception class, so
    "not an APIError" is the signal for that unknown-state case.

    42210000 is Alpaca's "asset cannot be sold short" code. The LQD leg died on
    it on 2026-09-24 before this classifier existed.
    """
    is_api_error = APIError is not None and isinstance(exc, APIError)

    code = None
    try:
        code = exc.code  # type: ignore[attr-defined]
    except Exception:
        code = None

    message = str(exc)
    try:
        message = exc.message  # type: ignore[attr-defined]
    except Exception:
        pass

    lowered = message.lower()
    if (
        code == 42210000
        or "sold short" in lowered
        or "not shortable" in lowered
    ):
        return REASON_NOT_SHORTABLE_AT_SUBMIT, False
    if is_api_error:
        return REASON_ALPACA_ERROR, False
    return REASON_SUBMIT_EXCEPTION, True


def _failure_outcome(spec: OrderSpec, exc: BaseException) -> SubmitOutcome:
    """Build and log the outcome for a leg that raised on the way to Alpaca."""
    reason, halts = classify_submit_failure(exc)
    detail = f"{type(exc).__name__}: {exc}"
    logger.error(
        "%s: %s leg=%d intent=%s notional=%.2f -- %s",
        reason, spec.ticker, spec.leg, spec.position_intent, spec.notional, detail,
    )
    return SubmitOutcome(
        ticker=spec.ticker,
        leg=spec.leg,
        side=spec.side,
        position_intent=spec.position_intent,
        requested_notional=spec.notional,
        status="UNKNOWN" if halts else "REJECTED",
        reason_code=reason,
        detail=detail,
        halts_run=halts,
    )


def submit_orders(
    client,
    orders: list[OrderSpec],
    close_prices: dict[str, float] | None = None,
) -> list[SubmitOutcome]:
    """Submit PENDING orders one leg at a time, isolating per-leg failures.

    Only orders with guard_status='PENDING' are submitted. Every PENDING spec
    produces exactly one SubmitOutcome, in order, so no leg is silently dropped
    (P1). A leg the broker rejects is classified and recorded, and the run
    continues through the remaining legs: one bad order must not abort the book
    or strand it half-traded. That is the 2026-09-24 failure: an unhandled
    42210000 on LQD killed the run after 5 of 7 legs, so GLD was never attempted
    and nothing was written back.

    The single deliberate exception is a transport-class failure, where the
    order's fate is unknown. Submission stops there, every remaining PENDING leg
    is recorded as NOT_ATTEMPTED with reason SKIPPED_AFTER_HALT, and
    run_execution.py turns that into a clean halt with full state written.

    Short-side intents (sell_to_open, buy_to_close) are submitted as integer
    qty (whole shares) because Alpaca paper rejects fractional sell_to_open.
    sell_to_close (closing a long before a zero-crossing) uses close_position()
    so Alpaca closes the exact fractional shares it holds -- avoids the
    notional->qty rounding mismatch that causes 403 insufficient-qty errors.
    Plain long reductions (notional sell, no zero-crossing) continue to use
    notional. close_prices is required for the qty conversion; if absent,
    short orders are submitted as notional (which may fail on paper).

    Never raises for a per-leg failure. `outcome.order_id` is set only for
    status == "SUBMITTED".
    """
    outcomes: list[SubmitOutcome] = []
    pending = [spec for spec in orders if spec.guard_status == "PENDING"]
    halted = False

    for spec in pending:
        if halted:
            logger.error(
                "SKIPPED_AFTER_HALT: not attempting %s leg=%d -- an earlier leg "
                "failed with unknown submission state",
                spec.ticker, spec.leg,
            )
            outcomes.append(SubmitOutcome(
                ticker=spec.ticker,
                leg=spec.leg,
                side=spec.side,
                position_intent=spec.position_intent,
                requested_notional=spec.notional,
                status="NOT_ATTEMPTED",
                reason_code=REASON_SKIPPED_AFTER_HALT,
                detail="run halted after an earlier transport failure",
            ))
            continue

        outcome = _submit_one_safe(client, spec, pending, close_prices)
        outcomes.append(outcome)
        if outcome.halts_run:
            halted = True

    return outcomes


def _submit_one_safe(
    client,
    spec: OrderSpec,
    pending: list[OrderSpec],
    close_prices: dict[str, float] | None,
) -> SubmitOutcome:
    """Attempt one leg, converting any exception into a classified outcome."""
    try:
        return _submit_one(client, spec, pending, close_prices)
    except Exception as exc:  # noqa: BLE001 -- by design, no leg may kill the run
        return _failure_outcome(spec, exc)


def _submit_one(
    client,
    spec: OrderSpec,
    pending: list[OrderSpec],
    close_prices: dict[str, float] | None,
) -> SubmitOutcome:
    """One leg, no exception handling. Callers go through _submit_one_safe."""
    intent = spec.position_intent

    # sell_to_close leg=1 of a zero-crossing: close the ENTIRE long position
    # using close_position() so Alpaca closes the exact fractional shares held.
    # Then block until filled before the open leg (leg=2 sell_to_open) runs.
    # Only applies to zero-crossings (leg=1 with a corresponding leg=2 for
    # the same ticker). Partial reductions (leg=1, no leg=2) use notional.
    _is_zero_cross_close = (
        intent == "sell_to_close"
        and spec.leg == 1
        and any(o.ticker == spec.ticker and o.leg == 2 for o in pending)
    )
    if _is_zero_cross_close:
        import time
        resp = client.close_position(spec.ticker)
        order_id = str(resp.id)
        logger.info(
            "close_position %s accepted order_id=%s (~$%.0f)",
            spec.ticker, order_id, spec.notional,
        )
        # Wait for the close to reach a terminal state before continuing. A
        # polling failure is not a submission failure: the close is in flight,
        # so log it and let the fill poll resolve what actually happened.
        try:
            deadline = time.monotonic() + FILL_POLL_TIMEOUT_SECS
            while time.monotonic() < deadline:
                o = client.get_order_by_id(order_id)
                if o.status in (
                    OrderStatus.FILLED, OrderStatus.CANCELED,
                    OrderStatus.EXPIRED, OrderStatus.REJECTED,
                ):
                    logger.info(
                        "close_position %s settled: status=%s", spec.ticker, o.status,
                    )
                    break
                time.sleep(FILL_POLL_INTERVAL_SECS)
            else:
                logger.warning(
                    "close_position %s did not settle within %ds -- proceeding anyway",
                    spec.ticker, FILL_POLL_TIMEOUT_SECS,
                )
        except Exception as exc:
            logger.warning(
                "close_position %s accepted but settle-polling failed: %s",
                spec.ticker, exc,
            )
        return SubmitOutcome(
            ticker=spec.ticker,
            leg=spec.leg,
            side=spec.side,
            position_intent=intent,
            requested_notional=spec.notional,
            order_id=order_id,
            status="SUBMITTED",
        )

    pi = PositionIntent(intent)
    side = OrderSide.BUY if spec.side == "buy" else OrderSide.SELL
    use_qty = intent in _SHORT_QTY_INTENTS

    if use_qty and close_prices is not None:
        price = close_prices.get(spec.ticker, 0.0)
        qty = int(spec.notional / price) if price > 0 else 0  # floor to whole shares
        if qty == 0:
            logger.warning(
                "QTY_ROUNDS_TO_ZERO: %s %s notional=%.2f price=%.2f -> qty=0",
                intent, spec.ticker, spec.notional, price,
            )
            return SubmitOutcome(
                ticker=spec.ticker,
                leg=spec.leg,
                side=spec.side,
                position_intent=intent,
                requested_notional=spec.notional,
                status="SKIPPED",
                reason_code=REASON_QTY_ZERO,
                detail=f"floor({spec.notional:.2f}/{price:.2f}) = 0 whole shares",
            )
        req = MarketOrderRequest(
            symbol=spec.ticker,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            position_intent=pi,
        )
        logger.info(
            "submitted (qty) %s %s qty=%d (~$%.0f), order_id pending",
            spec.side, spec.ticker, qty, spec.notional,
        )
    else:
        req = MarketOrderRequest(
            symbol=spec.ticker,
            notional=round(spec.notional, 2),
            side=side,
            time_in_force=TimeInForce.DAY,
            position_intent=pi,
        )
        logger.info(
            "submitted (notional) %s %s %.2f notional",
            spec.side, spec.ticker, spec.notional,
        )

    order = client.submit_order(order_data=req)
    logger.info(
        "order accepted: %s %s order_id=%s",
        spec.side, spec.ticker, order.id,
    )
    return SubmitOutcome(
        ticker=spec.ticker,
        leg=spec.leg,
        side=spec.side,
        position_intent=intent,
        requested_notional=spec.notional,
        order_id=str(order.id),
        status="SUBMITTED",
    )


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
    outcomes: list[SubmitOutcome],
    fill_data: dict[str, object],
) -> list[FillRecord]:
    """Construct FillRecord entries for all orders (including rejected ones).

    P1: every order -- submitted, rejected, skipped, or dry-run -- appears in the
    fill records. Silent drops are the most dangerous failure mode and are
    explicitly guarded here. A leg that passed the guards but produced no
    SubmitOutcome is a programming error and raises rather than being dropped.

    `outcomes` is one SubmitOutcome per PENDING spec, in submission order, as
    returned by submit_orders(). For a leg that never reached Alpaca, status is
    the outcome status (SKIPPED / REJECTED / UNKNOWN / NOT_ATTEMPTED) and
    reason_code carries the stable cause (see the REASON_* constants).
    """
    records: list[FillRecord] = []
    outcome_iter = iter(outcomes)

    for spec in orders:
        if spec.guard_status == "PENDING":
            outcome = next(outcome_iter, None)
            if outcome is None:
                raise ValueError(
                    f"no SubmitOutcome for PENDING leg {spec.ticker} "
                    f"leg={spec.leg} -- the leg would be silently dropped (P1)"
                )

            if not outcome.submitted:
                records.append(FillRecord(
                    ticker=spec.ticker,
                    order_id=outcome.order_id,
                    side=spec.side,
                    position_intent=spec.position_intent,
                    intended_notional=spec.notional,
                    filled_notional=0.0,
                    fill_price=0.0,
                    simulated_cost=0.0,
                    status=outcome.status,
                    guard_status=spec.guard_status,
                    leg=spec.leg,
                    reason_code=outcome.reason_code,
                    detail=outcome.detail,
                ))
                continue

            order = fill_data.get(outcome.order_id)
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
                order_id=outcome.order_id,
                side=spec.side,
                position_intent=spec.position_intent,
                intended_notional=spec.notional,
                filled_notional=filled_n,
                fill_price=fill_price,
                simulated_cost=0.0,  # marked in next step
                status=status,
                guard_status=spec.guard_status,
                leg=spec.leg,
            ))
        else:
            # rejected by a guard or dry-run: appears in record with zero fill
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
                leg=spec.leg,
                reason_code="" if spec.guard_status == "DRY_RUN" else spec.guard_status,
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


# ---------------------------------------------------------------- dust cleanup

def close_dust_positions(
    client,
    dry_run: bool = DRY_RUN_DEFAULT,
) -> list[str]:
    """Close any UNIVERSE position whose market value is below DUST_THRESHOLD_USD.

    Dust accumulates from whole-share rounding on short orders: floor(notional/price)
    leaves a fractional-dollar residual that cannot be submitted as a notional order
    (Alpaca rejects notional < $1). close_position() handles any remaining quantity
    regardless of size.

    Returns a list of tickers for which close_position was called.
    """
    if dry_run:
        return []

    try:
        all_positions = client.get_all_positions()
    except Exception as exc:
        logger.warning("close_dust_positions: get_all_positions failed: %s", exc)
        return []

    closed: list[str] = []
    for pos in all_positions:
        sym = pos.symbol
        if sym not in UNIVERSE:
            continue
        mv = abs(float(pos.market_value))
        if mv < DUST_THRESHOLD_USD:
            try:
                client.close_position(sym)
                logger.info(
                    "closed dust position: %s market_value=%.4f (below threshold %.2f)",
                    sym, mv, DUST_THRESHOLD_USD,
                )
                closed.append(sym)
            except Exception as exc:
                logger.warning("close_dust_positions: close_position(%s) failed: %s", sym, exc)

    return closed


# ---------------------------------------------------------------- attribution feed

def feed_attribution(
    fills: list[FillRecord],
    close_prices: dict[str, float],
    run_date: "date | None" = None,
    nav: float = PAPER_NAV_DEFAULT,
    parquet_path: str = "data/processed/attribution.parquet",
    backfilled: bool = False,
) -> int:
    """Translate paper fill records into v8.3-compatible rows and append to attribution.parquet.

    For each FILLED order, computes the day-of fill P&L components (price_change,
    carry=0 approximation, gross/net P&L, costs) and appends a row matching the v8.3
    tidy schema exactly. Factor-regression columns (directional, selection, beta_explained,
    residual, r_squared) are left as NaN -- they require rolling OLS history unavailable
    at single-day resolution.

    In dry-run (empty fills list or all non-FILLED), returns 0 without touching the file.
    Returns the number of rows appended.
    """
    import math
    import pandas as pd
    from pathlib import Path as _Path
    from signals.etf_universe import ASSET_CLASS

    run_date = run_date or date.today()
    run_date_str = str(run_date)

    _ATTRIBUTION_COLUMNS = [
        "date", "ticker", "asset_class", "weight", "pnl", "carry", "price_change",
        "gross_pnl", "net_pnl", "turnover_cost", "borrow_cost",
        "directional", "selection", "net_exposure", "beta_explained", "residual", "r_squared",
        "backfilled",
    ]

    rows = []
    for fill in fills:
        if fill.status != "FILLED":
            continue
        ticker = fill.ticker
        price = close_prices.get(ticker, 0.0)
        if fill.fill_price <= 0 or price <= 0:
            logger.warning(
                "feed_attribution: skipping %s -- fill_price=%.4f close=%.4f",
                ticker, fill.fill_price, price,
            )
            continue

        # Signed notional: positive for long (buy), negative for short (sell)
        signed_notional = fill.filled_notional if fill.side == "buy" else -fill.filled_notional
        weight = signed_notional / nav if nav > 0 else 0.0

        # Day's price return (intraday: fill_price to close)
        day_ret = (price - fill.fill_price) / fill.fill_price
        price_change = signed_notional * day_ret

        # Carry: dividend-based accrual is not tracked at fill granularity.
        # Using 0.0 here; the daily carry accrual is dominated by HYG/LQD coupons
        # that accrue in the historical frame but are not captured in single fills.
        carry = 0.0
        pnl = price_change + carry
        gross_pnl = pnl
        net_pnl = gross_pnl - fill.simulated_cost
        net_exposure = weight

        rows.append({
            "date": pd.Timestamp(run_date_str),
            "ticker": ticker,
            "asset_class": ASSET_CLASS.get(ticker, "unknown"),
            "weight": weight,
            "pnl": pnl,
            "carry": carry,
            "price_change": price_change,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "turnover_cost": fill.simulated_cost,
            "borrow_cost": 0.0,  # borrow accrual is in mark_costs; not re-split here
            "directional": math.nan,
            "selection": math.nan,
            "net_exposure": net_exposure,
            "beta_explained": math.nan,
            "residual": math.nan,
            "r_squared": math.nan,
            "backfilled": bool(backfilled or fill.backfilled),
        })

    if not rows:
        logger.info("feed_attribution: no filled orders to append for %s", run_date_str)
        return 0

    new_df = pd.DataFrame(rows, columns=_ATTRIBUTION_COLUMNS)

    p = _Path(parquet_path)
    if p.exists():
        existing = pd.read_parquet(p)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    # Historical rows predate the column, so they read back as NaN rather than
    # False. They were not backfilled, so normalise instead of leaving a float
    # column that a boolean flag is supposed to be.
    if "backfilled" in combined.columns:
        combined["backfilled"] = (
            combined["backfilled"].astype("boolean").fillna(False).astype(bool)
        )

    combined.to_parquet(p, index=False)
    logger.info(
        "feed_attribution: appended %d rows for %s -> %s",
        len(rows), run_date_str, p,
    )
    return len(rows)


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
        if fill.position_intent in _SHORT_QTY_INTENTS and fill.fill_price > 0:
            # Short legs are quantized to whole shares, so floor(notional/price)
            # can leave up to one share's value unallocated. That is the known
            # design (see v8.6 notes), not a discrepancy, so one share's value
            # is admitted rather than flagged.
            abs_tol = max(abs_tol, fill.fill_price)
        # A dry-run leg was never meant to reach the broker, so it has no fill
        # to be compared against. Flagging it would make every dry run look like
        # a catastrophe and hide the legs that genuinely did not execute.
        comparable = fill.guard_status != "DRY_RUN"
        flagged = comparable and abs(discrepancy) > abs_tol
        by_ticker[t]["legs"].append({
            "order_id": fill.order_id,
            "leg": fill.leg,
            "side": fill.side,
            "position_intent": fill.position_intent,
            "intended_notional": round(fill.intended_notional, 4),
            "filled_notional": round(fill.filled_notional, 4),
            "fill_price": round(fill.fill_price, 4),
            "discrepancy": round(discrepancy, 4),
            "flagged": flagged,
            "tolerance": round(abs_tol, 4),
            "simulated_cost": round(fill.simulated_cost, 6),
            "status": fill.status,
            "reason_code": fill.reason_code,
            "detail": fill.detail,
            "backfilled": fill.backfilled,
            "guard_status": fill.guard_status,
        })
        by_ticker[t]["total_intended"] += fill.intended_notional
        by_ticker[t]["total_filled"] += fill.filled_notional
        by_ticker[t]["total_cost"] += fill.simulated_cost

    flagged_count = sum(
        1 for td in by_ticker.values() for leg in td["legs"] if leg["flagged"]
    )
    rejection_count = sum(
        1 for td in by_ticker.values() for leg in td["legs"] if leg["reason_code"]
    )
    backfilled_count = sum(
        1 for td in by_ticker.values() for leg in td["legs"] if leg["backfilled"]
    )

    report = {
        "date": str(run_date),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_legs_intended": len(orders),
        "total_fills_captured": len(fills),
        "flagged_discrepancies": flagged_count,
        "rejected_or_skipped_legs": rejection_count,
        "backfilled_legs": backfilled_count,
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


# ---------------------------------------------------------------- rejection audit

def build_rejection_rows(
    fills: list[FillRecord],
    run_date: date,
    backfilled: bool = False,
) -> list[dict]:
    """Rows for the Supabase order_rejections table: every leg that did not
    become a submitted order, with its reason code.

    This is the only durable audit trail for those legs. Alpaca does not persist
    a submit-time rejection as an order record (verified 2026-09-24: the rejected
    LQD sell_to_open left no trace in the order history), and the reconciliation
    JSON is written to a filesystem that is ephemeral in production.

    Covers guard rejections (REJECTED_CAP, REJECTED_TRADED_NOTIONAL,
    REJECTED_MAX_ORDERS), the shortability pre-check (SKIPPED_NOT_SHORTABLE), and
    every skip or rejection at submission (QTY_ROUNDS_TO_ZERO,
    ASSET_NOT_SHORTABLE_AT_SUBMIT, ALPACA_API_ERROR, SUBMIT_EXCEPTION_UNKNOWN_STATE,
    SKIPPED_AFTER_HALT). A leg that reached Alpaca is excluded: it has an order id
    and is already auditable at the broker.
    """
    rows: list[dict] = []
    for fill in fills:
        if not fill.reason_code:
            continue
        rows.append({
            "run_date": str(run_date),
            "ticker": fill.ticker,
            "leg": int(fill.leg),
            "side": fill.side,
            "position_intent": fill.position_intent,
            "requested_notional": round(fill.intended_notional, 4),
            "status": fill.status,
            "reason_code": fill.reason_code,
            "detail": fill.detail or None,
            "order_id": fill.order_id or None,
            "backfilled": bool(backfilled or fill.backfilled),
        })
    return rows
