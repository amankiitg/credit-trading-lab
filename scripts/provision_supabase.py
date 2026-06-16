"""Create the v8.5 Supabase tables via the Supabase SQL execution API.

Run once before using the dashboard:
    source .env
    python scripts/provision_supabase.py

Uses the Supabase Management REST API at https://api.supabase.com with a
personal access token. The personal access token is NOT the project service
role key -- it comes from supabase.com -> Account -> Access Tokens.

If you prefer, run sprints/v8.5/supabase_schema.sql directly in the
Supabase dashboard SQL editor instead.
"""

from __future__ import annotations

import os
import sys

# Read from .env if available
try:
    for line in open(".env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

if not SUPABASE_URL or not SECRET_KEY:
    sys.exit("Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env first.")

# Extract project ref from URL
project_url = SUPABASE_URL.removesuffix("/rest/v1/")
project_ref = project_url.removeprefix("https://").split(".")[0]

SQL = open("sprints/v8.5/supabase_schema.sql").read()

# Approach 1: try the Management API (requires SUPABASE_ACCESS_TOKEN env var,
# which is a personal access token from supabase.com -> Account -> Access Tokens,
# NOT the project service role key)
access_token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
if access_token:
    import urllib.request, json
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    payload = json.dumps({"query": SQL}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print("Tables created via Management API:", resp.read().decode()[:200])
        sys.exit(0)
    except Exception as exc:
        print(f"Management API approach failed: {exc}")
        print("Trying the SQL editor approach below, or run the SQL manually.")
        print()

# Approach 2: print instructions for manual creation
print("=" * 60)
print("Manual table creation required")
print("=" * 60)
print()
print("Run the following SQL in the Supabase SQL editor:")
print(f"  https://supabase.com/dashboard/project/{project_ref}/sql/new")
print()
print("File: sprints/v8.5/supabase_schema.sql")
print()
print("Or copy-paste the SQL below:")
print()
print(SQL)
print()
print("After creating the tables, re-run:")
print("  python scripts/provision_supabase.py")
print("to verify the connection works.")
