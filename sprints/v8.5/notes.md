# Sprint v8.5 -- Notes

---

## S4 Guardrail (verbatim, required)

"This is a local attribution lab on a carry-dominated book. The historical
P&L shown is from a single rate cycle. The factor residual is confounded
with carry. None of this is an edge claim. Trade decisions made here are
signals to the v8.6 execution layer, not commitments to trade."

---

## T1 -- Supabase setup

### Tables: manual provisioning required

Supabase's REST API (PostgREST) does not support DDL. Tables must be
created manually via the Supabase SQL editor at:

    https://supabase.com/dashboard/project/omnsjnosbaiqkrmnknqw/sql/new

Run the contents of `sprints/v8.5/supabase_schema.sql`. Three tables are
created: `decisions`, `positions`, `pnl_log`.

Status: SQL written, tables need one-time manual provisioning before the
dashboard Approve/Reject buttons will write successfully.

### Connection

`dashboard/supabase_client.py` reads `SUPABASE_URL` and `SUPABASE_SECRET_KEY`
from the environment (.env). The `/rest/v1/` suffix is stripped before
constructing the client (`create_client` needs the project root URL).
`SUPABASE_SECRET_KEY` is the service-role key (bypasses RLS on writes).

Connection test (after tables exist):
```
source .env
python3 -c "
from dashboard.supabase_client import write_decision, fetch_decisions_for_date
ok = write_decision('2099-12-31','APPROVE','SPY',0.05,5000.0,'test@example.com')
print('write ok:', ok)
rows = fetch_decisions_for_date('2099-12-31')
print('rows:', rows)
assert rows and rows[0]['decision'] == 'APPROVE'
print('round-trip PASS')
"
```

### Approve/Reject round-trip (expected once tables exist)

- `write_decision(...)` inserts a row into `decisions` via the service-role key.
- `fetch_decisions_for_date(date)` reads back rows for that date.
- The dashboard Approve/Reject buttons call `write_decision` and rerun the page.
- `st.cache_data.clear()` flushes the 60-second cache so the new decision
  shows immediately.

---

## T2 -- Authentication

### Google OIDC: manual configuration required

The `.streamlit/secrets.toml` must be created by the user (it is gitignored).
Template at `.streamlit/secrets.toml.example`.

Steps:
1. Go to https://console.cloud.google.com -> APIs and Services -> Credentials
2. Create an OAuth 2.0 Client ID (type: Web Application)
3. Add `http://localhost:8501/oauth2callback` as an authorized redirect URI
4. Copy client_id and client_secret into `.streamlit/secrets.toml`
5. Set `cookie_secret` to any random 32-character string
6. Set `ALLOWED_EMAIL` in `.env` to your Google account email

The app gracefully degrades without secrets.toml (shows a warning and allows
local development without auth -- do not deploy in this mode).

### Email gate

`st.user.email` is compared to `ALLOWED_EMAIL` with exact case-sensitive
equality (not `in`, not substring -- exact match). Any other signed-in
account sees "Access denied" and cannot view any panel.

---

## T3-T4 -- Attribution panels

Implemented in `dashboard/views/attribution.py`. Seven panels:

| Panel | Data source | Status |
|-------|------------|--------|
| A: Sleeve P&L | attribution.parquet | done |
| B: Carry vs price change | attribution.parquet | done |
| C: Long vs short | attribution.parquet (reloaded for weight sign) | done |
| D: Directional vs selection | attribution.parquet (daily broadcast cols) | done |
| E: Factor betas + residual | recomputed from risk.attribution module | done |
| F: Cost drag | attribution.parquet (daily broadcast cols) | done |
| G: MCTR by sleeve | attribution_mctr_by_sleeve.parquet | done |

Panel E recomputes the rolling factor regression at dashboard load time
(cached 1 hour). This takes 1-2 seconds. Future optimization: persist the
rolling betas in an additional parquet file.

Finding-3 caveat (U2) is rendered as a permanent `st.info` box immediately
below Panel E: "The residual is confounded with carry accrual. The four
daily-return factors do not span coupon income. This residual is NOT
standalone alpha."

All P&L panels include a single-rate-cycle caption (U7).

---

## T5 -- Proposed trade panel + Approve/Reject

Implemented in `dashboard/views/operational.py`. The proposed trade is
computed via `_get_proposed_trade()` (cached 300 seconds):

```python
target = shift_to_next_day(
    apply_rebalance_control(
        to_position_matrix(compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)),
        rebal_freq=1, band_pct=0.20
    )
).iloc[-1].to_dict()
as_of_date = str(close.index[-1].date())
```

No look-ahead: `close.index[-1]` is yesterday's closing date, and
`shift_to_next_day` has already shifted the weights by one business day
so the resulting targets represent "what to hold entering the next trading
day", not "what to hold entering the day of the close."

The as-of date is shown prominently above the trade table (U3).
Approve/Reject buttons are disabled if Supabase credentials are missing
(graceful degradation rather than crash).

---

## T6 -- Operational stubs (U6)

Panels I-L are stubbed with `st.warning("TODO v8.6: ...")` text. Zero live
Alpaca calls are made from dashboard code:

    grep -r "TradingClient" dashboard/

Returns zero hits (U6 verified). The live feed is connected in v8.6 after
the smoke session.

Panels J and L read from Supabase `positions` and `pnl_log` tables and show
empty dataframes with correct column headers until the v8.6 execution job
writes actual fills.

---

## T7 -- Framing lock

Every P&L panel has `st.caption(FRAMING_CAPTION)` where:
```
FRAMING_CAPTION = (
    "Historical P&L shown for 2007-2026. "
    "This sample contains one secular rate cycle. "
    "Results are not forward-looking."
)
```

Finding-3 caveat (U2) is rendered as an `st.info` box -- not a caption and
not in an expander. It is always visible whenever Panel E is shown.

---

## T8 -- Integration smoke (pending)

Blocked on:
1. Tables provisioned in Supabase dashboard (supabase_schema.sql)
2. Google OIDC configured (.streamlit/secrets.toml)

Once both are configured, run:
    source .env
    streamlit run dashboard/app.py

And manually walk through the U1-U8 checklist from TASKS.md.

---

## Structural deviation from PRD (table names)

The ARGUMENTS for this sprint specified simpler names: `decisions`,
`positions`, `pnl_log`. The PRD had `trade_decisions`, `paper_positions`,
`paper_fills`. The implementation follows the ARGUMENTS (simpler names,
consistent with how table names were abbreviated throughout the sprint's
oral description). Documented here rather than silently diverging.

---

## v8 Standing Constraint (House Rule 8 extension)

No monthly or lower-frequency factor enters any panel in this dashboard.
The factor regression uses daily, exposure-matched factors only (SPY, IEF,
HYG-IEF, GLD). The Finding-3 caveat explains WHY the residual is confounded
with carry -- because daily factors do not span coupon income. If a future
dashboard update is asked to include the Manela factor or any monthly
factor in a daily-frequency panel, treat that as a stale carryover and omit
it per House Rule 8, recording the omission in the relevant sprint's notes.

Sprint v8.5 status: implementation complete; pending Supabase table
provisioning and Google OIDC configuration for full integration test.

---

## T8 manual smoke -- 2026-06-17

Ran `streamlit run dashboard/app.py` locally with `.env` sourced.

| Gate | Result | Notes |
|------|--------|-------|
| U1 -- Attribution panels load | PASS | All 7 panels render, row counts shown |
| U2 -- Finding-3 caveat present | PASS | st.info box visible in Panel E, verbatim text confirmed |
| U3 -- As-of date shown (no look-ahead) | PASS | Panel H shows yesterday's close date |
| U4 -- Approve/Reject writes to Supabase | PASS | Row visible in Supabase dashboard within 2s |
| U5 -- Google OIDC email gate | DEFERRED | Google OAuth config pending; dashboard runs in local dev mode (ALLOWED_EMAIL passthrough). Will be verified in v8.6 before Render deployment. |
| U6 -- Alpaca stubs labelled TODO v8.6 | PASS | Panels I-L show warnings; grep TradingClient dashboard/ = zero hits |
| U7 -- Rate-cycle framing caption on all P&L panels | PASS | Caption present on every panel that shows P&L |
| S4 -- Guardrail text in notes.md | PASS | Present verbatim at top of this file |

T8 status: 6/7 gates pass manually. U5 (Google OAuth) deferred to v8.6.
