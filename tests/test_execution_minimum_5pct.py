"""The execution minimum: 5% of the target notional, with a $250 floor and flips exempt.

The signal bands the WEIGHT at 20% of the desired weight. The execution minimum then
decides whether a dollar delta is worth sending at all. It used to be a flat $250, which
is 0.8% of a $30k name and 0.1% of a $250k one -- far inside the band, so on nearly every
session the execution minimum decided and the book rebalanced to a few hundred dollars of
price drift.

These tests pin the four properties of the replacement: proportional below the floor,
the floor itself, a flip that the band would otherwise suppress, and the dashboard
saying the same thing as the execution job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution import alpaca_paper as ap


def test_a_delta_inside_five_percent_of_the_target_is_skipped() -> None:
    # $3,000 on a $100,000 target is 3%: inside the band.
    assert ap.should_send_order(target_notional=100_000.0, current_notional=97_000.0) is False

    orders = ap.compute_delta_orders({"SPY": 1.0}, {"SPY": 97_000.0}, paper_nav=100_000.0)
    assert orders == []
    assert ap.min_delta_notional(100_000.0) == pytest.approx(5_000.0)


def test_a_delta_past_five_percent_is_sent() -> None:
    # $6,000 on a $100,000 target is 6%: outside the band.
    assert ap.should_send_order(target_notional=100_000.0, current_notional=94_000.0) is True

    orders = ap.compute_delta_orders({"SPY": 1.0}, {"SPY": 94_000.0}, paper_nav=100_000.0)
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].notional == pytest.approx(6_000.0)


def test_a_flip_always_trades_even_under_the_floor() -> None:
    """A $100 long against a $100 short is a reversal, not noise.

    The gap ($200) is under the $250 floor, so the size rule alone would leave the name
    on the wrong side to save a trade -- the one error that compounds. Flips are exempt
    and are routed through the normal crossing path, which closes first and opens after.
    """
    assert ap.is_position_flip(100.0, -100.0) is True
    assert ap.should_send_order(target_notional=100.0, current_notional=-100.0) is True

    orders = ap.compute_delta_orders({"SPY": 0.001}, {"SPY": -100.0}, paper_nav=100_000.0)
    assert [o.position_intent for o in orders] == ["buy_to_close", "buy_to_open"]
    assert [o.leg for o in orders] == [1, 2]


def test_opening_from_flat_and_closing_to_flat_are_not_flips() -> None:
    assert ap.is_position_flip(5_000.0, 0.0) is False
    assert ap.is_position_flip(0.0, -5_000.0) is False
    # Opening from flat is governed by the size rule like any other delta.
    assert ap.should_send_order(target_notional=200.0, current_notional=0.0) is False
    assert ap.should_send_order(target_notional=400.0, current_notional=0.0) is True


def test_a_dust_residue_on_the_wrong_side_is_not_a_flip() -> None:
    """Fifty cents is not a change of thesis.

    The flip exemption exists so a real reversal is never suppressed by the size band.
    A residue smaller than DUST_THRESHOLD_USD is not a reversal: calling it one would
    send a close-and-reopen pair -- two legs and two costs -- to move fifty cents that
    the dust path closes for nothing.
    """
    assert ap.is_position_flip(target_notional=5_000.0, current_notional=-0.50) is False
    assert ap.should_send_order(target_notional=100.0, current_notional=-0.50) is False

    orders = ap.compute_delta_orders({"SPY": 0.001}, {"SPY": -0.50}, paper_nav=100_000.0)
    assert orders == [], "below the floor, and not a flip: nothing to send"


def test_a_real_wrong_side_position_still_flips() -> None:
    """$500 against a long target is a genuine reversal and must trade."""
    assert ap.is_position_flip(target_notional=5_000.0, current_notional=-500.0) is True
    assert ap.should_send_order(target_notional=5_000.0, current_notional=-500.0) is True

    orders = ap.compute_delta_orders({"SPY": 0.05}, {"SPY": -500.0}, paper_nav=100_000.0)
    assert [o.position_intent for o in orders] == ["buy_to_close", "buy_to_open"]


def test_the_absolute_floor_still_applies_to_tiny_positions() -> None:
    """5% of a $2,000 name is $100, so the $250 floor is what decides there."""
    assert ap.min_delta_notional(2_000.0) == pytest.approx(250.0)
    assert ap.should_send_order(target_notional=2_000.0, current_notional=1_800.0) is False
    assert ap.should_send_order(target_notional=2_000.0, current_notional=1_700.0) is True


def test_the_dashboard_uses_the_same_rule_instead_of_its_own_250() -> None:
    """The page's "skip — within band" must be the job's rule, not a copy of the old one.

    A dashboard that says "skip" for an order the cron will send is worse than one with
    no threshold at all, and the two drifted apart precisely because the number was
    written twice.
    """
    source = Path("dashboard/views/operational.py").read_text()
    assert "should_send_order" in source
    assert "abs(delta) < 250" not in source
