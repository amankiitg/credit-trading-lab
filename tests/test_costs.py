"""W1 — trade cost model."""

from __future__ import annotations

import pytest

from execution.costs import (
    BORROW_ANNUAL,
    HALF_SPREAD_BP,
    SLIPPAGE_BP,
    CostParams,
    trade_cost,
)


def test_constants_match_prd() -> None:
    assert HALF_SPREAD_BP == 1.5
    assert SLIPPAGE_BP == 0.5
    assert BORROW_ANNUAL == 0.004


def test_hand_computed_trade() -> None:
    """$1,000,000 notional, 20 days held, 2 legs.

    spread   = 2 legs × 2 sides × 1.5bp × 1e6 = 4 × 1.5e-4 × 1e6 = 600
    slippage = 2 sides × 0.5bp × 1e6          = 2 × 0.5e-4 × 1e6 = 100
    borrow   = 0.004 × (20/252) × 1e6                            = 317.4603...
    total    = 1017.4603...
    """
    cost = trade_cost(1_000_000, 20)
    spread = 4 * 1.5e-4 * 1e6
    slippage = 2 * 0.5e-4 * 1e6
    borrow = 0.004 * (20 / 252) * 1e6
    assert cost == pytest.approx(spread + slippage + borrow, abs=1e-9)
    assert cost == pytest.approx(1017.460317, abs=1e-4)


def test_zero_holding_days_is_spread_plus_slippage_only() -> None:
    cost = trade_cost(1_000_000, 0)
    assert cost == pytest.approx(700.0, abs=1e-9)  # 600 spread + 100 slippage


def test_monotone_in_holding_days() -> None:
    base = trade_cost(1_000_000, 5)
    assert trade_cost(1_000_000, 10) > base
    assert trade_cost(1_000_000, 50) > trade_cost(1_000_000, 10)


def test_monotone_in_notional() -> None:
    base = trade_cost(500_000, 20)
    assert trade_cost(1_000_000, 20) > base
    assert trade_cost(2_000_000, 20) > trade_cost(1_000_000, 20)


def test_scales_linearly_with_notional() -> None:
    assert trade_cost(2_000_000, 20) == pytest.approx(2 * trade_cost(1_000_000, 20))


def test_negative_inputs_raise() -> None:
    with pytest.raises(ValueError):
        trade_cost(-1, 10)
    with pytest.raises(ValueError):
        trade_cost(1_000_000, -1)


def test_custom_params() -> None:
    p = CostParams(half_spread_bp=3.0, slippage_bp=1.0, borrow_annual=0.01, n_legs=1)
    # spread = 1 × 2 × 3bp × 1e6 = 6000e-4×... = 600 ; slippage = 2×1bp×1e6 = 200
    cost = trade_cost(1_000_000, 0, p)
    assert cost == pytest.approx(1 * 2 * 3e-4 * 1e6 + 2 * 1e-4 * 1e6, abs=1e-9)
