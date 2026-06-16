"""Sprint v8.5 Attribution Lab + Sprint v4 Signal Visualizer -- Streamlit entrypoint.

Run from project root:
    streamlit run dashboard/app.py

Authentication: requires Google OIDC configured in .streamlit/secrets.toml
(gitignored, never committed) and ALLOWED_EMAIL set in .env.

Attribution Lab leads with the forensic panels from v8.3. The v4 Today View
and historical signal views follow in a separate tab. No live Alpaca connection
in v8.5 -- operational panels are stubbed with TODO v8.6.
"""

from __future__ import annotations

import os
import sys

# Run from the repo root so relative parquet paths resolve.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if os.getcwd() != _ROOT:
    os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="Attribution Lab -- v8.5",
    page_icon=":test_tube:",
    layout="wide",
)

# --------------------------------------------------------------------- auth
# Requires [auth] + [auth.google] in .streamlit/secrets.toml (gitignored).
# ALLOWED_EMAIL must be set in .env.

_auth_configured = hasattr(st, "user") and hasattr(st.user, "is_logged_in")

if _auth_configured and not st.user.is_logged_in:
    st.title("Attribution Lab")
    st.write(
        "This is a private attribution dashboard. "
        "Sign in with the authorized Google account to continue."
    )
    if st.button("Sign in with Google"):
        st.login("google")
    st.stop()

_allowed = os.environ.get("ALLOWED_EMAIL", "")
_user_email = ""

if _auth_configured and st.user.is_logged_in:
    _user_email = st.user.email or ""
    if _allowed and _user_email != _allowed:
        st.error(f"Access denied: {_user_email} is not authorized.")
        if st.button("Sign out"):
            st.logout()
        st.stop()
elif _auth_configured and not st.user.is_logged_in and not _allowed:
    # Auth not configured: warn but allow local development without OIDC.
    st.warning(
        "Google OIDC is not configured (.streamlit/secrets.toml missing). "
        "Running in unauthenticated local-dev mode. "
        "Do not deploy without configuring auth."
    )

# --------------------------------------------------------------------- sidebar (shared)

from dashboard.loader import as_of_date, load_features  # noqa: E402

df = load_features()
as_of = as_of_date(df)

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
    key=f"start_{preset}",
)
end_d = st.sidebar.date_input(
    "End", value=default_end, min_value=date_min, max_value=date_max,
    key=f"end_{preset}",
)
if start_d > end_d:
    start_d, end_d = end_d, start_d
    st.sidebar.warning("Start > End -- auto-swapped.")
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

if _user_email:
    st.sidebar.markdown("---")
    st.sidebar.caption(f"Signed in as: {_user_email}")
    if st.sidebar.button("Sign out"):
        st.logout()

# --------------------------------------------------------------------- tabs

tab_attr, tab_ops, tab_today, tab_hist = st.tabs([
    "Attribution Lab",
    "Trades and Positions",
    "Today View",
    "Historical Signals",
])

# --------------------------------------------------------------------- Tab 1: Attribution Lab

with tab_attr:
    from dashboard.views import attribution as attribution_view  # noqa: E402
    attribution_view.render()

# --------------------------------------------------------------------- Tab 2: Trades and Positions

with tab_ops:
    from dashboard.views import operational as operational_view  # noqa: E402
    operational_view.render(user_email=_user_email)

# --------------------------------------------------------------------- Tab 3: Today View (original Sprint 4)

with tab_today:
    from dashboard.views import today as today_view  # noqa: E402
    today_view.render(df, entry_threshold=entry)

# --------------------------------------------------------------------- Tab 4: Historical Signals

with tab_hist:
    if family == "Historical Directional":
        st.subheader(f"Historical Directional -- {selected_pair}")
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
        st.subheader(f"Historical RV -- {selected_pair}")
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
