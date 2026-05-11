"""Sprint v4 Signal Visualizer — Streamlit entrypoint.

Run from project root:
    streamlit run dashboard/app.py

Today View is always pinned at the top; the sidebar selects which
historical view (Directional or RV) renders below.
"""

from __future__ import annotations

import os
import sys

# Ensure we run from the repo root so the relative parquet path resolves.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if os.getcwd() != _ROOT:
    os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from dashboard.loader import as_of_date, load_features  # noqa: E402
from dashboard.views import today as today_view  # noqa: E402


st.set_page_config(page_title="Credit RV Signals", layout="wide")

df = load_features()
as_of = as_of_date(df)

# ----------------------------------------------------------------- sidebar

st.sidebar.title("Controls")

family = st.sidebar.radio(
    "Family",
    ["Historical Directional", "Historical RV"],
    key="family",
)

if family == "Historical Directional":
    selected_pair = st.sidebar.selectbox(
        "Spread",
        ["hy_spread", "ig_spread", "hy_ig"],
        key="dir_pair",
    )
else:
    selected_pair = st.sidebar.selectbox(
        "RV pair",
        ["rv_hy_ig", "rv_credit_rates", "rv_xterm"],
        key="rv_pair",
    )

date_min = df.index[0].date()
date_max = df.index[-1].date()

# Quick presets in addition to manual start/end pickers.
preset = st.sidebar.selectbox(
    "Date range preset",
    ["Full", "Last 10y", "Last 5y", "Last 1y", "Last 6m", "Custom"],
    index=0,
    key="date_preset",
)
_PRESETS_DAYS = {"Last 10y": 365 * 10, "Last 5y": 365 * 5, "Last 1y": 365, "Last 6m": 183}
if preset == "Full":
    default_start, default_end = date_min, date_max
elif preset == "Custom":
    default_start, default_end = date_min, date_max
else:
    import datetime as _dt
    default_end = date_max
    default_start = max(date_min, date_max - _dt.timedelta(days=_PRESETS_DAYS[preset]))

start_d = st.sidebar.date_input(
    "Start", value=default_start, min_value=date_min, max_value=date_max,
    key=f"start_{preset}",  # rotate key so preset changes the rendered default
)
end_d = st.sidebar.date_input(
    "End", value=default_end, min_value=date_min, max_value=date_max,
    key=f"end_{preset}",
)
# Guard against the user inverting the range.
if start_d > end_d:
    start_d, end_d = end_d, start_d
    st.sidebar.warning("Start > End — auto-swapped.")
date_range = (start_d, end_d)

st.sidebar.markdown("**Thresholds (z-score)**")
entry = st.sidebar.slider("entry", 0.5, 4.0, 2.0, 0.1, key="entry")
exit_t = st.sidebar.slider("exit", 0.0, 2.0, 0.5, 0.1, key="exit")
stop = st.sidebar.slider("stop", 2.0, 6.0, 4.0, 0.1, key="stop")

regime_shading = st.sidebar.selectbox(
    "Regime shading",
    ["none", "vol_regime", "equity_credit_lag"],
    key="regime_shading",
)

# ----------------------------------------------------------------- Today View (sticky)

today_view.render(df, entry_threshold=entry)

st.markdown(
    "<hr style='margin: 10px 0; border: 0; border-top: 1px solid #ddd;'/>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------- Historical view

if family == "Historical Directional":
    st.subheader(f"Historical Directional — {selected_pair}")
    try:
        from dashboard.views import directional as directional_view  # noqa: E402

        directional_view.render(
            df,
            selected_pair=selected_pair,
            date_range=date_range,
            entry=entry,
            exit_t=exit_t,
            stop=stop,
            regime_shading=regime_shading,
        )
    except ImportError:
        st.info("Directional view not yet implemented.")
else:
    st.subheader(f"Historical RV — {selected_pair}")
    try:
        from dashboard.views import rv as rv_view  # noqa: E402

        rv_view.render(
            df,
            pair=selected_pair,
            date_range=date_range,
            entry=entry,
            exit_t=exit_t,
            stop=stop,
            regime_shading=regime_shading,
        )
    except ImportError:
        st.info("RV view not yet implemented.")
