# Sprint v8.6 PRD -- Render Deployment and Go-Live

**Status:** planning
**Dependency:** v8.5 closed (local-only). T8 (Google OAuth smoke) carries over.

---

## Overview

v8.6 is the deployment sprint. It lands four execution correctness fixes that the v8.5
supervised smoke session surfaced, closes the v8.4 T8 carryover (feed_attribution), and
ships the credit trading lab to Render as a reproducible three-service stack defined in a
committed `render.yaml` blueprint. The sprint contains no new research hypothesis and no
new signal. The deliverable is a live system: signal fires after US close, the operator
approves or rejects in the dashboard, and execution fills at the open window the next
morning -- all writes landing in Supabase and all four operational dashboard panels
populated from live data.

---

## Economic Hypothesis

None. v8 House Rule 1 applies: this sprint is operational, not a research hypothesis
sprint. There is no edge claim, no Sharpe target, and no signal-quality gate. The only
gates are engineering-correctness gates (E-series) and deployment-verification gates
(D-series).

---

## Falsification Criteria

Pre-registered. The sprint is complete when ALL of the following pass:

| Gate | Criterion |
|------|-----------|
| E1 | Short orders submit as whole-share qty; a fractional test sell_to_open fills without a 422; asymmetry documented in code comments |
| E2 | After execution on a day that creates a fractional residual, `get_all_positions()` shows zero dust (< $1 notional) for any UNIVERSE ticker |
| E3 | `apply_guards` receives live Alpaca account equity as `_nav`; the cap scales with the live balance, not `PAPER_NAV_DEFAULT` |
| E4 | `feed_attribution` appends rows to `attribution.parquet` with schema matching v8.3 tidy frame; dry-run appends zero rows |
| E5 | `render.yaml` declares three services; `git push` to main triggers a Render deploy without manual steps |
| E6 | Evening cron writes the proposed trade to Supabase `decisions` table within 60 min of US close |
| E7 | Mid-morning cron reads the decision, submits orders, marks costs, and calls `feed_attribution` within 20 min of its scheduled time |
| E8 | Dashboard panels I-L (equity curve, positions, P&L log, trade log) populate from Supabase after the first execution run; stub warnings removed |
| E9 | Google OIDC works end-to-end on the Render URL; unauthorized email is blocked (U5 from v8.5 closes) |
| E10 | Re-running either cron on the same calendar day produces zero new orders and a log line "already ran for YYYY-MM-DD" |
| E11 | Both crons skip NYSE closed days and early-close days; the NYSE check uses a library calendar, not a hardcoded list |
| D1 | ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY are absent from the dashboard Render service env vars; verified by inspecting the Render dashboard |

---

## Execution Fixes (pre-deployment, tasks T1-T4)

These fix live-session failures found in v8.5; they are correctness bugs, not
enhancements. They must pass before T5 (render.yaml) is written, because the cron
scripts will call the corrected execution layer.

### Fix A: Shorts via qty (E1)

Alpaca paper rejects fractional notional sell_to_open with HTTP 422
("fractional orders cannot be sold short"). All short-side legs must use integer qty
(whole shares).

Conversion: `qty = int(notional / last_price)` using floor division. If the computed qty
is zero, skip the leg (delta too small for a whole share at that price).

Affected intents: `sell_to_open`, `buy_to_close` (closing a short), `sell_to_close`
(partial short reduction). Long-side legs (`buy_to_open`, `sell_to_close` closing a long)
continue to use notional as before.

The resulting asymmetry is expected and intentional: longs are notional-precise, shorts
are quantized to whole shares. Document this in a code comment -- it is not a bug.

### Fix B: close_position for dust (E2)

Whole-share rounding leaves fractional residuals (e.g., $0.003 SPY from a rounding
remainder). These cannot be closed with a notional order (Alpaca rejects notional < $1).
After the order submission loop, scan all UNIVERSE positions; call `client.close_position(ticker)`
for any position with `abs(notional) < DUST_THRESHOLD` (suggested 1.0 USD). Log the
close. In dry_run mode, log but do not call the API.

### Fix C: live NAV wiring (E3)

`PAPER_NAV_DEFAULT = 100_000.0` is a hard-coded fallback. The cron execution script must
call `client.get_account().equity` at startup and pass the live value as `_nav` into
`apply_guards`. Write the live equity to Supabase `settings` table (key `live_nav`) so
the dashboard can display it. If the Alpaca call fails (non-trading hours, connection
error), fall back to `PAPER_NAV_DEFAULT` and log a warning.

### Fix D: feed_attribution (E4, v8.4 T8 carryover)

`execution/alpaca_paper.py::feed_attribution(fills, close_prices, dividends, date)`
translates FillRecord list into v8.3-compatible tidy rows and appends them to
`data/processed/attribution.parquet`. Schema must match `risk.attribution.build_tidy_attribution`
column list exactly (date, ticker, asset_class, weight, pnl, carry, price_change,
gross_pnl, net_pnl, turnover_cost, borrow_cost, ...). In dry-run, do not append.
After appending, the dashboard attribution panels (A-G) automatically reflect the live
session data on next cache expiry.

---

## Deployment Architecture

### Service map

```
Render project: credit-trading-lab
  |
  +-- Web service: credit-lab-dashboard   (always-on, 0 Alpaca keys)
  |     start: streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0
  |     env: SUPABASE_URL, SUPABASE_SECRET_KEY, ALLOWED_EMAIL,
  |           STREAMLIT_AUTH_REDIRECT_URI
  |
  +-- Cron job: credit-lab-signal         (evening, after US close)
  |     schedule: "30 21 * * 1-5"   (UTC -- 17:30 EDT / 16:30 EST, both after 16:00 ET close)
  |     start: python scripts/run_signal.py
  |     env: SUPABASE_URL, SUPABASE_SECRET_KEY, ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY
  |
  +-- Cron job: credit-lab-execution      (mid-morning, after approval window)
        schedule: "30 14 * * 1-5"   (UTC -- 10:30 EDT / 9:30 EST, both in clean execution window)
        start: python scripts/run_execution.py
        env: SUPABASE_URL, SUPABASE_SECRET_KEY, ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY
```

### DST handling

Render cron is UTC. UTC does not shift for DST; the mapping to ET does.

| Season | UTC offset | Signal cron 21:30 UTC | Execution cron 14:30 UTC |
|--------|------------|----------------------|-------------------------|
| EDT (Mar-Nov) | UTC-4 | 17:30 ET (90 min after close) | 10:30 ET |
| EST (Nov-Mar) | UTC-5 | 16:30 ET (30 min after close) | 09:30 ET |

Both times fall in the correct windows regardless of DST. No runtime DST logic is needed
in the scripts; the UTC schedule is self-correcting.

For early close days (day before Thanksgiving, Christmas Eve, July 3 when applicable):
the NYSE calendar check will detect the early close; the signal cron at 21:30 UTC
(16:30 ET in winter) fires after the early close time (13:00 ET) in all cases, so no
special handling is needed for the signal side. The execution cron fires before the
early-close session ends (13:00 ET), which is intentional -- execution happens in the
normal open window regardless of the early close.

### Shared state via Supabase

Render disk is ephemeral; no state is written to local files during cron runs. Every
piece of state that both services must see lives in Supabase:

| Supabase table | Written by | Read by |
|----------------|-----------|---------|
| `decisions` | signal cron (proposed trade) + dashboard (approve/reject) | execution cron |
| `positions` | execution cron (post-fill) | dashboard panel J |
| `pnl_log` | execution cron (daily P&L row) | dashboard panels I, K |
| `settings` | execution cron (live_nav, auto_approve) | execution cron, dashboard |

`data/processed/attribution.parquet` is the one exception: it lives in the repo and is
updated by `feed_attribution` during execution. The dashboard reads it from the deployed
Render filesystem. Because Render web service restarts on every deploy and cron jobs run
in separate containers, the parquet file must be regenerated from Supabase fills on each
cron run, OR the parquet is treated as a read-only historical artifact and the live
attribution data comes exclusively from Supabase `pnl_log`. The simpler path: keep the
parquet as historical-only; add a `live_attribution` table to Supabase for data from v8.6
forward; the dashboard attribution panels continue reading the parquet for history and
append live rows from `live_attribution`.

### Secret isolation (D1)

The dashboard Render service has no ALPACA_PAPER_API_KEY and no ALPACA_PAPER_SECRET_KEY.
The cron services have no STREAMLIT_AUTH_REDIRECT_URI and no Google OIDC secrets. This
boundary is enforced in `render.yaml` and verified manually after deployment.

### Google OAuth on Render (E9)

Before deploying the dashboard:
1. Add the Render service URL (e.g., `https://credit-lab-dashboard.onrender.com/oauth2callback`)
   as an authorized redirect URI in Google Cloud Console.
2. Set `STREAMLIT_AUTH_REDIRECT_URI` in the Render dashboard env vars to the same URL.
3. Keep `http://localhost:8501/oauth2callback` in the authorized list for local dev.
4. Test with the authorized email and a second Google account (U5).

---

## Signal Definition

No new signal. The evening signal cron runs the existing v8.2 pipeline:
`load_universe_close()` -> `compute_trend(L=120, k_dead_zone=0.5)` ->
`apply_rebalance_control(band_pct=0.20)` -> `shift_to_next_day()`.
Output is the proposed-trade row written to Supabase `decisions` with
`decision='proposed'`. The operator changes it to `'approve'` or `'reject'`
via the dashboard; if no action is taken before the execution cron fires, the
execution cron defaults to the `auto_approve` setting in the `settings` table.

---

## Data

No new data sources. Same sources as v8.2-v8.5:
- UNIVERSE: 8 liquid ETFs (SPY, IEF, TLT, LQD, HYG, GLD, GDX, SLV)
- Daily closes from yfinance, 120-day lookback
- Supabase (project: omnsjnosbaiqkrmnknqw) for mutable state
- Alpaca paper account for fills

---

## NYSE Calendar and Idempotency

Both cron scripts must:

1. **NYSE calendar check:** call `exchange_calendars.get_calendar("XNYS").is_session(today_str)`
   (or `pandas_market_calendars` equivalent). If today is not a trading day, log
   "skipping: NYSE closed on YYYY-MM-DD" and exit 0.

2. **Idempotency:** before doing any work, check Supabase for a `runs` entry
   (table `cron_runs`, columns: `run_date TEXT PK`, `job_name TEXT`, `completed_at TIMESTAMPTZ`).
   If a row exists for today and this job name, log "already ran for YYYY-MM-DD" and exit 0.
   Write the row at the END of a successful run so a partial run does not prevent a retry.

---

## Dashboard Live Panels (T8)

Remove the "TODO v8.6" stub warnings from panels I-L. Wire each to Supabase:

| Panel | Data source | Notes |
|-------|------------|-------|
| I -- Equity curve | `pnl_log` (cumulative sum of daily_pnl) | Show live account equity from `settings.live_nav` as current point |
| J -- Open positions | `positions` table (most recent row per ticker) | Include signed_notional, weight, entry_date |
| K -- Daily P&L and drawdown | `pnl_log` (daily_pnl column, drawdown computed in Python) | |
| L -- Closed trade log | `pnl_log` rows where position closed | Summarise by ticker |

Display `settings.live_nav` (updated by the execution cron) in the sidebar next to the
NAV-relative cap so the operator sees what the guard is anchored to.

---

## Deployment Test Checklist (pre-go-live)

Run these in order. Document pass/fail in `sprints/v8.6/notes.md`.

```
[ ] D1  Render dashboard service has no ALPACA keys in env vars (inspect Render UI)
[ ] D2  Render cron services have no Google OIDC vars in env vars
[ ] D3  Google OAuth end-to-end on Render URL: authorized email enters dashboard
[ ] D4  Google OAuth: unauthorized email sees "Access denied" and cannot proceed (U5)
[ ] D5  Dashboard panels A-G load attribution data correctly (same as local smoke)
[ ] D6  Dashboard panel H shows yesterday's proposed trade with correct as-of date
[ ] D7  Trigger signal cron manually (Render dashboard > manual run): check Supabase decisions table for proposed row
[ ] D8  Approve the proposed trade in the dashboard; verify decision='approve' in Supabase
[ ] D9  Trigger execution cron manually: verify orders submitted (or DRY_RUN log), fills in positions table, pnl_log row written
[ ] D10 Dashboard panels I-L populate after cron run (no more stub warnings)
[ ] D11 Re-trigger execution cron same day: verify "already ran" log, zero new orders
[ ] D12 Observe one complete live scheduled cycle (not manual trigger): signal cron fires at 21:30 UTC, approve in dashboard, execution cron fires at 14:30 UTC next morning
```

Gate D12 is the acceptance gate for the sprint close. The sprint is not closed until a
scheduled (not manually triggered) live cycle completes.

---

## Out of Scope

- New signal research or hypothesis testing
- Live (non-paper) trading
- Additional universe members beyond the 8 ETFs
- Multi-user access (still single ALLOWED_EMAIL)
- P&L attribution for closes and partial fills (attribution uses entry fills only)
- Automated CI/CD beyond Render auto-deploy on push

---

## Dependencies

| Dependency | Version / source |
|-----------|----------------|
| alpaca-py | existing in pyproject.toml |
| supabase | existing in pyproject.toml |
| exchange-calendars or pandas-market-calendars | add to pyproject.toml |
| v8.2 signal pipeline | signals/etf_momentum.py |
| v8.3 attribution engine | risk/attribution.py |
| v8.4 execution layer | execution/alpaca_paper.py |
| v8.5 dashboard | dashboard/app.py, dashboard/views/ |
| Supabase project | omnsjnosbaiqkrmnknqw |
| Alpaca paper account | credentials from env |
| Render account | deploy target |
| Google Cloud Console | OAuth2 client, add Render redirect URI |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Render free tier cold-start kills cron jobs mid-run | Render paid cron tier; or use starter plan with always-on |
| Alpaca paper rate limits during polling | `FILL_POLL_TIMEOUT_SECS = 30` already in place; reduce polling frequency if needed |
| Parquet on Render ephemeral disk lost on restart | Live attribution data goes to Supabase `live_attribution`; historical parquet is read-only artifact in repo |
| DST edge day (clock change Sunday) shifts cron by 1h | Both 30-min margins absorb the shift; NYSE calendar check prevents Sunday execution |
| `exchange_calendars` / `pandas_market_calendars` cold import slow | Import at module level; Render cron has no cold-start budget pressure (not user-facing) |
| Google OAuth token expiry on long-running dashboard session | Streamlit OIDC handles refresh; session cookie_secret in secrets.toml must be set correctly |
