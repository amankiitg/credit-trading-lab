"""Sprint v8.6 Attribution Lab -- Streamlit entrypoint.

Run from project root:
    streamlit run dashboard/app.py

Two tabs: Attribution Lab (v8.3 forensic panels) and Trades & Positions
(proposed trade + Approve/Reject + Supabase).

Auth: Google OIDC restricted to ALLOWED_EMAIL (exact case-sensitive match).
Requires .streamlit/secrets.toml with [auth] and [auth.google] sections.
Degrades gracefully when secrets.toml is absent: shows a warning and uses
ALLOWED_EMAIL as a local dev passthrough (no OIDC redirect).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if os.getcwd() != _ROOT:
    os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# -------------------------------------------------------------- secrets bootstrap
# On Render, write .streamlit/secrets.toml from env vars before st.secrets is
# accessed for the first time (Streamlit lazy-loads on first access).
# This runs on every worker startup so it survives container restarts.
_google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
if _google_client_id:
    _secrets_toml = Path(_ROOT) / ".streamlit" / "secrets.toml"
    if not _secrets_toml.exists():
        _secrets_toml.parent.mkdir(exist_ok=True)
        _secrets_toml.write_text(
            "[auth]\n"
            f'redirect_uri  = "{os.environ.get("STREAMLIT_AUTH_REDIRECT_URI", "")}"\n'
            f'cookie_secret = "{os.environ.get("STREAMLIT_COOKIE_SECRET", "")}"\n'
            "\n"
            "[auth.google]\n"
            f'client_id           = "{_google_client_id}"\n'
            f'client_secret       = "{os.environ.get("GOOGLE_CLIENT_SECRET", "")}"\n'
            'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n'
        )

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="Attribution Lab -- v8.6",
    page_icon=":test_tube:",
    layout="wide",
)

# ----------------------------------------------------------------- auth gate
_ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "")
try:
    _secrets_configured = (
        "auth" in st.secrets
        and "google" in st.secrets.get("auth", {})
    )
except Exception:
    _secrets_configured = False

if _secrets_configured:
    if not st.user.is_logged_in:
        st.login("google")
        st.stop()

    _user_email: str = st.user.email or ""
    if _user_email != _ALLOWED_EMAIL:
        st.error(
            f"Access denied. This dashboard is restricted to one authorized account. "
            f"You are signed in as **{_user_email}**. "
            f"Please sign out and use the authorized account."
        )
        st.stop()
else:
    # Local dev fallback: no OIDC redirect, use ALLOWED_EMAIL as passthrough.
    # Do NOT deploy in this mode.
    st.warning(
        "Google OIDC not configured. Running in local dev mode -- "
        "create .streamlit/secrets.toml to enable auth.",
        icon="⚠",
    )
    _user_email = _ALLOWED_EMAIL or "local"

# ----------------------------------------------------------------- tabs

tab_attr, tab_ops = st.tabs(["Attribution Lab", "Trades and Positions"])

with tab_attr:
    from dashboard.views import attribution as attribution_view
    attribution_view.render()

with tab_ops:
    from dashboard.views import operational as operational_view
    operational_view.render(user_email=_user_email)
