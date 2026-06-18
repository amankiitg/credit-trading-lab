"""Build-time script: write .streamlit/secrets.toml from Render env vars.

Run by the dashboard build command BEFORE the Streamlit server starts.
If any required env var is missing the script exits 1 so the build fails
visibly rather than silently deploying with auth disabled.

Required env vars (set in Render dashboard, sync: false):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  STREAMLIT_COOKIE_SECRET
  STREAMLIT_AUTH_REDIRECT_URI   e.g. https://<slug>.onrender.com/oauth2callback
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED = [
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "STREAMLIT_COOKIE_SECRET",
    "STREAMLIT_AUTH_REDIRECT_URI",
]

missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    print(f"[write_render_secrets] ERROR: missing env vars: {missing}", flush=True)
    print("Set them in the Render dashboard -> Environment before deploying.", flush=True)
    sys.exit(1)

out = Path(".streamlit/secrets.toml")
out.parent.mkdir(exist_ok=True)

content = f"""\
[auth]
redirect_uri  = "{os.environ['STREAMLIT_AUTH_REDIRECT_URI']}"
cookie_secret = "{os.environ['STREAMLIT_COOKIE_SECRET']}"

[auth.google]
client_id           = "{os.environ['GOOGLE_CLIENT_ID']}"
client_secret       = "{os.environ['GOOGLE_CLIENT_SECRET']}"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
"""

out.write_text(content)
print(f"[write_render_secrets] wrote {out}", flush=True)
