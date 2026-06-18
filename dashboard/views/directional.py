"""Historical Directional view — 3 synced spread panels with markers."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.components import markers as M
from dashboard.components.regime_shade import apply_shading

SPREADS = ("hy_spread", "ig_spread", "hy_ig")


def _slice_dates(df: pd.DataFrame, date_range) -> pd.DataFrame:
    if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        return df
    start, end = date_range
    return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]


def render(
    df: pd.DataFrame,
    selected_pair: str = "hy_spread",
    date_range=None,
    entry: float = 2.0,
    exit_t: float = 0.5,
    stop: float = 4.0,
    regime_shading: str | None = "none",
) -> None:
    sub = _slice_dates(df, date_range)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        subplot_titles=[f"{s} (raw + 63d z-score)" for s in SPREADS],
        specs=[[{"secondary_y": True}], [{"secondary_y": True}], [{"secondary_y": True}]],
    )

    for r, spread in enumerate(SPREADS, start=1):
        # Spread line
        fig.add_trace(
            go.Scattergl(
                x=sub.index, y=sub[spread], name=spread,
                line=dict(color="#222", width=1.0), showlegend=(r == 1),
                hovertemplate="%{x|%Y-%m-%d}<br>"+spread+"=%{y:.4f}<extra></extra>",
            ),
            row=r, col=1, secondary_y=False,
        )
        # z-score overlay
        z = sub[f"{spread}_z63"]
        fig.add_trace(
            go.Scattergl(
                x=sub.index, y=z, name="z63",
                line=dict(color="#888", width=0.7, dash="dot"), opacity=0.7,
                showlegend=(r == 1),
                hovertemplate="%{x|%Y-%m-%d}<br>z=%{y:.2f}<extra></extra>",
            ),
            row=r, col=1, secondary_y=True,
        )
        # Threshold-driven markers
        flags = M.from_thresholds(z, entry=entry, exit_t=exit_t, stop=stop)
        for flag_name, mask in flags.items():
            if not mask.any():
                continue
            pts = sub.loc[mask, spread]
            fig.add_trace(
                go.Scattergl(
                    x=pts.index, y=pts.values, mode="markers", name=flag_name,
                    marker=dict(
                        color=M.FLAG_COLORS[flag_name],
                        symbol=M.FLAG_SYMBOLS[flag_name],
                        size=7, opacity=0.85,
                    ),
                    showlegend=(r == 1),
                    hovertemplate="%{x|%Y-%m-%d}<br>"+flag_name+"<extra></extra>",
                ),
                row=r, col=1, secondary_y=False,
            )

    apply_shading(fig, sub, regime_shading, n_rows=3)

    fig.update_layout(
        height=720,
        margin=dict(l=40, r=20, t=40, b=30),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
    )
    for r in range(1, 4):
        fig.update_yaxes(title_text="spread", row=r, col=1, secondary_y=False)
        fig.update_yaxes(title_text="z", row=r, col=1, secondary_y=True, showgrid=False)

    st.plotly_chart(fig, width="stretch")
