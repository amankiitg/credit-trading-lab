"""Regime background-shading spans for Plotly figures.

Given a regime column (categorical), return a list of
``(start_date, end_date, label, color)`` tuples — one per contiguous
run of the same label — to be fed into Plotly's ``add_vrect``.
"""

from __future__ import annotations

import pandas as pd

REGIME_PALETTES: dict[str, dict[str, str]] = {
    "vol_regime": {
        "high": "rgba(255, 90, 90, 0.28)",
        "low": "rgba(90, 130, 255, 0.22)",
    },
    "equity_credit_lag": {
        "equity_first": "rgba(255, 179, 0, 0.32)",
        "credit_first": "rgba(156, 39, 176, 0.26)",
        "neither": "rgba(200, 200, 200, 0.10)",
    },
}


def spans(
    df: pd.DataFrame,
    regime_col: str,
) -> list[tuple[pd.Timestamp, pd.Timestamp, str, str]]:
    """Return ``(start, end, label, color)`` for every contiguous run.

    `start` = first date of the run, `end` = last date of the run.
    NaN labels are skipped (gaps in the timeline). For each run the
    palette lookup gives the fill color; if the label is unknown,
    falls back to a neutral gray.
    """
    if regime_col not in df.columns:
        raise KeyError(regime_col)
    s = df[regime_col]
    # treat the categorical / object series as plain strings, NaN → None
    labels = s.astype(object).where(s.notna(), other=None).tolist()
    idx = df.index
    palette = REGIME_PALETTES.get(regime_col, {})

    out: list[tuple[pd.Timestamp, pd.Timestamp, str, str]] = []
    if not labels:
        return out

    cur = labels[0]
    start = idx[0]
    for i in range(1, len(labels)):
        if labels[i] != cur:
            if cur is not None:
                color = palette.get(str(cur), "rgba(200,200,200,0.06)")
                out.append((start, idx[i - 1], str(cur), color))
            cur = labels[i]
            start = idx[i]
    if cur is not None:
        color = palette.get(str(cur), "rgba(200,200,200,0.06)")
        out.append((start, idx[-1], str(cur), color))
    return out


def apply_shading(fig, df: pd.DataFrame, regime_col: str | None, n_rows: int = 1) -> None:
    """Add regime background spans behind each subplot's data area.

    Per-subplot ``yref="{yaxis} domain"`` keeps the shading inside the
    plotting region only — title gutters between panels remain
    unshaded. Primary y-axes are discovered via ``select_yaxes`` so
    secondary-y configurations don't break the layout.

    No-op when ``regime_col`` is ``None`` or ``"none"``.
    """
    if not regime_col or regime_col == "none":
        return
    ranges = spans(df, regime_col)
    if not ranges:
        return

    yaxis_refs: list[str] = []
    for r in range(1, n_rows + 1):
        try:
            ax = next(fig.select_yaxes(row=r, col=1, secondary_y=False))
        except (StopIteration, ValueError):
            try:
                ax = next(fig.select_yaxes(row=r, col=1))
            except (StopIteration, ValueError):
                continue
        # plotly_name → "yaxis", "yaxis3", "yaxis5" → "y", "y3", "y5"
        yref_name = ax.plotly_name.replace("axis", "")
        yaxis_refs.append(yref_name)

    shapes = list(fig.layout.shapes or [])
    for yref in yaxis_refs:
        for start, end, _label, color in ranges:
            shapes.append(dict(
                type="rect",
                xref="x",
                yref=f"{yref} domain",
                x0=start, x1=end, y0=0, y1=1,
                fillcolor=color, line=dict(width=0),
                layer="below", opacity=1.0,
            ))
    fig.update_layout(shapes=shapes)
