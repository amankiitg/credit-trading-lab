"""D1 — conviction logic truth table + ancillary helpers."""

from __future__ import annotations

import math

import pytest

from dashboard.conviction import (
    BORDER_HIGH,
    BORDER_LOW,
    BORDER_MED,
    Z_GREEN,
    Z_RED,
    Z_YELLOW,
    arrow,
    border_color,
    border_width,
    conviction,
    regime_badge_color,
    z_color,
)

Z_GRID = [-3.0, -2.1, -1.6, -1.0, 0.0, 1.0, 1.6, 2.1, 3.0]
REGIMES = ["equity_first", "credit_first", "neither", None]


def expected(z: float, regime: str | None) -> str:
    a = abs(z)
    is_ef = regime == "equity_first"
    if a > 2.0 and is_ef:
        return "HIGH"
    if a > 2.0 or (a > 1.5 and is_ef):
        return "MED"
    return "LOW"


@pytest.mark.parametrize("z", Z_GRID)
@pytest.mark.parametrize("regime", REGIMES)
def test_conviction_truth_table(z: float, regime: str | None) -> None:
    assert conviction(z, regime) == expected(z, regime), (z, regime)


def test_conviction_high_iff_thesis_active() -> None:
    """The defining invariant: HIGH ⇔ equity_first AND |z|>2."""
    for z in Z_GRID:
        for regime in REGIMES:
            is_high = conviction(z, regime) == "HIGH"
            thesis_active = (regime == "equity_first") and abs(z) > 2.0
            assert is_high == thesis_active, (z, regime, is_high, thesis_active)


def test_conviction_handles_nan_regime() -> None:
    assert conviction(2.5, math.nan) == "MED"      # |z|>2 path
    assert conviction(0.5, math.nan) == "LOW"
    assert conviction(2.5, None) == "MED"


def test_conviction_handles_nan_z() -> None:
    assert conviction(math.nan, "equity_first") == "LOW"
    assert conviction(None, "neither") == "LOW"


@pytest.mark.parametrize(
    "z,want",
    [(-3.0, Z_GREEN), (-2.0, Z_GREEN), (-1.0, Z_YELLOW),
     (-0.5, Z_RED), (0.0, Z_RED), (0.99, Z_RED),
     (1.0, Z_YELLOW), (1.99, Z_YELLOW), (2.0, Z_GREEN), (3.0, Z_GREEN)],
)
def test_z_color(z, want) -> None:
    assert z_color(z) == want


def test_z_color_nan() -> None:
    assert z_color(math.nan) == Z_RED
    assert z_color(None) == Z_RED


@pytest.mark.parametrize(
    "z,want",
    [(2.5, "↓"), (1.0, "↓"), (1.001, "↓"),
     (-2.5, "↑"), (-1.001, "↑"),
     (0.5, "·"), (-0.999, "·"), (0.0, "·")],
)
def test_arrow(z, want) -> None:
    assert arrow(z) == want


def test_arrow_nan() -> None:
    assert arrow(math.nan) == "·"
    assert arrow(None) == "·"


def test_border_color_mapping() -> None:
    assert border_color("HIGH") == BORDER_HIGH
    assert border_color("MED") == BORDER_MED
    assert border_color("LOW") == BORDER_LOW


def test_border_width_mapping() -> None:
    assert border_width("HIGH") == 4
    assert border_width("MED") == 2
    assert border_width("LOW") == 1


def test_regime_badge_color_known_labels() -> None:
    assert regime_badge_color("equity_first") == "#ffb300"
    assert regime_badge_color("credit_first") == "#9c27b0"
    assert regime_badge_color("neither") == "#808080"


def test_regime_badge_color_unknown_or_nan() -> None:
    assert regime_badge_color(None) == "#cccccc"
    assert regime_badge_color(math.nan) == "#cccccc"
    assert regime_badge_color("foobar") == "#cccccc"
