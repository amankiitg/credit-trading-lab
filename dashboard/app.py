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
def _toml_val(v: str) -> str:
    """Escape a string value for a TOML basic string (double-quoted)."""
    return v.strip("\"'").replace("\\", "\\\\").replace('"', '\\"')

_google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
_oidc_env_vars = {
    k: bool(os.environ.get(k))
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
              "STREAMLIT_COOKIE_SECRET", "STREAMLIT_AUTH_REDIRECT_URI")
}
print(f"[app] OIDC env vars present: {_oidc_env_vars}", flush=True)

if _google_client_id:
    _secrets_toml = Path(_ROOT) / ".streamlit" / "secrets.toml"
    _secrets_toml.parent.mkdir(exist_ok=True)
    _secrets_toml.write_text(
        "[auth]\n"
        f'redirect_uri  = "{_toml_val(os.environ.get("STREAMLIT_AUTH_REDIRECT_URI", ""))}"\n'
        f'cookie_secret = "{_toml_val(os.environ.get("STREAMLIT_COOKIE_SECRET", ""))}"\n'
        "\n"
        "[auth.google]\n"
        f'client_id           = "{_toml_val(_google_client_id)}"\n'
        f'client_secret       = "{_toml_val(os.environ.get("GOOGLE_CLIENT_SECRET", ""))}"\n'
        'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n'
    )
    print(f"[app] wrote secrets.toml to {_secrets_toml}", flush=True)
else:
    print("[app] GOOGLE_CLIENT_ID not set -- OIDC disabled", flush=True)

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="Credit Trading Lab",
    page_icon=":test_tube:",
    layout="wide",
)

# ----------------------------------------------------------------- auth gate
# Dashboard is publicly visible. Auth is only required for approve/reject in Panel H.
_ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "")
try:
    _secrets_configured = (
        "auth" in st.secrets
        and "google" in st.secrets["auth"]
    )
except Exception as _e:
    print(f"[app] st.secrets check failed: {type(_e).__name__}: {_e}", flush=True)
    _secrets_configured = False

if _secrets_configured:
    _user_email: str = st.user.email or "" if st.user.is_logged_in else ""
    _is_authenticated: bool = (
        st.user.is_logged_in and _user_email == _ALLOWED_EMAIL
    )
    # Detect fresh login (logged-out → logged-in transition) and auto-navigate
    # to the Trade Approval tab where the sign-in button lives.
    _was_logged_in = st.session_state.get("_was_logged_in", False)
    if st.user.is_logged_in and not _was_logged_in:
        st.session_state["_was_logged_in"] = True
        import streamlit.components.v1 as _components
        _components.html("""<script>
        setTimeout(function () {
            var tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
            if (tabs.length > 1) tabs[1].click();
        }, 300);
        </script>""", height=0)
    elif not st.user.is_logged_in:
        st.session_state["_was_logged_in"] = False
else:
    # Local dev: no OIDC, treat as authenticated passthrough.
    _user_email = _ALLOWED_EMAIL or "local"
    _is_authenticated = True

# ----------------------------------------------------------------- tabs

tab_attr, tab_ops, tab_research = st.tabs([
    "Strategy Analytics",
    "Trade Approval",
    "Research Archive",
])

with tab_attr:
    from dashboard.views import attribution as attribution_view
    attribution_view.render()

with tab_ops:
    from dashboard.views import operational as operational_view
    operational_view.render(
        user_email=_user_email,
        is_authenticated=_is_authenticated,
        secrets_configured=_secrets_configured,
    )

with tab_research:
    from dashboard.views import research_history as research_history_view
    research_history_view.render()
