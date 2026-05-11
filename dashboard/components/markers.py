"""Marker frames derived from z-score thresholds for historical charts.

`from_thresholds(z, entry, exit_t, stop)` returns a dict of boolean
Series (entry_long, entry_short, exit, stop) matching the Sprint-1
flag semantics.

`from_flags(df, spread)` reads the pre-computed flag columns from
features.parquet for the directional view. These two views differ
because slider-driven re-classification needs to use the current
slider values, not the cached flag columns.
"""

from __future__ import annotations

import pandas as pd

FLAG_NAMES = ("entry_long", "entry_short", "exit", "stop")
FLAG_COLORS = {
    "entry_long": "#1b8a3a",   # green
    "entry_short": "#d9a116",  # yellow / amber
    "exit": "#d62728",         # red (take profit / close trade)
    "stop": "#111111",         # black X (stop-loss)
}
FLAG_SYMBOLS = {
    "entry_long": "triangle-up",
    "entry_short": "triangle-down",
    "exit": "circle",
    "stop": "x",
}


def from_thresholds(
    z: pd.Series,
    entry: float,
    exit_t: float,
    stop: float,
) -> dict[str, pd.Series]:
    """Apply Sprint-1 flag semantics to a z-score series."""
    absz = z.abs()
    return {
        "entry_long": (z < -entry).fillna(False),
        "entry_short": (z > entry).fillna(False),
        "exit": (absz < exit_t).fillna(False),
        "stop": (absz > stop).fillna(False),
    }


def from_flags(df: pd.DataFrame, spread: str) -> dict[str, pd.Series]:
    """Read pre-computed flags for a directional spread."""
    return {f: df[f"{spread}_{f}"] for f in FLAG_NAMES}
