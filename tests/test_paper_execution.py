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
from unittest.mock import MagicMock, patch

import pytest

from execution.alpaca_paper import (
    CAP_PER_POSITION_NOTIONAL,
    DELTA_MIN_NOTIONAL,
    MAX_ORDERS_PER_RUN,
    MAX_TRADED_NOTIONAL_PER_RUN,
    PAPER_NAV_DEFAULT,
    FillRecord,
    OrderSpec,
    apply_guards,
    build_fill_records,
    compute_delta_orders,
    mark_costs,
    reconcile,
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
    """P2: a target notional above CAP_PER_POSITION_NOTIONAL must never be
    submitted to Alpaca -- the guard must set guard_status='REJECTED_CAP'.
    """
    oversized_target = CAP_PER_POSITION_NOTIONAL + 500.0  # deliberately above cap
    orders = [
        _pending_spec("SPY", "buy", oversized_target, "buy_to_open", oversized_target)
    ]
    guarded = apply_guards(orders, dry_run=False)

    assert guarded[0].guard_status == "REJECTED_CAP"
    # confirm no PENDING orders remain that could slip through to submission
    pending = [o for o in guarded if o.guard_status == "PENDING"]
    assert len(pending) == 0


def test_guard_accepts_order_at_cap_boundary() -> None:
    """P2 boundary: exactly at the cap should be accepted (strict less-than)."""
    at_cap = CAP_PER_POSITION_NOTIONAL
    orders = [
        _pending_spec("IEF", "buy", at_cap, "buy_to_open", at_cap)
    ]
    guarded = apply_guards(orders, dry_run=False)
    assert guarded[0].guard_status == "PENDING"


def test_guard_rejects_oversized_short_using_abs() -> None:
    """P2: the cap guard uses the absolute value of target_notional so shorts
    are bounded the same way as longs.
    """
    oversized_short = -(CAP_PER_POSITION_NOTIONAL + 100.0)
    orders = [
        _pending_spec("HYG", "sell", abs(oversized_short), "sell_to_open", oversized_short)
    ]
    guarded = apply_guards(orders, dry_run=False)
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
    """
    # Oversized target -> REJECTED_CAP
    oversized = [_pending_spec("TLT", "buy", 9_000.0, "buy_to_open", 9_000.0)]
    # Normal open that passes cap but is within brake
    normal = [_pending_spec("IEF", "buy", 3_000.0, "buy_to_open", 3_000.0)]

    # Build a crossing that is within cap but over the brake
    # close $7,000 + open $6,000 = $13,000 > $12,000 brake
    current_notionals = {"SPY": 7_000.0}
    crossing_orders = compute_delta_orders(
        {"SPY": -0.06}, current_notionals, paper_nav=100_000.0
    )

    all_orders = oversized + crossing_orders + normal
    guarded = apply_guards(all_orders, dry_run=True, _max_traded=12_000.0)

    statuses = {o.ticker: o.guard_status for o in guarded}

    # Oversized target: must still be REJECTED_CAP, not DRY_RUN
    assert statuses["TLT"] == "REJECTED_CAP", (
        f"oversized target must show REJECTED_CAP in dry-run, got {statuses['TLT']}"
    )

    # Crossing over brake: must still be REJECTED_TRADED_NOTIONAL, not DRY_RUN
    spy_statuses = [o.guard_status for o in guarded if o.ticker == "SPY"]
    assert all(s == "REJECTED_TRADED_NOTIONAL" for s in spy_statuses), (
        f"brake-rejected crossing must show REJECTED_TRADED_NOTIONAL in dry-run, got {spy_statuses}"
    )

    # Normal open: passes both guards, becomes DRY_RUN
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


def test_traded_notional_brake_uses_default_constant() -> None:
    """Without an override, MAX_TRADED_NOTIONAL_PER_RUN is 2x the cap."""
    assert MAX_TRADED_NOTIONAL_PER_RUN == 2 * CAP_PER_POSITION_NOTIONAL
