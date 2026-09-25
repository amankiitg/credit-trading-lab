"""Tests for execution/alpaca_paper.py, sprint v8.4 (P1-P8 gates).

No real Alpaca credentials are needed. All Alpaca I/O is mocked via
unittest.mock.MagicMock. The module under test is imported independently
of any live connection.

Two headline tests (per the sprint v8.4 dev ARGUMENTS):
  - an oversized target is rejected by the guard layer (P2)
  - a long-to-short crossing generates the correct two-leg order sequence
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from execution.alpaca_paper import (
    DELTA_MIN_NOTIONAL,
    DUST_THRESHOLD_USD,
    GUARD_CROSSING_FLAT_NOT_SHORTABLE,
    GUARD_SKIPPED_NOT_SHORTABLE,
    MAX_ORDERS_PER_RUN,
    MAX_POSITION_PCT_OF_NAV,
    MAX_TRADED_NOTIONAL_PER_RUN,
    PAPER_NAV_DEFAULT,
    REASON_ALPACA_ERROR,
    REASON_NOT_SHORTABLE_AT_SUBMIT,
    REASON_QTY_ZERO,
    REASON_SKIPPED_AFTER_HALT,
    REASON_SUBMIT_EXCEPTION,
    FillRecord,
    OrderSpec,
    apply_guards,
    apply_shortable_filter,
    build_fill_records,
    build_rejection_rows,
    classify_submit_failure,
    close_dust_positions,
    compute_delta_orders,
    diff_positions,
    feed_attribution,
    get_live_nav,
    get_shortable_flags,
    mark_costs,
    reconcile,
    shorts_requiring_check,
    submit_orders,
)
from execution.costs import CostParams


def _pending_spec(ticker, side, notional, position_intent, target_notional, leg=1) -> OrderSpec:
    return OrderSpec(
        ticker=ticker,
        side=side,
        notional=notional,
        position_intent=position_intent,
        target_notional=target_notional,
        guard_status="PENDING",
        leg=leg,
    )


# ---------------------------------------------------------------- P2: guard rejects oversized target

def test_guard_rejects_order_exceeding_cap() -> None:
    """P2: a target notional above _cap_pct * _nav must be REJECTED_CAP.

    Override cap_pct=0.10 and nav=$100k (cap=$10k) to make the test
    independent of the module default MAX_POSITION_PCT_OF_NAV.
    """
    cap_pct, nav = 0.10, 100_000.0          # explicit cap = $10,000
    oversized_target = cap_pct * nav + 500.0  # $10,500 -- deliberately above cap
    orders = [
        _pending_spec("SPY", "buy", oversized_target, "buy_to_open", oversized_target)
    ]
    guarded = apply_guards(orders, dry_run=False, _cap_pct=cap_pct, _nav=nav)

    assert guarded[0].guard_status == "REJECTED_CAP"
    pending = [o for o in guarded if o.guard_status == "PENDING"]
    assert len(pending) == 0


def test_guard_accepts_order_at_cap_boundary() -> None:
    """P2 boundary: exactly at cap_pct * nav is accepted (strict greater-than check)."""
    cap_pct, nav = 0.10, 100_000.0
    at_cap = cap_pct * nav   # exactly $10,000
    orders = [
        _pending_spec("IEF", "buy", at_cap, "buy_to_open", at_cap)
    ]
    guarded = apply_guards(orders, dry_run=False, _cap_pct=cap_pct, _nav=nav)
    assert guarded[0].guard_status == "PENDING"


def test_guard_rejects_oversized_short_using_abs() -> None:
    """P2: the cap guard uses abs(target_notional) so shorts are bounded
    the same way as longs.
    """
    cap_pct, nav = 0.10, 100_000.0
    oversized_short = -(cap_pct * nav + 100.0)   # -$10,100
    orders = [
        _pending_spec("HYG", "sell", abs(oversized_short), "sell_to_open", oversized_short)
    ]
    guarded = apply_guards(orders, dry_run=False, _cap_pct=cap_pct, _nav=nav)
    assert guarded[0].guard_status == "REJECTED_CAP"


# ---------------------------------------------------------------- crossing: long-to-short

def test_long_to_short_crossing_generates_two_leg_sequence() -> None:
    """The headline crossing test: current=+$3000 SPY, target=-$2000 SPY.

    Expected two-leg sequence:
      Leg 1: sell_to_close $3000 (close the existing long)
      Leg 2: sell_to_open  $2000 (establish the new short)
    Both legs must be present, in that order, before any guard is applied.
    """
    current_notionals = {"SPY": 3000.0}
    target_weights = {"SPY": -0.02}  # -$2000 at $100K NAV

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=100_000.0)
    spy_orders = [o for o in orders if o.ticker == "SPY"]

    assert len(spy_orders) == 2, f"expected 2 legs, got {len(spy_orders)}: {spy_orders}"

    leg1 = spy_orders[0]
    leg2 = spy_orders[1]

    assert leg1.leg == 1
    assert leg1.side == "sell"
    assert leg1.position_intent == "sell_to_close"
    assert abs(leg1.notional - 3000.0) < 0.01, f"leg1 notional mismatch: {leg1.notional}"

    assert leg2.leg == 2
    assert leg2.side == "sell"
    assert leg2.position_intent == "sell_to_open"
    assert abs(leg2.notional - 2000.0) < 0.01, f"leg2 notional mismatch: {leg2.notional}"


def test_short_to_long_crossing_generates_two_leg_sequence() -> None:
    """Symmetric crossing: current=-$4000 EEM, target=+$1000 EEM."""
    current_notionals = {"EEM": -4000.0}
    target_weights = {"EEM": 0.01}  # +$1000 at $100K NAV

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=100_000.0)
    eem_orders = [o for o in orders if o.ticker == "EEM"]

    assert len(eem_orders) == 2

    leg1 = eem_orders[0]
    leg2 = eem_orders[1]

    assert leg1.position_intent == "buy_to_close"
    assert abs(leg1.notional - 4000.0) < 0.01

    assert leg2.position_intent == "buy_to_open"
    assert abs(leg2.notional - 1000.0) < 0.01


# ---------------------------------------------------------------- non-crossing cases

def test_fresh_long_generates_single_buy_to_open() -> None:
    orders = compute_delta_orders({"SPY": 0.05}, {}, paper_nav=100_000.0)
    spy_orders = [o for o in orders if o.ticker == "SPY"]
    assert len(spy_orders) == 1
    assert spy_orders[0].side == "buy"
    assert spy_orders[0].position_intent == "buy_to_open"
    assert abs(spy_orders[0].notional - 5000.0) < 0.01


def test_fresh_short_generates_single_sell_to_open() -> None:
    orders = compute_delta_orders({"GLD": -0.03}, {}, paper_nav=100_000.0)
    gld_orders = [o for o in orders if o.ticker == "GLD"]
    assert len(gld_orders) == 1
    assert gld_orders[0].side == "sell"
    assert gld_orders[0].position_intent == "sell_to_open"
    assert abs(gld_orders[0].notional - 3000.0) < 0.01


def test_small_delta_below_min_is_skipped() -> None:
    """Deltas below DELTA_MIN_NOTIONAL must not generate an order."""
    orders = compute_delta_orders(
        {"TLT": 0.001},  # $100 at $100K NAV = delta $100 - (current=$95) = $5 < DELTA_MIN
        {"TLT": 95.0},
        paper_nav=100_000.0,
    )
    tlt_orders = [o for o in orders if o.ticker == "TLT"]
    assert len(tlt_orders) == 0


def test_close_to_flat_generates_sell_to_close() -> None:
    orders = compute_delta_orders({"IEF": 0.0}, {"IEF": 2500.0}, paper_nav=100_000.0)
    ief_orders = [o for o in orders if o.ticker == "IEF"]
    assert len(ief_orders) == 1
    assert ief_orders[0].position_intent == "sell_to_close"
    assert ief_orders[0].target_notional == 0.0


def test_adding_to_long_uses_single_buy_to_open() -> None:
    current = {"LQD": 2000.0}
    target = {"LQD": 0.05}  # $5000 target, add $3000
    orders = compute_delta_orders(target, current, paper_nav=100_000.0)
    lqd_orders = [o for o in orders if o.ticker == "LQD"]
    assert len(lqd_orders) == 1
    assert lqd_orders[0].side == "buy"
    assert lqd_orders[0].position_intent == "buy_to_open"
    assert abs(lqd_orders[0].notional - 3000.0) < 0.01


def test_reducing_long_uses_sell_to_close() -> None:
    current = {"SPY": 5000.0}
    target = {"SPY": 0.02}  # $2000 target, reduce by $3000
    orders = compute_delta_orders(target, current, paper_nav=100_000.0)
    spy_orders = [o for o in orders if o.ticker == "SPY"]
    assert len(spy_orders) == 1
    assert spy_orders[0].side == "sell"
    assert spy_orders[0].position_intent == "sell_to_close"


def test_reducing_short_uses_buy_to_close() -> None:
    current = {"HYG": -3000.0}
    target = {"HYG": -0.01}  # -$1000 target, reduce short by $2000 (buy to close)
    orders = compute_delta_orders(target, current, paper_nav=100_000.0)
    hyg_orders = [o for o in orders if o.ticker == "HYG"]
    assert len(hyg_orders) == 1
    assert hyg_orders[0].side == "buy"
    assert hyg_orders[0].position_intent == "buy_to_close"


# ---------------------------------------------------------------- P3: max orders per run

def test_guard_enforces_max_orders_per_run() -> None:
    """P3: once MAX_ORDERS_PER_RUN submissions are queued, all remaining
    PENDING orders must become REJECTED_MAX_ORDERS.
    """
    # build more orders than the limit allows
    orders = []
    for i in range(MAX_ORDERS_PER_RUN + 3):
        ticker = ["SPY", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD"][i % 8]
        orders.append(_pending_spec(ticker, "buy", 100.0, "buy_to_open", 100.0, leg=i))

    guarded = apply_guards(orders, dry_run=False)

    submitted = [o for o in guarded if o.guard_status == "PENDING"]
    rejected_max = [o for o in guarded if o.guard_status == "REJECTED_MAX_ORDERS"]

    assert len(submitted) == MAX_ORDERS_PER_RUN
    assert len(rejected_max) == 3


# ---------------------------------------------------------------- P8: dry-run blocks all

def test_dry_run_blocks_all_pending_orders() -> None:
    """P8: in dry-run mode, zero PENDING orders survive guard application."""
    orders = [
        _pending_spec("SPY", "buy", 2000.0, "buy_to_open", 2000.0),
        _pending_spec("GLD", "sell", 1500.0, "sell_to_open", -1500.0),
    ]
    guarded = apply_guards(orders, dry_run=True)

    pending = [o for o in guarded if o.guard_status == "PENDING"]
    dry = [o for o in guarded if o.guard_status == "DRY_RUN"]

    assert len(pending) == 0
    assert len(dry) == 2


# ---------------------------------------------------------------- P5: cost marking

def test_cost_marking_matches_v65_constants() -> None:
    """P5: simulated cost uses CostParams() defaults exactly."""
    cp = CostParams()
    fills = [
        FillRecord(
            ticker="SPY",
            order_id="test",
            side="buy",
            position_intent="buy_to_open",
            intended_notional=1000.0,
            filled_notional=1000.0,
            fill_price=500.0,
            simulated_cost=0.0,
            status="FILLED",
            guard_status="PENDING",
        )
    ]
    marked = mark_costs(fills, current_short_notionals={}, cost_params=cp)

    expected_trading_cost = (cp.half_spread_bp + cp.slippage_bp) * 1e-4 * 1000.0
    assert abs(marked[0].simulated_cost - expected_trading_cost) < 1e-9


def test_borrow_cost_applied_to_shorts_only() -> None:
    cp = CostParams()
    long_fill = FillRecord(
        ticker="SPY", order_id="a", side="buy", position_intent="buy_to_open",
        intended_notional=1000.0, filled_notional=1000.0, fill_price=100.0,
        simulated_cost=0.0, status="FILLED", guard_status="PENDING",
    )
    short_fill = FillRecord(
        ticker="HYG", order_id="b", side="sell", position_intent="sell_to_open",
        intended_notional=1000.0, filled_notional=1000.0, fill_price=80.0,
        simulated_cost=0.0, status="FILLED", guard_status="PENDING",
    )
    filled = mark_costs(
        [long_fill, short_fill],
        current_short_notionals={"HYG": 1000.0},
        cost_params=cp,
    )
    long_cost = filled[0].simulated_cost
    short_cost = filled[1].simulated_cost

    trading_base = (cp.half_spread_bp + cp.slippage_bp) * 1e-4 * 1000.0
    borrow = cp.borrow_annual / 252 * 1000.0

    assert abs(long_cost - trading_base) < 1e-9      # no borrow on long
    assert abs(short_cost - (trading_base + borrow)) < 1e-9


# ---------------------------------------------------------------- P1: reconciliation logs all orders

def test_reconciliation_includes_every_order(tmp_path: Path) -> None:
    """P1: every order appears in the reconciliation, including rejected ones."""
    import execution.alpaca_paper as ap_module
    original_dir = ap_module.LOG_DIR
    ap_module.LOG_DIR = tmp_path

    try:
        orders = [
            _pending_spec("SPY", "buy", 2000.0, "buy_to_open", 2000.0),
            replace(_pending_spec("HYG", "sell", 10000.0, "sell_to_open", -10000.0),
                    guard_status="REJECTED_CAP"),
        ]
        # SPY filled; HYG was rejected so no fill
        fills = [
            FillRecord("SPY", "ord1", "buy", "buy_to_open",
                       2000.0, 2000.0, 100.0, 0.40, "FILLED", "PENDING"),
            FillRecord("HYG", "", "sell", "sell_to_open",
                       10000.0, 0.0, 0.0, 0.0, "REJECTED_CAP", "REJECTED_CAP"),
        ]
        report = reconcile(orders, fills, run_date=date(2026, 1, 15))

        assert "SPY" in report["by_ticker"]
        assert "HYG" in report["by_ticker"]
        assert report["total_fills_captured"] == 2
        assert (tmp_path / "reconciliation_2026-01-15.json").exists()
    finally:
        ap_module.LOG_DIR = original_dir


def test_reconciliation_flags_large_discrepancy(tmp_path: Path) -> None:
    import execution.alpaca_paper as ap_module
    ap_module.LOG_DIR = tmp_path
    try:
        orders = [_pending_spec("TLT", "buy", 3000.0, "buy_to_open", 3000.0)]
        fills = [
            FillRecord("TLT", "x", "buy", "buy_to_open",
                       3000.0, 2900.0, 90.0, 0.0, "FILLED", "PENDING"),
        ]
        report = reconcile(orders, fills, run_date=date(2026, 1, 15))
        tlt = report["by_ticker"]["TLT"]["legs"][0]
        # discrepancy = 2900 - 3000 = -100; abs_tol = max(10, 0.005*3000) = 15 -> flagged
        assert tlt["flagged"] is True
    finally:
        ap_module.LOG_DIR = tmp_path


# ---------------------------------------------------------------- P7: no credentials in module

def test_no_api_key_pattern_in_module_source() -> None:
    """P7: the module must not contain any hardcoded credential pattern."""
    module_path = Path("execution/alpaca_paper.py")
    source = module_path.read_text()
    import re
    patterns = [
        r"AKIAIOSFODNN",  # AWS-style key prefix
        r"PK[A-Z0-9]{18}",  # Alpaca key prefix
        r"api_key\s*=\s*['\"][A-Za-z0-9]{10}",  # literal assignment
    ]
    for pat in patterns:
        assert not re.search(pat, source), f"credential pattern found: {pat}"


# ---------------------------------------------------------------- full dry-run pipeline (P8)

def test_full_dry_run_pipeline_no_alpaca_calls() -> None:
    """P8: running the pipeline in dry-run mode makes zero Alpaca API calls."""
    with patch("execution.alpaca_paper.TradingClient") as mock_client_class:
        from execution.alpaca_paper import connect, get_current_positions
        client = connect(dry_run=True)
        assert client is None  # no client created in dry-run
        positions = get_current_positions(None, dry_run=True)
        assert positions == {}
        mock_client_class.assert_not_called()

    # compute orders from a non-trivial target
    target_weights = {"SPY": 0.05, "GLD": -0.03}
    orders = compute_delta_orders(target_weights, {}, paper_nav=PAPER_NAV_DEFAULT)
    guarded = apply_guards(orders, dry_run=True)

    pending = [o for o in guarded if o.guard_status == "PENDING"]
    assert len(pending) == 0, "dry-run must leave zero PENDING orders"

    dry_run_orders = [o for o in guarded if o.guard_status == "DRY_RUN"]
    assert len(dry_run_orders) > 0


# ================================================================
# Traded-notional brake tests (second fail-safe guard)
# ================================================================

# ---------------------------------------------------------------- core new guard: crossing under cap, over brake

def test_traded_notional_brake_catches_crossing_that_cap_misses() -> None:
    """This is the case the position-size cap alone would miss.

    The target (-$6,000) is within the $8,000 cap, so CAP passes.
    But the crossing trades abs(close $7,000) + abs(open $6,000) = $13,000,
    which exceeds the _max_traded=$12,000 brake. Both legs must be rejected
    with REJECTED_TRADED_NOTIONAL, not REJECTED_CAP.
    """
    current_notionals = {"SPY": 7_000.0}
    target_weights = {"SPY": -0.06}  # -$6,000 at $100K NAV

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=100_000.0)
    spy_orders = [o for o in orders if o.ticker == "SPY"]
    assert len(spy_orders) == 2, "expected two-leg crossing"

    guarded = apply_guards(spy_orders, dry_run=False, _max_traded=12_000.0)

    spy_guarded = [o for o in guarded if o.ticker == "SPY"]
    statuses = [o.guard_status for o in spy_guarded]

    assert all(s == "REJECTED_TRADED_NOTIONAL" for s in statuses), (
        f"expected both legs REJECTED_TRADED_NOTIONAL, got {statuses}"
    )
    # Confirm cap did NOT trigger (target $6,000 < cap $8,000)
    assert not any(s == "REJECTED_CAP" for s in statuses)


def test_traded_notional_brake_crossing_all_or_nothing() -> None:
    """Neither leg of a crossing fires when the brake triggers.

    This verifies all-or-nothing behavior: leg 1 (close) alone would be
    within the brake, but leg 1 + leg 2 together exceed it. The brake
    must block both, not just leg 2.
    """
    # close leg: $7,000; open leg: $6,000; total: $13,000 > $12,000 brake
    current_notionals = {"SPY": 7_000.0}
    target_weights = {"SPY": -0.06}

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=100_000.0)
    guarded = apply_guards(orders, dry_run=False, _max_traded=12_000.0)

    leg1 = next(o for o in guarded if o.ticker == "SPY" and o.leg == 1)
    leg2 = next(o for o in guarded if o.ticker == "SPY" and o.leg == 2)

    # Leg 1 (close $7,000) alone would be within $12,000 brake, but must
    # still be blocked because the crossing is evaluated as a unit.
    assert leg1.guard_status == "REJECTED_TRADED_NOTIONAL"
    assert leg2.guard_status == "REJECTED_TRADED_NOTIONAL"


def test_normal_open_unaffected_by_traded_notional_brake() -> None:
    """A plain open with notional below the brake passes through unchanged."""
    orders = compute_delta_orders({"GLD": 0.03}, {}, paper_nav=100_000.0)
    guarded = apply_guards(orders, dry_run=False, _max_traded=12_000.0)

    gld = [o for o in guarded if o.ticker == "GLD"]
    assert len(gld) == 1
    assert gld[0].guard_status == "PENDING"


def test_crossing_within_brake_passes() -> None:
    """A crossing whose total traded notional is within the brake passes."""
    # close $3,000 + open $2,000 = $5,000 < $12,000 brake
    current_notionals = {"IEF": 3_000.0}
    target_weights = {"IEF": -0.02}  # -$2,000 at $100K NAV

    orders = compute_delta_orders(target_weights, current_notionals, paper_nav=100_000.0)
    guarded = apply_guards(orders, dry_run=False, _max_traded=12_000.0)

    ief = [o for o in guarded if o.ticker == "IEF"]
    assert len(ief) == 2
    assert all(o.guard_status == "PENDING" for o in ief)


# ---------------------------------------------------------------- dry-run auditability of both rejection reasons

def test_both_rejection_reasons_visible_in_dry_run() -> None:
    """DRY_RUN applied last: REJECTED_CAP and REJECTED_TRADED_NOTIONAL must
    surface in dry-run so guards stay auditable without a live run.

    Uses explicit _cap_pct=0.40, _nav=100k (cap=$40k) so the oversized
    target of $45k is clearly above cap regardless of module defaults.
    """
    # Oversized target ($45k > 40% of $100k = $40k cap) -> REJECTED_CAP
    oversized = [_pending_spec("TLT", "buy", 45_000.0, "buy_to_open", 45_000.0)]
    # Normal open that passes cap and is within brake
    normal = [_pending_spec("IEF", "buy", 3_000.0, "buy_to_open", 3_000.0)]

    # Crossing within cap ($6k < $40k) but over the explicit brake ($13k > $12k)
    # close $7,000 + open $6,000 = $13,000 > $12,000 brake
    current_notionals = {"SPY": 7_000.0}
    crossing_orders = compute_delta_orders(
        {"SPY": -0.06}, current_notionals, paper_nav=100_000.0
    )

    all_orders = oversized + crossing_orders + normal
    guarded = apply_guards(
        all_orders, dry_run=True,
        _cap_pct=0.40, _nav=100_000.0, _max_traded=12_000.0,
    )

    statuses = {o.ticker: o.guard_status for o in guarded}

    assert statuses["TLT"] == "REJECTED_CAP", (
        f"oversized target must show REJECTED_CAP in dry-run, got {statuses['TLT']}"
    )

    spy_statuses = [o.guard_status for o in guarded if o.ticker == "SPY"]
    assert all(s == "REJECTED_TRADED_NOTIONAL" for s in spy_statuses), (
        f"brake-rejected crossing must show REJECTED_TRADED_NOTIONAL in dry-run, "
        f"got {spy_statuses}"
    )

    assert statuses["IEF"] == "DRY_RUN", (
        f"normal open must be DRY_RUN after passing all content guards, got {statuses['IEF']}"
    )


# ---------------------------------------------------------------- cumulative brake across multiple orders

def test_traded_notional_accumulates_across_tickers() -> None:
    """The brake is a per-run total, not per-order.

    Two normal opens of $7,000 each = $14,000 total. First passes ($7,000 <
    $12,000); second is blocked because $7,000 + $7,000 = $14,000 > $12,000.
    """
    orders = [
        _pending_spec("SPY", "buy", 7_000.0, "buy_to_open", 7_000.0),
        _pending_spec("IEF", "buy", 7_000.0, "buy_to_open", 7_000.0),
    ]
    guarded = apply_guards(orders, dry_run=False, _max_traded=12_000.0)

    spy = next(o for o in guarded if o.ticker == "SPY")
    ief = next(o for o in guarded if o.ticker == "IEF")

    assert spy.guard_status == "PENDING"  # $7,000 <= $12,000 OK
    assert ief.guard_status == "REJECTED_TRADED_NOTIONAL"  # $7,000+$7,000 > $12,000


def test_traded_notional_brake_is_absolute() -> None:
    """MAX_TRADED_NOTIONAL_PER_RUN is an absolute dollar limit independent of NAV."""
    assert MAX_TRADED_NOTIONAL_PER_RUN == 16_000.0
    assert isinstance(MAX_TRADED_NOTIONAL_PER_RUN, float)


# ================================================================
# NAV-relative cap: new tests surfaced by the live T5 session
# ================================================================

def test_full_book_at_100k_nav_passes_cap() -> None:
    """All 8 live-session weights must pass the cap at $100k NAV.

    The live T5 run rejected all 8 tickers at REJECTED_CAP because the old
    absolute $8k cap was smaller than even the smallest weight (IEF ~18% =
    $18k).  This test is the regression gate: with MAX_POSITION_PCT_OF_NAV
    and the default PAPER_NAV_DEFAULT, no name should be rejected.
    """
    live_weights = {
        "SPY": -0.1993, "EFA":  0.3496, "EEM": 0.2614, "TLT": -0.3625,
        "IEF":  0.1812, "HYG":  0.2075, "LQD": 0.2065, "GLD":  0.2320,
    }
    orders = compute_delta_orders(live_weights, {}, paper_nav=PAPER_NAV_DEFAULT)
    guarded = apply_guards(orders, dry_run=False)

    rejected_cap = [o for o in guarded if o.guard_status == "REJECTED_CAP"]
    assert len(rejected_cap) == 0, (
        f"0 REJECTED_CAP expected at {PAPER_NAV_DEFAULT:.0f} NAV; "
        f"got {[(o.ticker, o.target_notional) for o in rejected_cap]}"
    )


def test_cap_rejects_position_exceeding_pct_of_nav() -> None:
    """A target notional above MAX_POSITION_PCT_OF_NAV * NAV is REJECTED_CAP.

    Uses the default MAX_POSITION_PCT_OF_NAV and an explicit NAV so the
    threshold is computed the same way the guard does it.
    """
    nav = 80_000.0
    oversized = MAX_POSITION_PCT_OF_NAV * nav + 1_000.0
    orders = [_pending_spec("GLD", "buy", oversized, "buy_to_open", oversized)]
    guarded = apply_guards(orders, dry_run=False, _nav=nav)
    assert guarded[0].guard_status == "REJECTED_CAP"


def test_cap_scales_with_nav() -> None:
    """The same absolute notional passes the cap at high NAV and fails at low NAV.

    This verifies the guard is truly NAV-relative rather than a fixed dollar
    limit: halving the book size halves the cap, so a mid-range notional that
    was safe at full size becomes oversized at half size.
    """
    cap_pct = MAX_POSITION_PCT_OF_NAV
    notional = 15_000.0

    # At $100k NAV: cap = 0.40 * 100k = $40k; $15k is within cap.
    guarded_high = apply_guards(
        [_pending_spec("EFA", "buy", notional, "buy_to_open", notional)],
        dry_run=False, _cap_pct=cap_pct, _nav=100_000.0,
    )
    assert guarded_high[0].guard_status == "PENDING"

    # At $30k NAV: cap = 0.40 * 30k = $12k; $15k exceeds cap.
    guarded_low = apply_guards(
        [_pending_spec("EFA", "buy", notional, "buy_to_open", notional)],
        dry_run=False, _cap_pct=cap_pct, _nav=30_000.0,
    )
    assert guarded_low[0].guard_status == "REJECTED_CAP"


def test_brake_fires_independently_of_nav() -> None:
    """The traded-notional brake is absolute: raising NAV does not relax it.

    Two $7k orders total $14k traded, which exceeds the $12k override brake
    regardless of whether NAV is $100k or $1M.  The cap (NAV-relative) would
    pass both at those NAV levels, so any rejection here must be the brake.
    """
    orders = [
        _pending_spec("SPY", "buy", 7_000.0, "buy_to_open", 7_000.0),
        _pending_spec("IEF", "buy", 7_000.0, "buy_to_open", 7_000.0),
    ]
    for nav in (100_000.0, 1_000_000.0):
        guarded = apply_guards(orders, dry_run=False, _nav=nav, _max_traded=12_000.0)
        spy = next(o for o in guarded if o.ticker == "SPY")
        ief = next(o for o in guarded if o.ticker == "IEF")
        assert spy.guard_status == "PENDING", f"SPY should pass cap at NAV={nav}"
        assert ief.guard_status == "REJECTED_TRADED_NOTIONAL", (
            f"IEF should hit brake at NAV={nav}"
        )


# ================================================================
# v8.6 tests: shorts via qty, dust close, live NAV, feed_attribution
# ================================================================

# ---------------------------------------------------------------- T1a: short orders use whole-share qty

def test_short_submit_uses_qty() -> None:
    """sell_to_open must use integer qty, not notional (E1).

    Alpaca paper rejects fractional sell_to_open. This test verifies that
    submit_orders builds a MarketOrderRequest with qty= (not notional=) when
    the position_intent is sell_to_open and close_prices is provided.
    """
    from unittest.mock import call, patch, MagicMock
    from alpaca.trading.requests import MarketOrderRequest
    import execution.alpaca_paper as ap_mod

    spec = _pending_spec("GLD", "sell", 3_000.0, "sell_to_open", -3_000.0)
    close_prices = {"GLD": 200.0}  # qty = floor(3000/200) = 15

    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "test-order-id"
    mock_client.submit_order.return_value = mock_order

    with patch.object(ap_mod, "PositionIntent", side_effect=lambda x: x), \
         patch.object(ap_mod, "OrderSide") as mock_side, \
         patch.object(ap_mod, "TimeInForce") as mock_tif, \
         patch.object(ap_mod, "MarketOrderRequest") as mock_req_cls:
        mock_side.BUY = "buy"
        mock_side.SELL = "sell"
        mock_tif.DAY = "day"
        mock_req_cls.return_value = MagicMock()
        mock_client.submit_order.return_value = mock_order

        result = submit_orders(mock_client, [spec], close_prices=close_prices)

    # Verify MarketOrderRequest was called with qty=15, not notional
    mock_req_cls.assert_called_once()
    call_kwargs = mock_req_cls.call_args[1]
    assert "qty" in call_kwargs, f"Expected qty in call kwargs, got: {call_kwargs}"
    assert call_kwargs["qty"] == 15, f"Expected qty=15, got {call_kwargs['qty']}"
    assert "notional" not in call_kwargs, "notional must not appear for short orders"
    assert len(result) == 1
    assert result[0].order_id == "test-order-id"
    assert result[0].status == "SUBMITTED"
    assert result[0].reason_code == ""


def test_long_submit_uses_notional() -> None:
    """buy_to_open must still use notional (not qty). Asymmetry is intentional."""
    from unittest.mock import patch, MagicMock
    import execution.alpaca_paper as ap_mod

    spec = _pending_spec("SPY", "buy", 5_000.0, "buy_to_open", 5_000.0)
    close_prices = {"SPY": 500.0}

    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "long-order"
    mock_client.submit_order.return_value = mock_order

    with patch.object(ap_mod, "PositionIntent", side_effect=lambda x: x), \
         patch.object(ap_mod, "OrderSide") as mock_side, \
         patch.object(ap_mod, "TimeInForce") as mock_tif, \
         patch.object(ap_mod, "MarketOrderRequest") as mock_req_cls:
        mock_side.BUY = "buy"
        mock_side.SELL = "sell"
        mock_tif.DAY = "day"
        mock_req_cls.return_value = MagicMock()

        result = submit_orders(mock_client, [spec], close_prices=close_prices)

    call_kwargs = mock_req_cls.call_args[1]
    assert "notional" in call_kwargs, f"Expected notional in kwargs, got: {call_kwargs}"
    assert "qty" not in call_kwargs, "qty must not appear for long orders"
    assert len(result) == 1
    assert result[0].order_id == "long-order"
    assert result[0].status == "SUBMITTED"


def test_qty_zero_skipped() -> None:
    """When floor(notional/price)=0, the leg is skipped with a QTY_ROUNDS_TO_ZERO
    reason code and nothing is submitted."""
    from unittest.mock import patch, MagicMock
    import execution.alpaca_paper as ap_mod

    # $5 notional at $200 price -> floor(5/200) = 0 shares
    spec = _pending_spec("GLD", "sell", 5.0, "sell_to_open", -5.0)
    close_prices = {"GLD": 200.0}

    mock_client = MagicMock()

    with patch.object(ap_mod, "PositionIntent", side_effect=lambda x: x), \
         patch.object(ap_mod, "OrderSide") as mock_side, \
         patch.object(ap_mod, "TimeInForce") as mock_tif, \
         patch.object(ap_mod, "MarketOrderRequest") as mock_req_cls:
        mock_side.SELL = "sell"
        mock_tif.DAY = "day"

        result = submit_orders(mock_client, [spec], close_prices=close_prices)

    assert len(result) == 1
    assert result[0].status == "SKIPPED"
    assert result[0].reason_code == REASON_QTY_ZERO
    assert result[0].order_id == ""
    mock_client.submit_order.assert_not_called()


# ---------------------------------------------------------------- T1: reconciliation tolerance

def test_reconciliation_tolerates_one_share_of_short_rounding(tmp_path: Path) -> None:
    """A whole-share short leg filling one share short is not a discrepancy.

    TLT sized at $345.24 floors to 4 shares at $80.26, so it fills $321.04. That
    $24.20 gap is the documented whole-share design, not an execution error.
    """
    import execution.alpaca_paper as ap_module
    ap_module.LOG_DIR = tmp_path
    try:
        spec = _pending_spec("TLT", "sell", 345.24, "sell_to_open", -27_756.78)
        fills = [
            FillRecord("TLT", "oid-tlt", "sell", "sell_to_open",
                       345.24, 321.04, 80.26, 0.0, "FILLED", "PENDING"),
        ]

        report = reconcile([spec], fills, run_date=date(2026, 9, 24))
        leg = report["by_ticker"]["TLT"]["legs"][0]

        assert leg["tolerance"] == pytest.approx(80.26), "one share's value is admitted"
        assert leg["flagged"] is False
        assert report["flagged_discrepancies"] == 0
    finally:
        ap_module.LOG_DIR = tmp_path


def test_reconciliation_still_flags_a_short_gap_beyond_one_share(tmp_path: Path) -> None:
    """Negative control: the tolerance must not swallow a real shortfall."""
    import execution.alpaca_paper as ap_module
    ap_module.LOG_DIR = tmp_path
    try:
        # Two shares of rounding at $80.26, so $160.52 missing.
        spec = _pending_spec("TLT", "sell", 500.00, "sell_to_open", -27_756.78)
        fills = [
            FillRecord("TLT", "oid-tlt", "sell", "sell_to_open",
                       500.00, 339.48, 80.26, 0.0, "FILLED", "PENDING"),
        ]

        report = reconcile([spec], fills, run_date=date(2026, 9, 24))
        leg = report["by_ticker"]["TLT"]["legs"][0]

        assert leg["flagged"] is True
        assert report["flagged_discrepancies"] == 1
    finally:
        ap_module.LOG_DIR = tmp_path


def test_reconciliation_tolerance_not_applied_to_long_legs(tmp_path: Path) -> None:
    """A long leg is notional-precise, so it gets no whole-share allowance."""
    import execution.alpaca_paper as ap_module
    ap_module.LOG_DIR = tmp_path
    try:
        spec = _pending_spec("SPY", "buy", 5000.0, "buy_to_open", 27_000.0)
        fills = [
            FillRecord("SPY", "oid-spy", "buy", "buy_to_open",
                       5000.0, 4900.0, 500.0, 0.0, "FILLED", "PENDING"),
        ]

        report = reconcile([spec], fills, run_date=date(2026, 9, 24))
        leg = report["by_ticker"]["SPY"]["legs"][0]

        assert leg["tolerance"] == pytest.approx(25.0), "0.5% of 5000, not one share"
        assert leg["flagged"] is True
    finally:
        ap_module.LOG_DIR = tmp_path


def test_buy_to_close_uses_qty() -> None:
    """buy_to_close (closing a short) also uses qty to match the shorted share count."""
    from unittest.mock import patch, MagicMock
    import execution.alpaca_paper as ap_mod

    spec = _pending_spec("HYG", "buy", 2_000.0, "buy_to_close", 0.0)
    close_prices = {"HYG": 80.0}  # qty = floor(2000/80) = 25

    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "btc-order"
    mock_client.submit_order.return_value = mock_order

    with patch.object(ap_mod, "PositionIntent", side_effect=lambda x: x), \
         patch.object(ap_mod, "OrderSide") as mock_side, \
         patch.object(ap_mod, "TimeInForce") as mock_tif, \
         patch.object(ap_mod, "MarketOrderRequest") as mock_req_cls:
        mock_side.BUY = "buy"
        mock_tif.DAY = "day"
        mock_req_cls.return_value = MagicMock()

        result = submit_orders(mock_client, [spec], close_prices=close_prices)

    call_kwargs = mock_req_cls.call_args[1]
    assert "qty" in call_kwargs
    assert call_kwargs["qty"] == 25
    assert "notional" not in call_kwargs


# ---------------------------------------------------------------- T1b: dust position cleanup

def test_dust_close_called_in_live_mode() -> None:
    """close_position is called for any UNIVERSE position below DUST_THRESHOLD_USD (E2)."""
    mock_client = MagicMock()
    dust_pos = MagicMock()
    dust_pos.symbol = "SPY"
    dust_pos.market_value = "0.003"

    big_pos = MagicMock()
    big_pos.symbol = "GLD"
    big_pos.market_value = "5000.00"

    mock_client.get_all_positions.return_value = [dust_pos, big_pos]

    closed = close_dust_positions(mock_client, dry_run=False)

    assert closed == ["SPY"]
    mock_client.close_position.assert_called_once_with("SPY")


def test_dust_not_closed_in_dry_run() -> None:
    """close_position must NOT be called in dry_run=True (P8 gate extension)."""
    mock_client = MagicMock()
    closed = close_dust_positions(mock_client, dry_run=True)

    assert closed == []
    mock_client.get_all_positions.assert_not_called()
    mock_client.close_position.assert_not_called()


def test_non_universe_dust_ignored() -> None:
    """Dust cleanup only touches tickers in UNIVERSE, not unrecognised symbols."""
    mock_client = MagicMock()
    external_dust = MagicMock()
    external_dust.symbol = "AAPL"
    external_dust.market_value = "0.01"

    mock_client.get_all_positions.return_value = [external_dust]

    closed = close_dust_positions(mock_client, dry_run=False)

    assert closed == []
    mock_client.close_position.assert_not_called()


# ---------------------------------------------------------------- position drift check

def test_drift_detected_when_live_disagrees_with_cache() -> None:
    """Live Alpaca positions differing from the cached snapshot by more than
    DELTA_MIN_NOTIONAL are flagged, with cached/live/diff for each ticker."""
    from alpaca.trading.enums import PositionSide

    from execution.alpaca_paper import check_position_drift

    spy_pos = MagicMock()
    spy_pos.symbol = "SPY"
    spy_pos.side = PositionSide.LONG
    spy_pos.market_value = "0.00"  # broker actually flat

    mock_client = MagicMock()
    mock_client.get_all_positions.return_value = [spy_pos]

    cached = {"SPY": 26_643.0}  # stale Supabase snapshot still shows a big position

    drift = check_position_drift(mock_client, cached, dry_run=False)

    assert "SPY" in drift
    assert drift["SPY"]["cached"] == 26_643.0
    assert drift["SPY"]["live"] == 0.0
    assert drift["SPY"]["diff"] == pytest.approx(-26_643.0)


def test_no_drift_when_live_matches_cache() -> None:
    """No ticker is flagged when live positions agree with the cache."""
    from alpaca.trading.enums import PositionSide

    from execution.alpaca_paper import check_position_drift

    hyg_pos = MagicMock()
    hyg_pos.symbol = "HYG"
    hyg_pos.side = PositionSide.LONG
    hyg_pos.market_value = "293.90"

    mock_client = MagicMock()
    mock_client.get_all_positions.return_value = [hyg_pos]

    cached = {"HYG": 293.90}

    drift = check_position_drift(mock_client, cached, dry_run=False)
    assert drift == {}


def test_drift_check_skipped_in_dry_run() -> None:
    """dry_run=True never calls Alpaca and always reports no drift."""
    from execution.alpaca_paper import check_position_drift

    mock_client = MagicMock()
    drift = check_position_drift(mock_client, {"SPY": 26_643.0}, dry_run=True)

    assert drift == {}
    mock_client.get_all_positions.assert_not_called()


def test_drift_below_threshold_not_flagged() -> None:
    """A gap smaller than DELTA_MIN_NOTIONAL is normal day-to-day noise, not drift."""
    from alpaca.trading.enums import PositionSide

    from execution.alpaca_paper import DELTA_MIN_NOTIONAL, check_position_drift

    lqd_pos = MagicMock()
    lqd_pos.symbol = "LQD"
    lqd_pos.side = PositionSide.LONG
    lqd_pos.market_value = "27000.00"

    mock_client = MagicMock()
    mock_client.get_all_positions.return_value = [lqd_pos]

    cached = {"LQD": 27_000.0 + (DELTA_MIN_NOTIONAL - 1)}

    drift = check_position_drift(mock_client, cached, dry_run=False)
    assert drift == {}


# ---------------------------------------------------------------- full-universe reconciliation diff

def test_diff_positions_reports_every_universe_ticker() -> None:
    """Task 0 reconciliation: the diff must cover the whole book, not only the
    gaps, so a stale cache stays visible even where the numbers happen to agree.
    """
    from execution.alpaca_paper import UNIVERSE as _UNIVERSE
    from execution.alpaca_paper import diff_positions

    live = {"SPY": 1000.0, "TLT": -2000.0}
    cached = {"SPY": 900.0, "TLT": -2000.0}

    rows = diff_positions(live, cached)

    assert [r["ticker"] for r in rows] == list(_UNIVERSE)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["SPY"]["live"] == 1000.0
    assert by_ticker["SPY"]["cached"] == 900.0
    assert by_ticker["SPY"]["diff"] == pytest.approx(100.0)
    assert by_ticker["TLT"]["diff"] == pytest.approx(0.0)
    # a ticker absent from both sides appears as an explicit flat 0/0 row
    assert by_ticker["GLD"]["live"] == 0.0
    assert by_ticker["GLD"]["cached"] == 0.0


def test_diff_positions_material_flag_uses_delta_min_notional() -> None:
    """`material` marks rows the execution layer itself would treat as a real
    position, so it reuses DELTA_MIN_NOTIONAL rather than a separate constant.
    """
    from execution.alpaca_paper import DELTA_MIN_NOTIONAL, diff_positions

    below = diff_positions({"SPY": DELTA_MIN_NOTIONAL - 1.0}, {"SPY": 0.0})
    assert below[0]["material"] is False

    at = diff_positions({"SPY": DELTA_MIN_NOTIONAL}, {"SPY": 0.0})
    assert at[0]["material"] is True


# ---------------------------------------------------------------- T2: live NAV

def test_get_live_nav_returns_equity() -> None:
    """get_live_nav returns account.equity as float (E3)."""
    mock_client = MagicMock()
    mock_account = MagicMock()
    mock_account.equity = "120000.50"
    mock_client.get_account.return_value = mock_account

    nav = get_live_nav(mock_client)
    assert abs(nav - 120_000.50) < 0.01


def test_get_live_nav_fallback_on_error() -> None:
    """get_live_nav falls back to PAPER_NAV_DEFAULT if Alpaca call fails."""
    mock_client = MagicMock()
    mock_client.get_account.side_effect = RuntimeError("network error")

    nav = get_live_nav(mock_client)
    assert nav == PAPER_NAV_DEFAULT


# ---------------------------------------------------------------- T3: feed_attribution

def test_feed_attribution_appends_one_row(tmp_path: Path) -> None:
    """A single FILLED FillRecord appends exactly one row to attribution.parquet (E4)."""
    import pandas as pd

    fill = FillRecord(
        ticker="GLD",
        order_id="ord-abc",
        side="buy",
        position_intent="buy_to_open",
        intended_notional=5_000.0,
        filled_notional=5_000.0,
        fill_price=190.0,
        simulated_cost=1.0,
        status="FILLED",
        guard_status="PENDING",
    )
    close_prices = {"GLD": 192.0}
    parquet_path = str(tmp_path / "attribution.parquet")

    n = feed_attribution(
        [fill],
        close_prices=close_prices,
        run_date=date(2026, 6, 17),
        nav=100_000.0,
        parquet_path=parquet_path,
    )

    assert n == 1
    df = pd.read_parquet(parquet_path)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "GLD"
    assert row["asset_class"] == "commodity"
    # pnl = (192-190)/190 * 5000 = 2/190 * 5000 ≈ 52.63
    expected_pnl = (192.0 - 190.0) / 190.0 * 5_000.0
    assert abs(row["pnl"] - expected_pnl) < 0.01
    assert row["net_pnl"] == row["gross_pnl"] - row["turnover_cost"]


def test_feed_attribution_dry_run_noop(tmp_path: Path) -> None:
    """Empty fills list leaves the parquet file untouched (E4 dry-run gate)."""
    parquet_path = str(tmp_path / "attribution.parquet")

    n = feed_attribution(
        [],
        close_prices={},
        run_date=date(2026, 6, 17),
        nav=100_000.0,
        parquet_path=parquet_path,
    )

    assert n == 0
    assert not (tmp_path / "attribution.parquet").exists()


def test_feed_attribution_schema_match(tmp_path: Path) -> None:
    """Appended rows have exactly the v8.3 tidy-frame column set (E4 schema gate)."""
    import pandas as pd

    _EXPECTED_COLUMNS = [
        "date", "ticker", "asset_class", "weight", "pnl", "carry", "price_change",
        "gross_pnl", "net_pnl", "turnover_cost", "borrow_cost",
        "directional", "selection", "net_exposure", "beta_explained", "residual", "r_squared",
        "backfilled",
    ]

    fill = FillRecord(
        ticker="SPY",
        order_id="ord-xyz",
        side="sell",
        position_intent="sell_to_open",
        intended_notional=3_000.0,
        filled_notional=3_000.0,
        fill_price=520.0,
        simulated_cost=0.60,
        status="FILLED",
        guard_status="PENDING",
    )
    parquet_path = str(tmp_path / "attribution.parquet")

    feed_attribution(
        [fill],
        close_prices={"SPY": 518.0},
        run_date=date(2026, 6, 17),
        nav=100_000.0,
        parquet_path=parquet_path,
    )

    df = pd.read_parquet(parquet_path)
    assert list(df.columns) == _EXPECTED_COLUMNS, (
        f"Column mismatch.\nExpected: {_EXPECTED_COLUMNS}\nGot:      {list(df.columns)}"
    )


def test_feed_attribution_short_sign(tmp_path: Path) -> None:
    """A short fill (sell) has negative weight and profits when price falls (E4)."""
    import pandas as pd

    fill = FillRecord(
        ticker="SPY",
        order_id="s1",
        side="sell",
        position_intent="sell_to_open",
        intended_notional=4_000.0,
        filled_notional=4_000.0,
        fill_price=520.0,
        simulated_cost=0.80,
        status="FILLED",
        guard_status="PENDING",
    )
    parquet_path = str(tmp_path / "attribution.parquet")

    feed_attribution(
        [fill],
        close_prices={"SPY": 515.0},  # price fell -> short profits
        run_date=date(2026, 6, 17),
        nav=100_000.0,
        parquet_path=parquet_path,
    )

    df = pd.read_parquet(parquet_path)
    row = df.iloc[0]
    assert row["weight"] < 0, "short position must have negative weight"
    assert row["pnl"] > 0, "short position profits when price falls"


def test_feed_attribution_appends_to_existing(tmp_path: Path) -> None:
    """feed_attribution appends to an existing parquet without overwriting it."""
    import pandas as pd

    parquet_path = str(tmp_path / "attribution.parquet")

    # Pre-populate with one row
    existing = pd.DataFrame([{
        "date": pd.Timestamp("2026-01-01"),
        "ticker": "IEF", "asset_class": "rates",
        "weight": 0.1, "pnl": 100.0, "carry": 50.0, "price_change": 50.0,
        "gross_pnl": 100.0, "net_pnl": 99.0, "turnover_cost": 1.0, "borrow_cost": 0.0,
        "directional": float("nan"), "selection": float("nan"), "net_exposure": 0.1,
        "beta_explained": float("nan"), "residual": float("nan"), "r_squared": float("nan"),
    }])
    existing.to_parquet(parquet_path, index=False)

    fill = FillRecord(
        ticker="GLD", order_id="g1", side="buy", position_intent="buy_to_open",
        intended_notional=2_000.0, filled_notional=2_000.0, fill_price=200.0,
        simulated_cost=0.4, status="FILLED", guard_status="PENDING",
    )
    n = feed_attribution(
        [fill],
        close_prices={"GLD": 202.0},
        run_date=date(2026, 6, 17),
        nav=100_000.0,
        parquet_path=parquet_path,
    )

    assert n == 1
    df = pd.read_parquet(parquet_path)
    assert len(df) == 2  # original + new
    assert set(df["ticker"]) == {"IEF", "GLD"}


def test_feed_attribution_marks_backfilled_rows(tmp_path: Path) -> None:
    """A reconstructed row must be distinguishable from a row written live."""
    import pandas as pd

    parquet_path = str(tmp_path / "attribution.parquet")
    fill = FillRecord(
        ticker="EFA",
        order_id="oid-efa",
        side="buy",
        position_intent="buy_to_open",
        intended_notional=475.28,
        filled_notional=475.27,
        fill_price=104.44,
        simulated_cost=0.0951,
        status="FILLED",
        guard_status="PENDING",
        backfilled=True,
    )

    feed_attribution(
        [fill],
        close_prices={"EFA": 104.06},
        run_date=date(2026, 9, 24),
        nav=101_013.45,
        parquet_path=parquet_path,
        backfilled=True,
    )

    df = pd.read_parquet(parquet_path)
    assert bool(df.iloc[0]["backfilled"]) is True
    assert df["backfilled"].dtype == bool


# ================================================================
# Sprint v9.2 -- run-level resilience (T1) and shortability (T2)
#
# Motivating live incident, 2026-09-24 14:31 UTC: 7 legs intended
# (EFA, EEM, TLT, IEF, HYG, LQD, GLD), 5 filled, LQD rejected with
# 42210000 "asset cannot be sold short", GLD never attempted, and no
# state written back because the exception propagated out of main().
# ================================================================

# ---------------------------------------------------------------- T1: classification

def test_classify_submit_failure_codes() -> None:
    """A broker rejection is a per-leg fact; an unknown-state failure halts."""
    from alpaca.common.exceptions import APIError

    code, halts = classify_submit_failure(
        APIError('{"code":42210000,"message":"asset LQD cannot be sold short"}')
    )
    assert code == REASON_NOT_SHORTABLE_AT_SUBMIT
    assert halts is False

    code, halts = classify_submit_failure(
        APIError('{"code":40310000,"message":"insufficient qty available"}')
    )
    assert code == REASON_ALPACA_ERROR
    assert halts is False

    code, halts = classify_submit_failure(TimeoutError("read timed out"))
    assert code == REASON_SUBMIT_EXCEPTION
    assert halts is True


# ---------------------------------------------------------------- T1: one bad leg cannot abort the book

def _ok_order(order_id: str) -> MagicMock:
    order = MagicMock()
    order.id = order_id
    return order


def test_one_rejected_leg_does_not_abort_the_run() -> None:
    """The exact 2026-09-24 shape: leg 1 is rejected, leg 2 must still be sent.

    Before this change the rejection raised out of submit_orders and the whole
    run died, which is why GLD was never attempted.
    """
    from alpaca.common.exceptions import APIError

    spec_a = _pending_spec("LQD", "sell", 290.39, "sell_to_open", -26_877.96)
    spec_b = _pending_spec("GLD", "sell", 580.74, "sell_to_open", -21_391.72)

    mock_client = MagicMock()
    mock_client.submit_order.side_effect = [
        APIError('{"code":42210000,"message":"asset LQD cannot be sold short"}'),
        _ok_order("gld-order-id"),
    ]

    outcomes = submit_orders(
        mock_client, [spec_a, spec_b], close_prices={"LQD": 100.0, "GLD": 400.0}
    )

    assert len(outcomes) == 2, "one outcome per PENDING leg, no silent drops"
    assert outcomes[0].status == "REJECTED"
    assert outcomes[0].reason_code == REASON_NOT_SHORTABLE_AT_SUBMIT
    assert outcomes[0].halts_run is False
    assert "sold short" in outcomes[0].detail

    assert outcomes[1].status == "SUBMITTED"
    assert outcomes[1].order_id == "gld-order-id"
    assert mock_client.submit_order.call_count == 2, "the run continued past the failure"


def test_transport_failure_halts_remaining_legs() -> None:
    """Unknown submission state stops the sequence and marks the rest un-attempted.

    Continuing after a connection failure would stack more unknown state on top
    of an unknown state, so the remaining legs are skipped by policy and
    recorded, not silently dropped.
    """
    spec_a = _pending_spec("SPY", "buy", 5000.0, "buy_to_open", 5000.0)
    spec_b = _pending_spec("LQD", "sell", 290.39, "sell_to_open", -26_877.96)

    mock_client = MagicMock()
    mock_client.submit_order.side_effect = ConnectionError("connection reset by peer")

    outcomes = submit_orders(mock_client, [spec_a, spec_b], close_prices={"LQD": 100.0})

    assert outcomes[0].status == "UNKNOWN"
    assert outcomes[0].reason_code == REASON_SUBMIT_EXCEPTION
    assert outcomes[0].halts_run is True
    assert outcomes[1].status == "NOT_ATTEMPTED"
    assert outcomes[1].reason_code == REASON_SKIPPED_AFTER_HALT
    assert mock_client.submit_order.call_count == 1, "must not keep submitting blind"


def test_build_fill_records_raises_when_a_leg_has_no_outcome() -> None:
    """P1: a PENDING leg with no outcome must raise, never vanish."""
    spec = _pending_spec("SPY", "buy", 1000.0, "buy_to_open", 1000.0)
    with pytest.raises(ValueError, match="silently dropped"):
        build_fill_records([spec], [], {})


def test_build_rejection_rows_audits_only_rejected_legs() -> None:
    """Every rejected or skipped leg reaches the Supabase audit table."""
    fills = [
        FillRecord(
            "LQD", "", "sell", "sell_to_open", 290.39, 0.0, 0.0, 0.0,
            "REJECTED", "PENDING", leg=1,
            reason_code=REASON_NOT_SHORTABLE_AT_SUBMIT,
            detail="APIError: asset LQD cannot be sold short",
        ),
        FillRecord(
            "GLD", "", "sell", "sell_to_open", 580.74, 0.0, 0.0, 0.0,
            "NOT_ATTEMPTED", "PENDING", leg=1,
            reason_code=REASON_SKIPPED_AFTER_HALT,
        ),
        FillRecord(
            "EFA", "oid-efa", "buy", "buy_to_open", 475.28, 475.28, 104.0, 0.05,
            "FILLED", "PENDING",
        ),
    ]

    rows = build_rejection_rows(fills, run_date=date(2026, 9, 24))

    assert len(rows) == 2, "a leg that reached Alpaca is already auditable there"
    assert {r["ticker"] for r in rows} == {"LQD", "GLD"}
    lqd = next(r for r in rows if r["ticker"] == "LQD")
    assert lqd["reason_code"] == REASON_NOT_SHORTABLE_AT_SUBMIT
    assert lqd["run_date"] == "2026-09-24"
    assert lqd["detail"] == "APIError: asset LQD cannot be sold short"
    assert lqd["backfilled"] is False


# ---------------------------------------------------------------- T2: shortability policy

def test_shortable_filter_skips_only_sell_to_open() -> None:
    """Task 2 policy: block opening or growing a short, never reducing one."""
    orders = [
        _pending_spec("LQD", "sell", 290.0, "sell_to_open", -26_877.96),
        _pending_spec("HYG", "buy", 300.0, "buy_to_close", -28_000.0),
        _pending_spec("IEF", "sell", 250.0, "sell_to_close", 0.0),
        _pending_spec("SPY", "buy", 500.0, "buy_to_open", 27_000.0),
    ]
    shortable = {"LQD": False, "HYG": True, "IEF": True, "SPY": True}

    filtered = apply_shortable_filter(orders, shortable)
    status = {s.ticker: s.guard_status for s in filtered}

    assert status["LQD"] == GUARD_SKIPPED_NOT_SHORTABLE
    assert status["HYG"] == "PENDING", "reducing a short must still go through"
    assert status["IEF"] == "PENDING", "closing a long must still go through"
    assert status["SPY"] == "PENDING"


def test_shortable_filter_blocks_increasing_a_short_but_not_reducing_it() -> None:
    """The live LQD case, driven through the real delta maths.

    LQD was already short and the signal wanted it shorter, which is a
    sell_to_open. Wanting it less short is a buy_to_close and must be allowed
    even though the asset is not shortable.
    """
    current = {"LQD": -26_587.58}
    nav = 101_013.45

    increasing = compute_delta_orders({"LQD": -0.266083}, current, paper_nav=nav)
    assert [o.position_intent for o in increasing] == ["sell_to_open"]
    assert apply_shortable_filter(increasing, {"LQD": False})[0].guard_status == (
        GUARD_SKIPPED_NOT_SHORTABLE
    )

    reducing = compute_delta_orders({"LQD": -0.257490}, current, paper_nav=nav)
    assert [o.position_intent for o in reducing] == ["buy_to_close"]
    assert apply_shortable_filter(reducing, {"LQD": False})[0].guard_status == "PENDING"


def test_shortable_filter_does_not_touch_already_blocked_legs() -> None:
    """A leg the guards already rejected keeps its own reason, not a new one."""
    spec = _pending_spec("LQD", "sell", 290.0, "sell_to_open", -26_877.96)
    already_rejected = replace(spec, guard_status="REJECTED_CAP")

    out = apply_shortable_filter([already_rejected], {"LQD": False})

    assert out[0].guard_status == "REJECTED_CAP"


def test_shorts_requiring_check_only_lists_pending_sell_to_open() -> None:
    orders = [
        _pending_spec("LQD", "sell", 100.0, "sell_to_open", -100.0),
        _pending_spec("HYG", "buy", 100.0, "buy_to_close", 0.0),
        replace(
            _pending_spec("GLD", "sell", 100.0, "sell_to_open", -100.0),
            guard_status="DRY_RUN",
        ),
    ]
    assert shorts_requiring_check(orders) == ["LQD"]


def test_shortable_filter_distinguishes_the_open_leg_of_a_crossing() -> None:
    """A blocked crossing open leg gets its own code, because the book ends FLAT.

    Long SPY to short SPY runs sell_to_close then sell_to_open. Blocking only the
    open leg leaves the name flat, which is closer to a short target than staying
    long, so the two cases must be separable in attribution.
    """
    crossing = compute_delta_orders({"SPY": -0.05}, {"SPY": 5000.0}, paper_nav=100_000.0)
    assert [o.position_intent for o in crossing] == ["sell_to_close", "sell_to_open"]

    filtered = apply_shortable_filter(crossing, {"SPY": False})
    by_leg = {o.leg: o.guard_status for o in filtered}
    assert by_leg[1] == "PENDING", "the closing leg must still run"
    assert by_leg[2] == GUARD_CROSSING_FLAT_NOT_SHORTABLE

    # An ordinary short leg, with no crossing, keeps the plain code.
    ordinary = compute_delta_orders({"GLD": -0.02}, {}, paper_nav=100_000.0)
    assert apply_shortable_filter(ordinary, {"GLD": False})[0].guard_status == (
        GUARD_SKIPPED_NOT_SHORTABLE
    )


def test_get_shortable_flags_reads_the_asset_endpoint() -> None:
    client = MagicMock()
    client.get_asset.side_effect = lambda t: MagicMock(shortable=(t != "LQD"))

    flags = get_shortable_flags(client, ["SPY", "LQD"])

    assert flags == {"SPY": True, "LQD": False}
    assert client.get_asset.call_count == 2


def test_get_shortable_flags_fails_closed_on_lookup_error() -> None:
    """An unverifiable short must be skipped, not submitted on an assumption."""
    client = MagicMock()
    client.get_asset.side_effect = ConnectionError("asset endpoint unavailable")

    assert get_shortable_flags(client, ["GLD"]) == {"GLD": False}


# ---------------------------------------------------------------- run-level, end to end

def _run_execution_with_stub(
    tmp_path: Path,
    monkeypatch,
    *,
    shortable_map: dict[str, bool],
    submit_failure: dict[str, Exception] | None = None,
    dry_run: bool = False,
    signal_as_of: str | None = None,
    drift: dict | None = None,
    alert_sender: Callable | None = None,
) -> dict:
    """Drive scripts.run_execution.main() with Alpaca and Supabase stubbed.

    The Alpaca stub keeps a real book: every accepted leg changes it, and
    get_all_positions reports that book back. So the positions the run records
    can be compared against what actually executed, rather than against a
    hardcoded expectation.

    `signal_as_of` overrides the stored signal date, which is how the staleness
    guard is reached; it defaults to today. `drift` forces the drift check to
    report a divergence, which is the only way into the alert branch. `alert_sender`
    replaces the email sender for the whole run, covering both the drift alert and
    the daily summary.
    """
    import json as _json
    from datetime import date as _date

    import dashboard.supabase_client as sb_mod
    import execution.alpaca_paper as ap_mod
    import execution.calendar_utils as cal_mod
    import scripts.run_execution as run_mod

    today = _date.today().isoformat()
    submit_failure = submit_failure or {}

    # Fills must mark at the same prices the whole-share conversion used, or a
    # short leg would appear to fill for less than it was sized for.
    prices = {"SPY": 500.0, "LQD": 100.0, "GLD": 400.0}

    book: dict[str, float] = {}
    submitted_orders: dict[str, MagicMock] = {}

    def _get_all_positions():
        out = []
        for sym, signed in book.items():
            pos = MagicMock()
            pos.symbol = sym
            pos.market_value = str(abs(signed))
            pos.side = ap_mod.PositionSide.LONG if signed > 0 else ap_mod.PositionSide.SHORT
            out.append(pos)
        return out

    def _submit(order_data):
        sym = order_data.symbol
        if sym in submit_failure:
            raise submit_failure[sym]
        mark = prices.get(sym, 100.0)
        if order_data.notional is not None:
            amount = float(order_data.notional)
            qty = amount / mark
        else:
            qty = float(order_data.qty)
            amount = qty * mark
        sign = 1.0 if order_data.side.value == "buy" else -1.0
        book[sym] = book.get(sym, 0.0) + sign * amount

        order = MagicMock()
        order.id = f"oid-{sym}-{order_data.position_intent}"
        order.status = ap_mod.OrderStatus.FILLED
        order.filled_avg_price = str(mark)
        order.filled_qty = str(qty)
        submitted_orders[order.id] = order
        return order

    client = MagicMock()
    client.get_account.return_value.equity = "100000"
    client.get_all_positions.side_effect = _get_all_positions
    client.get_asset.side_effect = lambda t: MagicMock(shortable=shortable_map.get(t, True))
    client.submit_order.side_effect = _submit
    client.get_order_by_id.side_effect = lambda oid: submitted_orders[oid]

    monkeypatch.setattr(ap_mod, "connect", lambda dry_run=False: client)
    monkeypatch.setattr(ap_mod, "DRY_RUN_DEFAULT", dry_run)
    monkeypatch.setattr(ap_mod, "LOG_DIR", tmp_path / "logs")

    feed_calls: list = []
    monkeypatch.setattr(
        ap_mod, "feed_attribution",
        lambda fills, close_prices, run_date=None, nav=100_000.0, **kw: (
            feed_calls.append(list(fills)) or 0
        ),
    )

    monkeypatch.setattr(cal_mod, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal_mod, "check_already_ran", lambda job, d: False)
    recorded: dict = {}
    monkeypatch.setattr(
        cal_mod, "record_run", lambda job, d: recorded.setdefault("run", (job, d))
    )

    if drift is not None:
        # run_execution imports this at call time, so patching the source module
        # is what the drift branch actually sees.
        monkeypatch.setattr(
            ap_mod, "check_position_drift",
            lambda client, cached, dry_run=False: drift,
        )

    settings = {
        "signal_as_of_date": signal_as_of or today,
        "signal_target_weights": _json.dumps({"SPY": 0.05, "LQD": -0.06, "GLD": -0.02}),
        "signal_close_prices": _json.dumps({"SPY": 500.0, "LQD": 100.0, "GLD": 400.0}),
        "live_nav": "100000",
    }
    monkeypatch.setattr(sb_mod, "get_setting", lambda k: settings.get(k))
    monkeypatch.setattr(
        sb_mod, "set_setting", lambda k, v: settings.__setitem__(k, v) or True
    )
    monkeypatch.setattr(sb_mod, "get_auto_approve", lambda: True)
    monkeypatch.setattr(sb_mod, "fetch_decision_for_date", lambda d: "approve")
    monkeypatch.setattr(sb_mod, "fetch_positions", lambda latest_only=True: [])

    written: dict[str, list] = {"positions": [], "pnl": [], "rejections": [], "attribution": []}
    monkeypatch.setattr(
        sb_mod, "write_positions", lambda rows: written["positions"].extend(rows) or True
    )
    monkeypatch.setattr(
        sb_mod, "write_pnl_log", lambda row: written["pnl"].append(row) or True
    )
    monkeypatch.setattr(
        sb_mod, "write_order_rejections",
        lambda rows: written["rejections"].extend(rows) or True,
    )
    monkeypatch.setattr(
        sb_mod, "write_live_attribution",
        lambda rows: written["attribution"].extend(rows) or True,
    )
    monkeypatch.setattr(sb_mod, "write_cron_run", lambda job, d: True)

    # Capture the daily summary email rather than sending it. daily_summary and
    # the drift branch both resolve execution.alerts at call time, so patching the
    # attribute covers both. Looked up in sys.modules rather than imported, because
    # one test simulates the module being absent by setting it to None.
    import sys

    emails: list[dict] = []

    if alert_sender is not None:
        _sender = alert_sender
    else:
        def _sender(subject, body):
            return emails.append({"subject": subject, "body": body}) or True

    _alerts_mod = sys.modules.get("execution.alerts")
    if _alerts_mod is not None:
        monkeypatch.setattr(_alerts_mod, "send_alert_email", _sender)

    exit_code = run_mod.main()

    return {
        "exit_code": exit_code,
        "book": book,
        "written": written,
        "recorded": recorded,
        "feed_calls": feed_calls,
        "submit_calls": client.submit_order.call_count,
        "settings": settings,
        "reconcile_log": tmp_path / "logs" / f"reconciliation_{today}.json",
        "emails": emails,
    }


def test_run_completes_when_a_short_leg_is_rejected_at_submit(tmp_path, monkeypatch) -> None:
    """Task 1 end to end: the run survives the live LQD rejection.

    The asset endpoint says LQD is shortable, so the leg is submitted and Alpaca
    rejects it. The run must finish (exit 0), log the rejection with a reason
    code, and record positions consistent with what actually executed.
    """
    from alpaca.common.exceptions import APIError

    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        submit_failure={
            "LQD": APIError('{"code":42210000,"message":"asset LQD cannot be sold short"}')
        },
    )

    assert result["exit_code"] == 0, "a rejected leg must not fail the run"
    assert result["submit_calls"] == 3, "all three legs were attempted"

    # The rejection is logged with its reason code, and GLD still traded.
    rejections = result["written"]["rejections"]
    assert len(rejections) == 1
    assert rejections[0]["ticker"] == "LQD"
    assert rejections[0]["reason_code"] == REASON_NOT_SHORTABLE_AT_SUBMIT
    assert rejections[0]["status"] == "REJECTED"

    # Recorded positions equal the book the broker actually holds.
    assert result["book"] == {"SPY": 5000.0, "GLD": -2000.0}
    recorded = {r["ticker"]: r["signed_notional"] for r in result["written"]["positions"]}
    assert recorded == pytest.approx(result["book"])
    assert "LQD" not in recorded, "a rejected leg must not appear as a position"

    # The run is complete, so cron_runs is written and the rejection JSON exists.
    assert result["recorded"]["run"][0] == "run_execution"
    assert result["reconcile_log"].exists()

    import json as _json
    report = _json.loads(result["reconcile_log"].read_text())
    assert report["rejected_or_skipped_legs"] == 1
    lqd_leg = report["by_ticker"]["LQD"]["legs"][0]
    assert lqd_leg["reason_code"] == REASON_NOT_SHORTABLE_AT_SUBMIT
    assert lqd_leg["status"] == "REJECTED"


def test_run_skips_a_non_shortable_leg_before_submitting(tmp_path, monkeypatch) -> None:
    """Task 2 end to end: the pre-check means LQD is never sent at all."""
    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": False, "GLD": True},
    )

    assert result["exit_code"] == 0
    assert result["submit_calls"] == 2, "the non-shortable leg must not be submitted"

    rejections = result["written"]["rejections"]
    assert len(rejections) == 1
    assert rejections[0]["ticker"] == "LQD"
    assert rejections[0]["reason_code"] == GUARD_SKIPPED_NOT_SHORTABLE
    assert rejections[0]["status"] == GUARD_SKIPPED_NOT_SHORTABLE

    assert result["book"] == {"SPY": 5000.0, "GLD": -2000.0}
    assert result["recorded"]["run"][0] == "run_execution"


def _signal_date_sessions_ago(n: int) -> str:
    """A session date exactly `n` NYSE sessions before the most recent one."""
    import exchange_calendars as ec
    import pandas as pd

    cal = ec.get_calendar("XNYS")
    today = pd.Timestamp(date.today())
    sessions = cal.sessions_in_range(today - pd.Timedelta(days=60), today)
    return str(sessions[-(n + 1)].date())


def test_run_skips_a_stale_signal_without_trading(tmp_path, monkeypatch, caplog) -> None:
    """v9.4 item 2: a signal past the staleness limit must not be traded.

    On 2026-09-24 the execution cron traded a signal stored for 2026-09-22. The
    book is sized for the market the signal was computed against, so using it two
    sessions later puts on the wrong book. The run must skip, trade nothing, and
    exit distinctly.

    cron_runs must stay unwritten. That row is the idempotency gate, so writing it
    on a skip would make every later tick that day skip too and the fresh signal
    would never be traded.
    """
    import logging

    stale = _signal_date_sessions_ago(3)
    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            signal_as_of=stale,
        )

    assert result["exit_code"] == 4, "a stale signal must skip with its own exit code"
    assert result["submit_calls"] == 0, "not one leg may be submitted on a stale signal"
    assert result["written"]["positions"] == [], "no positions may be recorded"
    assert result["written"]["pnl"] == [], "no pnl row may be written"
    assert "run" not in result["recorded"], "a skipped run must not be recorded"
    assert not result["reconcile_log"].exists(), "nothing to reconcile"

    assert "STALE SIGNAL" in caplog.text
    assert "3 NYSE sessions older" in caplog.text
    assert "SKIPPING the run without trading" in caplog.text


def test_run_trades_a_signal_at_the_staleness_limit(tmp_path, monkeypatch, caplog) -> None:
    """Negative control: exactly at the limit, the run still trades.

    Without this, the skip test would also pass if the guard simply refused every
    signal. Two sessions old is the documented limit and must go through.
    """
    import logging

    at_limit = _signal_date_sessions_ago(2)
    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            signal_as_of=at_limit,
        )

    assert result["exit_code"] == 0
    assert result["submit_calls"] == 3, "all three legs were attempted"
    assert result["recorded"]["run"][0] == "run_execution"
    assert "STALE SIGNAL" not in caplog.text
    assert "is 2 NYSE session(s) old" in caplog.text


# ------------------------------------------------- v9.5: the daily summary email

def test_ok_run_sends_exactly_one_ok_summary(tmp_path, monkeypatch) -> None:
    """Requirement 5: a normal run sends one [OK] email with the run's numbers."""
    today = date.today().isoformat()
    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
    )

    assert result["exit_code"] == 0
    assert len(result["emails"]) == 1, "exactly one email per run"

    email = result["emails"][0]
    assert email["subject"] == f"[OK] run_execution {today}"

    body = email["body"]
    assert "exit code 0" in body
    assert f"Signal as_of_date: {today}" in body
    assert "NAV frozen for sizing: $100,000.00" in body
    assert "Orders: 3 filled, 0 skipped, 0 rejected" in body
    assert "filled: SPY buy_to_open" in body
    assert "Day P&L:" in body, "the day's P&L belongs in the email"


def test_stale_skip_sends_exactly_one_skip_summary(tmp_path, monkeypatch) -> None:
    """Requirement 5: a stale skip is [SKIP], not [FAIL] and not [OK].

    The run deliberately did nothing, and the email has to say which, because a
    silent skip is indistinguishable from a missed run in an inbox full of them.
    """
    today = date.today().isoformat()
    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        signal_as_of=_signal_date_sessions_ago(3),
    )

    assert result["exit_code"] == 4
    assert len(result["emails"]) == 1

    email = result["emails"][0]
    assert email["subject"] == f"[SKIP] run_execution {today}"
    assert "exit code 4" in email["body"]
    assert "over the 2 session limit" in email["body"]
    assert "Last step started" in email["body"], "a skip says where it stopped"
    assert "Orders: 0 filled" in email["body"]


def test_dry_run_sends_no_summary(tmp_path, monkeypatch, caplog) -> None:
    """Requirement 4: a rehearsal must not email, and must log the skip."""
    import logging

    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            dry_run=True,
        )

    assert result["exit_code"] == 0
    assert result["emails"] == [], "a dry run must not send a summary"
    assert "summary email skipped: dry run" in caplog.text


def test_a_failed_summary_email_leaves_the_exit_code_alone(
    tmp_path, monkeypatch, caplog
) -> None:
    """Requirement 5: a send failure must not change the exit code.

    The run traded, so it exits 0 whatever happened to the email. The failure is
    still visible in the log at WARNING.
    """
    import logging

    def _boom(subject, body):
        raise RuntimeError("resend unreachable")

    with caplog.at_level(logging.WARNING):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            alert_sender=_boom,
        )

    assert result["exit_code"] == 0, "a failed email must not fail the run"
    assert result["submit_calls"] == 3, "the trading still happened"
    assert result["recorded"]["run"][0] == "run_execution"
    assert "summary email failed (RuntimeError: resend unreachable)" in caplog.text


# ------------------------------------------------- v9.4 follow-up: alerts are best effort

_DRIFT = {"SPY": {"cached": 5000.0, "live": 4000.0, "diff": 1000.0}}


def test_run_completes_when_the_alerts_module_cannot_be_imported(
    tmp_path, monkeypatch, caplog
) -> None:
    """An alert must never block trading, not even a missing alerts module.

    execution/alerts.py existed in the working tree but had never been committed,
    so on Render the drift branch raised ModuleNotFoundError at the import, which
    sits before order submission. The whole run aborted and nothing traded. Simulating
    the missing module is the point: committing the file fixes today's instance of
    this, but the call site has to survive the class of failure.
    """
    import logging
    import sys

    # The canonical way to make an import fail, exactly as if the file were absent.
    monkeypatch.setitem(sys.modules, "execution.alerts", None)

    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            drift=_DRIFT,
        )

    assert result["exit_code"] == 0, "a failed alert must not fail the run"
    assert result["submit_calls"] == 3, "all three legs were still submitted"
    assert result["recorded"]["run"][0] == "run_execution", "the run completed"
    assert "position-drift alert email failed" in caplog.text
    assert "must never block trading" in caplog.text
    # The drift was still reported to the operator through the channel that
    # matters, so the failure only lost the email, not the record.
    assert "POSITION DRIFT" in caplog.text


def test_run_completes_when_the_alert_send_raises(tmp_path, monkeypatch, caplog) -> None:
    """A raising send is caught too, so a Resend outage cannot stop the book trading.

    Covers both senders in one run: the drift alert and the daily summary. Neither
    may change what the run did.
    """
    import logging

    def _boom(subject, body):
        raise RuntimeError("resend returned 500")

    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            drift=_DRIFT,
            alert_sender=_boom,
        )

    assert result["exit_code"] == 0
    assert result["submit_calls"] == 3
    assert result["recorded"]["run"][0] == "run_execution"
    assert "position-drift alert email failed (RuntimeError: resend returned 500)" in caplog.text
    assert "summary email failed (RuntimeError: resend returned 500)" in caplog.text


def test_a_failed_alert_does_not_hide_the_absence_of_drift_handling(
    tmp_path, monkeypatch, caplog
) -> None:
    """Negative control: with no drift the alert branch is never entered.

    Otherwise the two tests above would pass even if the guard were wrapped around
    something that always runs, and a successful send could be silently swallowed.
    """
    import logging
    import sys

    monkeypatch.setitem(sys.modules, "execution.alerts", None)

    with caplog.at_level(logging.INFO):
        result = _run_execution_with_stub(
            tmp_path, monkeypatch,
            shortable_map={"SPY": True, "LQD": True, "GLD": True},
            drift=None,
        )

    assert result["exit_code"] == 0
    assert result["submit_calls"] == 3
    assert "position-drift alert email failed" not in caplog.text
    assert "POSITION DRIFT" not in caplog.text


def test_run_halts_cleanly_and_stays_unrecorded_on_transport_failure(
    tmp_path, monkeypatch
) -> None:
    """An unknown-state failure halts with state written and cron_runs left unwritten.

    Not recording the run matters: cron_runs is the idempotency gate, so leaving
    it unwritten is what lets the next tick retry the legs that never landed.
    """
    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        submit_failure={"SPY": ConnectionError("connection reset by peer")},
    )

    assert result["exit_code"] == 2, "a halt must be visible to the scheduler"
    assert "run" not in result["recorded"], "a halted run must not record cron_runs"

    rejections = result["written"]["rejections"]
    codes = {r["ticker"]: r["reason_code"] for r in rejections}
    assert codes["SPY"] == REASON_SUBMIT_EXCEPTION
    assert codes["LQD"] == REASON_SKIPPED_AFTER_HALT
    assert codes["GLD"] == REASON_SKIPPED_AFTER_HALT


def test_dry_run_writes_no_live_state(tmp_path, monkeypatch) -> None:
    """A dry run must not mutate live Supabase state.

    It never queries Alpaca, so nav_live is the placeholder 100,000 and there are
    no fills. Writing either the placeholder NAV or a zero P&L row would corrupt
    the record the next real run depends on.
    """
    result = _run_execution_with_stub(
        tmp_path, monkeypatch,
        shortable_map={"SPY": True, "LQD": True, "GLD": True},
        dry_run=True,
    )

    assert result["exit_code"] == 0
    assert result["submit_calls"] == 0, "a dry run must not submit anything"
    assert result["written"]["pnl"] == [], "no fabricated P&L row"
    assert result["written"]["positions"] == []
    assert result["written"]["rejections"] == []
    assert result["settings"]["live_nav"] == "100000", (
        "the placeholder NAV must never be written over the account NAV"
    )
    assert "run" not in result["recorded"], (
        "recording cron_runs in a dry run would make the real run skip itself"
    )
