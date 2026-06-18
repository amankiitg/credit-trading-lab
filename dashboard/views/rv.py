"""Historical RV view — 4 panels for the selected pair."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.components import markers as M
from dashboard.components.regime_shade import apply_shading


# pair → (leg1_label, leg1_col, leg2_label, leg2_col, hr_col, resid_col, z_col)
PAIR_SPECS: dict[str, dict] = {
    "rv_hy_ig": dict(
        leg1=("hy_spread", "hy_spread"),
        leg2=("ig_spread", "ig_spread"),
        hr="hedge_ratio_hy_ig",
        resid="rv_hy_ig_residual",
        z="z_rv_hy_ig",
    ),
    "rv_credit_rates": dict(
        leg1=("hy_spread", "hy_spread"),
        leg2=("dgs10 (decimal)", None),  # synthesized from credit_market_data
        hr="hedge_ratio_cr",
        resid="rv_credit_rates_residual",
        z="z_rv_credit_rates",
    ),
    "rv_xterm": dict(
        leg1=("hy_ig", "hy_ig"),
        leg2=("slope (10y-2y, decimal)", None),
        hr=None,  # not stored in features.parquet; reuse hedge_ratio_hy_ig is wrong — see rv_signals.dv01_hedge
        resid="rv_xterm_residual",
        z="z_rv_xterm",
    ),
}


def _slice_dates(df: pd.DataFrame, date_range) -> pd.DataFrame:
    if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        return df
    start, end = date_range
    return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]


@st.cache_data(show_spinner=False)
def _load_rates() -> pd.DataFrame:
    return pd.read_parquet("data/raw/credit_market_data.parquet")


@st.cache_data(show_spinner=False)
def _cached_stats(pair: str, start, end, resid_col: str, hr_col: str | None, residuals: pd.Series, hedge: pd.Series | None) -> dict:
    return _stats_strip_impl(resid_col, hr_col, residuals, hedge)


def _stats_strip_impl(resid_col: str, hr_col: str | None, residuals: pd.Series, hedge: pd.Series | None) -> dict[str, float]:
    from signals.halflife import ou_halflife

    r = residuals.dropna()
    out: dict[str, float] = {}
    try:
        out["half_life"] = float(ou_halflife(r))
    except Exception:
        out["half_life"] = float("nan")
    if hedge is not None and len(hedge.dropna()) > 0:
        hr = hedge.dropna()
        out["beta_mean"] = float(hr.mean())
        out["beta_std"] = float(hr.std())
        if len(hr) > 63:
            mu = hr.rolling(63, min_periods=63).mean()
            sd = hr.rolling(63, min_periods=63).std()
            cv = (sd / mu.abs()).iloc[-1]
            out["cv_63d_last"] = float(cv) if np.isfinite(cv) else float("nan")
        else:
            out["cv_63d_last"] = float("nan")
    else:
        out["beta_mean"] = float("nan")
        out["beta_std"] = float("nan")
        out["cv_63d_last"] = float("nan")
    try:
        from statsmodels.tsa.stattools import adfuller
        out["adf_p"] = float(adfuller(r, autolag="AIC")[1])
    except Exception:
        out["adf_p"] = float("nan")
    return out


def _leg2_series(pair: str, sub: pd.DataFrame) -> pd.Series | None:
    spec = PAIR_SPECS[pair]
    _, leg2_col = spec["leg2"]
    if leg2_col is not None:
        return sub[leg2_col]
    rates = _load_rates().reindex(sub.index).ffill()
    if pair == "rv_credit_rates":
        return rates["dgs10"] / 100.0
    if pair == "rv_xterm":
        return (rates["dgs10"] - rates["dgs2"]) / 100.0
    return None


def _stats_strip(pair: str, sub: pd.DataFrame) -> dict[str, float]:
    resid_col = PAIR_SPECS[pair]["resid"]
    hr_col = PAIR_SPECS[pair].get("hr")
    hedge = sub[hr_col] if hr_col and hr_col in sub.columns else None
    return _stats_strip_impl(resid_col, hr_col, sub[resid_col], hedge)


def render(
    df: pd.DataFrame,
    pair: str = "rv_hy_ig",
    date_range=None,
    entry: float = 2.0,
    exit_t: float = 0.5,
    stop: float = 4.0,
    regime_shading: str | None = "none",
) -> None:
    spec = PAIR_SPECS[pair]
    sub = _slice_dates(df, date_range)
    leg1_label, leg1_col = spec["leg1"]
    leg2_label, _ = spec["leg2"]
    leg2 = _leg2_series(pair, sub)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        subplot_titles=[
            f"Legs — {leg1_label} (left) vs {leg2_label} (right)",
            f"Hedge ratio  ({spec.get('hr') or '—'})",
            f"Residual + z  ({spec['resid']})",
        ],
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": False}],
            [{"secondary_y": True}],
        ],
    )

    # Panel 1 — legs
    fig.add_trace(
        go.Scattergl(x=sub.index, y=sub[leg1_col], name=leg1_label,
                     line=dict(color="#222", width=1.0)),
        row=1, col=1, secondary_y=False,
    )
    if leg2 is not None:
        fig.add_trace(
            go.Scattergl(x=leg2.index, y=leg2.values, name=leg2_label,
                         line=dict(color="#1f77b4", width=1.0)),
            row=1, col=1, secondary_y=True,
        )

    # Panel 2 — hedge ratio (only for pairs where we stored it)
    hr_col = spec.get("hr")
    if hr_col and hr_col in sub.columns:
        fig.add_trace(
            go.Scattergl(x=sub.index, y=sub[hr_col], name=hr_col,
                         line=dict(color="#9467bd", width=1.0)),
            row=2, col=1,
        )
    else:
        fig.add_annotation(
            xref="x2 domain", yref="y2 domain", x=0.5, y=0.5,
            text="Hedge ratio not stored for this pair", showarrow=False,
            font=dict(color="#888"),
        )

    # Panel 3 — residual + z
    fig.add_trace(
        go.Scattergl(x=sub.index, y=sub[spec["resid"]], name="residual",
                     line=dict(color="#222", width=0.9)),
        row=3, col=1, secondary_y=False,
    )
    z = sub[spec["z"]]
    fig.add_trace(
        go.Scattergl(x=sub.index, y=z, name="z (63d)",
                     line=dict(color="#888", width=0.7, dash="dot"), opacity=0.7),
        row=3, col=1, secondary_y=True,
    )
    flags = M.from_thresholds(z, entry=entry, exit_t=exit_t, stop=stop)
    for flag_name, mask in flags.items():
        if not mask.any():
            continue
        pts = sub.loc[mask, spec["resid"]]
        fig.add_trace(
            go.Scattergl(
                x=pts.index, y=pts.values, mode="markers", name=flag_name,
                marker=dict(
                    color=M.FLAG_COLORS[flag_name],
                    symbol=M.FLAG_SYMBOLS[flag_name],
                    size=7, opacity=0.85,
                ),
            ),
            row=3, col=1, secondary_y=False,
        )

    apply_shading(fig, sub, regime_shading, n_rows=3)

    fig.update_layout(
        height=820,
        margin=dict(l=40, r=20, t=50, b=30),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
    )

    st.plotly_chart(fig, width="stretch")

    # Static stats strip
    st.caption("Residual diagnostics · computed on selected date range · threshold-independent")
    stats = _stats_strip(pair, sub)
    cols = st.columns(4)
    cols[0].metric("OU half-life (days)",
                   f"{stats['half_life']:.2f}" if np.isfinite(stats["half_life"]) else "—")
    cols[1].metric("β mean / std",
                   f"{stats['beta_mean']:.3f} / {stats['beta_std']:.3f}"
                   if np.isfinite(stats["beta_mean"]) else "—")
    cols[2].metric("Hedge CV (last 63d)",
                   f"{stats['cv_63d_last']:.4f}" if np.isfinite(stats["cv_63d_last"]) else "—")
    cols[3].metric("ADF p-value",
                   f"{stats['adf_p']:.4f}" if np.isfinite(stats["adf_p"]) else "—")
