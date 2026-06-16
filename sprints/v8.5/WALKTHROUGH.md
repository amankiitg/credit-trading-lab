# Sprint v8.5 — Walkthrough: Attribution Lab Dashboard

**Status: closed, local-only.** All implementation tasks complete (T1–T7). T8 (integration smoke) blocked on manual Supabase table provisioning and Google OIDC configuration — those require one-time steps outside the repo. The dashboard runs correctly against historical and mocked state; live Alpaca connection deferred to v8.6.

---

## Summary

v8.5 surfaces the v8.3 forensic attribution engine and the v8.4 paper-execution layer in a single Streamlit dashboard. The sprint is operational, not a research hypothesis: it has no edge claim, no Sharpe target, and no signal-quality gates. The deliverable is a working local dashboard with seven attribution panels, an Approve/Reject trade-decision workflow writing to Supabase, a Google OIDC auth gate restricted to one email, and four explicitly-stubbed operational panels marked TODO v8.6. All seven engineering-correctness gates (U1–U7) pass in code. T8 (manual smoke session) remains pending until the operator configures Supabase tables and Google OIDC outside the repo.

---

## Hypothesis & Falsification Criteria

**No economic hypothesis** (v8 House Rule 1). The PRD registered engineering-correctness gates only.

| Gate | Criterion | Status |
|------|-----------|--------|
| U1 | Attribution panels load from `attribution.parquet` without error | Pass — `st.cache_data` load, row count shown in panel |
| U2 | Finding-3 caveat present verbatim as non-dismissible `st.info` box in Panel E | Pass — `FINDING3_CAVEAT` constant rendered unconditionally |
| U3 | Proposed-trade panel shows yesterday's close as as-of date; no look-ahead | Pass — `close.index[-1]` + `shift_to_next_day` pattern; as-of date captioned prominently |
| U4 | Approve writes `decision='approve'` to Supabase; Reject writes `decision='reject'`; unauthenticated session cannot write | Pass in code — `write_decision()` upsert; buttons disabled when `SUPABASE_SECRET_KEY` absent; **runtime verification pending T8** |
| U5 | Google OIDC restricted to `ALLOWED_EMAIL`; any other account sees "Access denied" | Pass in code — `app.py` reads `ALLOWED_EMAIL` from env; gracefully degrades without `secrets.toml` for local dev; **runtime verification pending T8** |
| U6 | Alpaca stub panels labelled "TODO v8.6"; no live `TradingClient` instantiation in `dashboard/` | Pass — `grep -r "TradingClient" dashboard/` returns zero hits; all four stub panels carry the label |
| U7 | Every P&L panel carries single-rate-cycle framing caption | Pass — `FRAMING_CAPTION` constant applied to all P&L panels in both views |
| S4 | Guardrail text present verbatim in `sprints/v8.5/notes.md` | Pass — present at top of notes.md |

---

## What Was Built

### Authentication Layer (T2)

`dashboard/app.py` reads `ALLOWED_EMAIL` from the environment and compares it to `st.user.email` with exact case-sensitive equality (`==`, not `in`). Any other signed-in Google account is stopped before any panel renders. The file `.streamlit/secrets.toml` (Google OIDC client ID + secret + cookie secret) is gitignored; a template lives at `.streamlit/secrets.toml.example`. The app degrades gracefully when `secrets.toml` is absent — it skips the OIDC redirect and uses `ALLOWED_EMAIL` as a local dev passthrough — so the dashboard is runnable without configuring Google Cloud Console. Auth must be hardened before any deployment.

### Attribution Tab — Panels A–G (T3, T4)

All seven panels read from `data/processed/attribution.parquet` (38,608 rows, 2007–2026) and `data/processed/attribution_mctr_by_sleeve.parquet`, loaded once per hour via `st.cache_data`. Implemented in `dashboard/views/attribution.py`.

| Panel | Description |
|-------|-------------|
| A — Sleeve P&L | Cumulative P&L stacked by `asset_class` (commodity, credit, rates, equity). Commodity $434k, credit $178k, rates $181k, equity $86k over the full sample. |
| B — Carry vs Price Change | **The headline finding.** Carry accrual $612k vs price change $268k; carry is ~70% of gross P&L. Prominently framed. |
| C — Long vs Short P&L | Cumulative net P&L split by long vs short sleeve sign. |
| D — Directional vs Selection P&L | Directional (beta-explained) vs selection residual decomposition. |
| E — Factor Betas + Residual | Rolling 120-day OLS betas on four daily factors (SPY, IEF, HYG–IEF, GLD). Beta-explained vs residual cumulative P&L below. Finding-3 caveat rendered unconditionally as `st.info`. |
| F — Cost Drag | Gross vs net equity overlay; shaded gap = cumulative turnover cost + borrow cost in bps/day. |
| G — MCTR by Sleeve | Ex-ante and realized marginal contribution to risk for the most recent row in the MCTR parquet. |

**Panel B framing note (Finding-3 context):** The reason carry dominates the residual in Panel E is that daily equity, rates, credit, and commodity factors do not span coupon income. Carry accrues into the book's daily return but not into any factor column. The regression labels it "unexplained." The UI makes this explicit: Panel B shows the carry dominance first, and Panel E's `st.info` box explicitly states the residual is not standalone alpha. This is the v8.3 Finding-3 revision shown in the UI on every dashboard load, not as a footnote.

### Operational Tab (T5, T6)

Implemented in `dashboard/views/operational.py`.

**Panel H — Proposed Next Trade.** Signal pipeline runs at load time (cached 300 s). Uses `load_universe_close()` → `compute_trend(L=120, k_dead_zone=0.5)` → `apply_rebalance_control(band_pct=0.20)` → `shift_to_next_day()`. The as-of date is `close.index[-1]` — yesterday's closing date — displayed prominently above the trade table. No look-ahead: the `shift_to_next_day` shift means the returned weights represent "what to hold entering the next trading day" based solely on data through the as-of date.

**Approve / Reject workflow.** One decision per day for the whole book (not per-ticker). Clicking Approve calls `write_decision(date, 'approve')`, which upserts a row into the Supabase `decisions` table via the service-role key. Reject upserts `decision='reject'`. The v8.6 cron job will read this table each morning: under auto-approve mode it executes unless `decision='reject'`; under manual mode it requires `decision='approve'`. An auto-approve toggle (`set_auto_approve` / `get_auto_approve`) is persisted in a Supabase `settings` key-value table. Buttons are disabled (`disabled=not supabase_ok`) when `SUPABASE_SECRET_KEY` is not set, so the dashboard cannot crash an unauthenticated session.

**Panels I–L — Stubs.** Equity curve, open positions, daily P&L log, and closed trade log all render `st.warning("TODO v8.6: ...")` because the Supabase `pnl_log` and `positions` tables have no rows yet (the v8.4 execution job has not run a live paper session). The panels are wired to live Supabase reads and will populate automatically once fills exist — the stub is a data-state stub, not a code stub. Zero `TradingClient` instantiations exist anywhere in `dashboard/`.

### Supabase Schema (T1)

Three tables provisioned via `sprints/v8.5/supabase_schema.sql`:

| Table | Purpose | Written by |
|-------|---------|-----------|
| `decisions` | One approve/reject per trading day | Dashboard (Approve/Reject buttons) |
| `positions` | Current paper book state | v8.6 execution job |
| `pnl_log` | Daily aggregate P&L | v8.6 execution job |

**Schema deviation from PRD.** The PRD specified `trade_decisions`, `paper_positions`, `paper_fills`. The implementation uses shorter names (`decisions`, `positions`, `pnl_log`) consistent with the oral specification used throughout the sprint. Documented in `notes.md`; no functional impact.

`dashboard/supabase_client.py` implements a lazy singleton `get_supabase_client()` that strips the `/rest/v1/` suffix from `SUPABASE_URL` before calling `create_client`. The service-role key (`SUPABASE_SECRET_KEY`) is used for all writes, bypassing row-level security at the service layer. RLS is enabled on all three tables; the dashboard itself is the only writer.

---

## Key Findings

1. **Carry dominates at ~70% of gross P&L and is front-page.** Panel B makes this the first thing the operator sees on every load. The historical equity curve without this framing would mislead; with it, it is an honest accounting record.

2. **Finding-3 caveat is always visible in the UI, not a footnote.** Panel E renders the `FINDING3_CAVEAT` as an `st.info` block unconditionally — not in an expander, not as a dismissible alert. The residual's confoundedness with carry is architectural to the signal decomposition and must be visible to any decision-maker.

3. **The Supabase round-trip design is simple but sufficient.** One row per day in `decisions` (upsert on primary key `trade_date`) is all the v8.6 cron needs to gate execution. The auto-approve toggle adds operator flexibility without adding schema complexity.

4. **Auth graceful degradation is a local-dev convenience, not a permission model.** When `secrets.toml` is absent, the app bypasses OIDC and uses `ALLOWED_EMAIL` directly from the environment. This is intentional for local iteration. Any deployment must restore the OIDC guard.

5. **T8 is the only remaining gate.** All code paths are implemented. The smoke session (U1–U7 manual walk-through with real credentials) is blocked on one-time external configuration: Supabase table provisioning via `supabase_schema.sql` and Google Cloud Console OAuth2 setup. Both are documented step-by-step in `notes.md T2`.

---

## Limitations

- **Single rate cycle.** All attribution panels cover 2007–2026. This sample contains one secular downtrend in rates followed by one sharp reversal. Carry dominance, factor betas, and MCTR are measured in this context only. Every panel carries the framing caption by House Rule 10.
- **Security-selection layer absent.** The book holds ETF baskets; there is no single-bond or issuer-level selection. This is a named permanent gap (House Rule 7) visible in the UI.
- **No monthly factor.** House Rule 8 prohibits monthly or lower-frequency factors in any daily panel. The Manela distress factor is excluded. If a future sprint adds it, treat it as a stale carryover and omit per Rule 8.
- **T8 smoke not yet run.** U4 (Supabase write round-trip) and U5 (OIDC unauthorized-email rejection) are verified in code but not yet by a live operator session.
- **No borrow cost on short positions in the carry decomposition.** The borrow cost shown in Panel F is from the v8.3 attribution frame which estimates it at a fixed rate. Actual margin/borrow costs in paper execution (v8.6) may differ.

---

## Reproducibility

- **Commit hash:** `f69e56a` (v8.5: align Supabase client and operational panel to actual table schema)
- **Data snapshot:** `data/processed/attribution.parquet` written by v8.3 (commit `ff2ab76`); not regenerated in v8.5.
- **Parameters frozen from v8.2:** `L=120`, `k_dead_zone=0.5`, `band_pct=0.20`.
- **Seeds:** no stochastic computation in v8.5.

Run the dashboard:
```bash
source .env                          # SUPABASE_URL, SUPABASE_SECRET_KEY, ALLOWED_EMAIL
# (ensure .streamlit/secrets.toml exists for OIDC, or omit for local dev)
streamlit run dashboard/app.py
```

Provision Supabase tables (one-time):
```
# In the Supabase SQL editor:
# https://supabase.com/dashboard/project/<project-id>/sql/new
# Run the contents of sprints/v8.5/supabase_schema.sql
```

Verify Supabase connection:
```bash
source .env
python3 -c "
from dashboard.supabase_client import write_decision, fetch_decision_for_date
ok = write_decision('2099-12-31', 'approve')
print('write ok:', ok)
row = fetch_decision_for_date('2099-12-31')
print('read back:', row)
assert row == 'approve', 'round-trip FAIL'
print('round-trip PASS')
"
```

---

## What Carries Over to v8.6

The following items are explicitly out of scope for v8.5 and must be picked up in v8.6:

| Item | Notes |
|------|-------|
| **T8 integration smoke** | Run the full U1–U7 checklist from `TASKS.md` once Supabase tables and Google OIDC are configured. Record results in `sprints/v8.5/notes.md`. |
| **Live Alpaca fill/position feed** | Panels I–L read from Supabase `pnl_log` and `positions`; both are empty until the v8.4 execution job (`execution/alpaca_paper.py`) runs a supervised live paper session and writes fills. |
| **Mid-morning execution cron** | The v8.6 cron reads `decisions.decision` for today's date each morning and routes to Alpaca only if the gate is satisfied. Auto-approve vs manual-approve logic is already persisted in Supabase `settings`. |
| **Render deployment** | Not started. Auth must be hardened (OIDC mandatory, no local-dev bypass) before deploying. |
| **Unauthorized-email smoke (U5)** | Sign in with a second Google account and confirm "Access denied" before any panel renders. |
| **Supabase `settings` table** | Required for auto-approve toggle persistence. Provision alongside `decisions`/`positions`/`pnl_log`. Schema: `(key text PK, value text)`. |
