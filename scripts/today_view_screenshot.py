"""Render a static PNG of the Today View as of features.index[-1].

Mirrors the per-card layout in dashboard/views/today.py — same color
palette, same conviction tier, same regime badge, same position text —
so the image can stand in for the dashboard in the walkthrough.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

from dashboard.conviction import (
    arrow,
    border_color,
    border_width,
    conviction,
    regime_badge_color,
    z_color,
)
from dashboard.signal_specs import CARD_SPECS, position_text

OUT = Path("sprints/v4/today_screenshot.png")


def _safe(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def render_today_view(df: pd.DataFrame, entry_threshold: float = 2.0) -> None:
    row = df.iloc[-1]
    as_of = df.index[-1].date()
    n = len(CARD_SPECS)

    fig = plt.figure(figsize=(16, 4.2), dpi=130)
    fig.suptitle(
        f"Today View — Credit RV Signals  ·  as of {as_of}",
        fontsize=12, fontweight="600", y=0.97,
    )

    pad_l, pad_r = 0.015, 0.015
    avail = 1 - pad_l - pad_r
    card_gap = 0.008
    card_w = (avail - (n - 1) * card_gap) / n

    for i, spec in enumerate(CARD_SPECS):
        z_val = _safe(row.get(spec.z_col))
        regime = _safe(row.get(spec.regime_col))
        if hasattr(regime, "item"):
            try:
                regime = regime.item()
            except Exception:
                pass
        regime_str = str(regime) if isinstance(regime, str) else None
        tier = conviction(z_val, regime_str)
        zc = z_color(z_val)
        arr = arrow(z_val)
        bc = border_color(tier)
        bw = border_width(tier)
        badge = regime_badge_color(regime_str)
        pos = position_text(spec, z_val, entry=entry_threshold)
        z_text = "—" if z_val is None else f"{z_val:+.2f}"
        regime_label = regime_str if regime_str else "—"

        left = pad_l + i * (card_w + card_gap)
        ax = fig.add_axes([left, 0.05, card_w, 0.83])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color(bc)
            s.set_linewidth(bw)

        # Signal name
        ax.text(0.04, 0.91, spec.name, fontsize=10, fontweight="600",
                color="#222", va="top")
        # Big z + arrow
        ax.text(0.04, 0.62, z_text, fontsize=22, fontweight="700",
                color=zc, va="center")
        ax.text(0.52, 0.62, arr, fontsize=22, color=zc, va="center")
        # Regime badge
        badge_w = 0.4
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.04, 0.36), badge_w, 0.10,
            boxstyle="round,pad=0.005,rounding_size=0.05",
            facecolor=badge, edgecolor="none",
        ))
        ax.text(0.04 + badge_w / 2, 0.41, regime_label,
                fontsize=8, fontweight="700", color="white",
                ha="center", va="center")
        # Tier
        ax.text(0.04, 0.25, tier, fontsize=10, fontweight="700",
                color=bc, va="center")
        # Position text
        ax.text(0.04, 0.10, pos, fontsize=8, color="#444",
                va="center", wrap=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=140, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    df = pd.read_parquet("data/processed/features.parquet")
    render_today_view(df)
