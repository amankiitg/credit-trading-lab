"""Conviction tier logic for the Today View — pure functions.

The thesis (from Sprint v3 §C22): RV1 mean-reverts faster on
`equity_first` regime days than on `neither` days. The dashboard
encodes that as a per-signal conviction:

- HIGH iff `equity_credit_lag == 'equity_first'` AND `|z| > 2`.
- MED  iff `|z| > 2` OR (`|z| > 1.5` AND `equity_first`), but not HIGH.
- LOW  otherwise.

`conviction`, `z_color`, `arrow`, `border_color`, and `regime_badge`
are all pure and side-effect-free.
"""

from __future__ import annotations

import math
from typing import Literal

Tier = Literal["HIGH", "MED", "LOW"]

# Color constants — match PRD §Signal Definition
Z_GREEN = "#1b8a3a"
Z_YELLOW = "#d9a116"
Z_RED = "#b04040"
BORDER_HIGH = "#1b8a3a"
BORDER_MED = "#d9a116"
BORDER_LOW = "#a0a0a0"

REGIME_COLORS = {
    "equity_first": "#ffb300",
    "credit_first": "#9c27b0",
    "neither": "#808080",
}


def _abs_or_nan(z: float | None) -> float:
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return float("nan")
    return abs(float(z))


def conviction(z: float | None, regime: str | None) -> Tier:
    """Return HIGH / MED / LOW per the PRD §D1 truth table."""
    a = _abs_or_nan(z)
    if math.isnan(a):
        return "LOW"
    is_equity_first = regime == "equity_first"
    if a > 2.0 and is_equity_first:
        return "HIGH"
    if a > 2.0 or (a > 1.5 and is_equity_first):
        return "MED"
    return "LOW"


def z_color(z: float | None) -> str:
    """Cell color for the z-score number."""
    a = _abs_or_nan(z)
    if math.isnan(a):
        return Z_RED
    if a >= 2.0:
        return Z_GREEN
    if a >= 1.0:
        return Z_YELLOW
    return Z_RED


def arrow(z: float | None) -> str:
    """Direction-of-trade arrow.

    Positive z (signal extended high) → expect snap down → ↓
    Negative z (extended low) → expect snap up → ↑
    |z| < 1 → · (flat).
    """
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return "·"
    if abs(z) < 1.0:
        return "·"
    return "↓" if z > 0 else "↑"


def border_color(tier: Tier) -> str:
    return {"HIGH": BORDER_HIGH, "MED": BORDER_MED, "LOW": BORDER_LOW}[tier]


def border_width(tier: Tier) -> int:
    return {"HIGH": 4, "MED": 2, "LOW": 1}[tier]


def regime_badge_color(regime: str | None) -> str:
    if regime is None or (isinstance(regime, float) and math.isnan(regime)):
        return "#cccccc"
    return REGIME_COLORS.get(regime, "#cccccc")
