"""Today View — six horizontal cards always visible at the top.

Each card encodes one signal at the as-of date:
  signal name | z (colored) | arrow | regime badge | conviction | position text

Card border thickness/color encodes conviction tier.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from dashboard.conviction import (
    arrow,
    border_color,
    border_width,
    conviction,
    regime_badge_color,
    z_color,
)
from dashboard.signal_specs import CARD_SPECS, position_text


def _fmt(z: float | None) -> str:
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return "—"
    return f"{z:+.2f}"


def _card_html(spec, z, regime, tier, entry_threshold) -> str:
    z_c = z_color(z)
    arr = arrow(z)
    badge_color = regime_badge_color(regime)
    badge_label = regime if isinstance(regime, str) and regime else "—"
    bc = border_color(tier)
    bw = border_width(tier)
    pos = position_text(spec, z, entry=entry_threshold)
    tier_color = bc
    return f"""
<div style="
  border: {bw}px solid {bc};
  border-radius: 6px;
  padding: 10px 12px;
  margin: 4px 0;
  background-color: #ffffff;
  font-family: -apple-system, system-ui, sans-serif;
  min-height: 168px;
">
  <div style="font-weight: 600; font-size: 13px; color: #222; margin-bottom: 6px;">
    {spec.name}
  </div>
  <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px;">
    <span style="font-size: 26px; font-weight: 700; color: {z_c};">{_fmt(z)}</span>
    <span style="font-size: 22px; color: {z_c};">{arr}</span>
  </div>
  <div style="margin-bottom: 6px;">
    <span style="
      display: inline-block;
      background: {badge_color};
      color: #ffffff;
      font-size: 10px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 10px;
      letter-spacing: 0.3px;
    ">{badge_label}</span>
  </div>
  <div style="font-size: 11px; font-weight: 700; color: {tier_color}; margin-bottom: 6px;">
    {tier}
  </div>
  <div style="font-size: 11px; color: #444; line-height: 1.3;">{pos}</div>
</div>
"""


def render(df: pd.DataFrame, entry_threshold: float = 2.0) -> None:
    """Render the 6-card horizontal Today View."""
    last_date = df.index[-1]
    st.markdown(
        f"<div style='font-size: 12px; color: #666; margin-bottom: 6px;'>"
        f"<b>Today View</b> &nbsp; as of <code>{last_date.date()}</code> "
        f"&nbsp;(latest bar in features.parquet)</div>",
        unsafe_allow_html=True,
    )
    row = df.iloc[-1]
    cols = st.columns(len(CARD_SPECS))
    for col, spec in zip(cols, CARD_SPECS):
        z_val = row.get(spec.z_col)
        regime = row.get(spec.regime_col)
        # pd categorical handling
        if hasattr(regime, "item"):
            try:
                regime = regime.item()
            except Exception:
                pass
        if isinstance(regime, float) and math.isnan(regime):
            regime = None
        tier = conviction(z_val, regime if isinstance(regime, str) else None)
        with col:
            st.markdown(
                _card_html(spec, z_val, regime, tier, entry_threshold),
                unsafe_allow_html=True,
            )
