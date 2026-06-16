"""Sprint v8.5 Attribution Lab -- Streamlit entrypoint.

Run from project root:
    streamlit run dashboard/app.py

Two tabs: Attribution Lab (v8.3 forensic panels) and Trades & Positions
(proposed trade + Approve/Reject + Supabase). No auth in v8.5 local build.
"""

from __future__ import annotations

import os
import sys

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

# Auth disabled for local dev -- TODO v8.6: restore before deploying
_user_email = os.environ.get("ALLOWED_EMAIL", "local")

# ----------------------------------------------------------------- tabs

tab_attr, tab_ops = st.tabs(["Attribution Lab", "Trades and Positions"])

with tab_attr:
    from dashboard.views import attribution as attribution_view
    attribution_view.render()

with tab_ops:
    from dashboard.views import operational as operational_view
    operational_view.render(user_email=_user_email)
