"""Signal-state flags derived from z-scores.

Converts continuous z-score columns into four boolean state flags per
spread: entry_long / entry_short / exit / stop. Thresholds are
symmetric — entry_long fires when the z-score drops below −entry and a
long mean-reversion trade is implied; entry_short fires at +entry. The
flags are stateless (no position memory) — a strategy layer above will
combine them into trades.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FlagThresholds:
    """Default thresholds from the PRD. All distances in z-score units."""

    entry: float = 2.0
    exit: float = 0.5
    stop: float = 4.0


DEFAULT_FLAG_WINDOW: int = 63
FLAG_NAMES: tuple[str, ...] = ("entry_long", "entry_short", "exit", "stop")


def compute_flags(
    df: pd.DataFrame,
    spreads: list[str],
    window: int = DEFAULT_FLAG_WINDOW,
    thresholds: FlagThresholds = FlagThresholds(),
) -> pd.DataFrame:
    """Return bool-dtype flags for each spread using its ``_z{window}`` column.

    Output columns: for each ``s`` in ``spreads``,
    ``{s}_entry_long``, ``{s}_entry_short``, ``{s}_exit``, ``{s}_stop``.
    All output cells are guaranteed non-NaN: rows where the underlying
    z-score is NaN (i.e. inside the warmup window) are False rather
    than NaN, because a missing z-score is not evidence of a trade.
    """
    if thresholds.exit >= thresholds.entry:
        raise ValueError("exit threshold must be strictly below entry")
    if thresholds.stop <= thresholds.entry:
        raise ValueError("stop threshold must be strictly above entry")

    out = pd.DataFrame(index=df.index)
    for s in spreads:
        col = f"{s}_z{window}"
        if col not in df.columns:
            raise KeyError(f"missing z-score column {col!r}")
        z = df[col]
        absz = z.abs()
        # NaN-safe: `<` / `>` with NaN produce False, which is what we want
        out[f"{s}_entry_long"] = (z < -thresholds.entry).fillna(False).astype(bool)
        out[f"{s}_entry_short"] = (z > thresholds.entry).fillna(False).astype(bool)
        out[f"{s}_exit"] = (absz < thresholds.exit).fillna(False).astype(bool)
        out[f"{s}_stop"] = (absz > thresholds.stop).fillna(False).astype(bool)
    return out


# Phase 3 will populate these; for now the pipeline reserves the column
# names so downstream consumers (dashboard, backtest) can depend on a
# stable schema.
RV_STUB_COLUMNS: tuple[str, ...] = (
    "rv_hy_ig_residual",
    "rv_credit_rates_residual",
    "rv_xterm_residual",
    "hedge_ratio_hy_ig",
    "hedge_ratio_cr",
)


def rv_stubs(index: pd.Index) -> pd.DataFrame:
    """Return a frame of all-NaN RV placeholder columns."""
    return pd.DataFrame(
        {c: np.full(len(index), np.nan, dtype="float64") for c in RV_STUB_COLUMNS},
        index=index,
    )
