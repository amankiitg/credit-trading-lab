"""Trade cost model — half-spread + slippage + short borrow.

All constants are pre-registered in sprints/v5/PRD.md §Signal
Definition and must not be tuned to results.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pre-registered constants (PRD §Costs)
HALF_SPREAD_BP: float = 1.5      # per leg, per side
SLIPPAGE_BP: float = 0.5         # per trade (entry + exit combined leg-agnostic)
BORROW_ANNUAL: float = 0.004     # 0.40% / year on the short leg
TRADING_DAYS: int = 252

_BP = 1e-4


@dataclass(frozen=True)
class CostParams:
    """Immutable cost configuration. Defaults are the PRD constants."""

    half_spread_bp: float = HALF_SPREAD_BP
    slippage_bp: float = SLIPPAGE_BP
    borrow_annual: float = BORROW_ANNUAL
    n_legs: int = 2


def trade_cost(
    notional: float,
    holding_days: float,
    params: CostParams = CostParams(),
) -> float:
    """Total round-trip cost of one trade, in dollars.

    Components:
      - half-spread: n_legs × 2 sides × half_spread_bp × notional
      - slippage:    2 sides × slippage_bp × notional
      - borrow:      borrow_annual × (holding_days / 252) × notional

    Monotone increasing in both ``notional`` and ``holding_days``.
    """
    if notional < 0:
        raise ValueError(f"notional must be >= 0, got {notional}")
    if holding_days < 0:
        raise ValueError(f"holding_days must be >= 0, got {holding_days}")

    spread = params.n_legs * 2 * params.half_spread_bp * _BP * notional
    slippage = 2 * params.slippage_bp * _BP * notional
    borrow = params.borrow_annual * (holding_days / TRADING_DAYS) * notional
    return spread + slippage + borrow
