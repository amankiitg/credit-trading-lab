# Sprint v8.5 -- Live Attribution Lab (Programme v8, Sprint 5)

## Context: What This Sprint Is

v8.1-v8.4 built the signal, the attribution engine, and the paper
execution layer. v8.5 surfaces all of that in a live Streamlit dashboard,
locally only. No Render deployment (v8.6). No live Alpaca connection in
v8.5 (the Alpaca position and fill feed is explicitly stubbed with a clear
TODO). The smoke session and the mid-morning execution job come after.

This sprint extends `dashboard/app.py` from Sprint 4's Today View into a
full live attribution lab. The framing matters: this is not a "trend
paper-trade dashboard." It is a carry-dominated book attribution lab with
an embedded trade-approval workflow. The v8.3 finding is the headline --
70% of gross P&L is carry, GLD is 49% of gross P&L, and the factor
regression residual is confounded with carry rather than being standalone
alpha. The dashboard makes that finding visible every time it loads, as
the primary surface, not a footnote.

## v8 House Rules (carried, with two additions for v8.5)

1. No edge claim. The dashboard does not claim the book makes money.
2. No look-ahead. The "proposed next trade" panel uses only yesterday's
   close; the date watermark is always visible.
3. Parameters frozen from v8.2 (`L=120`, `k_dead_zone=0.5`,
   `band_pct=0.20`). Nothing in this sprint changes them.
4. Universe fixed from v8.1. No ticker changes.
5. Mechanical and reproducible.
6. Single-regime caveat. All historical P&L panels include a caption
   noting that the 2007-2026 sample contains one secular rate cycle.
7. Security-selection layer absent (named gap, visible in UI).
8. No monthly factor in any daily computation.
9. **State in Supabase, not in files.** No position data, decision
   records, or P&L log is stored in the repo. `.env` and
   `.streamlit/secrets.toml` are gitignored. If either appears in a
   committed changeset, that is a hard stop requiring key rotation before
   proceeding.
10. **Framing lock.** Every panel that shows P&L, returns, or factor
    decomposition must include a visible, non-dismissible sub-caption
    identifying the book as carry-dominated and the historical context as
    a single rate cycle. The residual panel must explicitly label the
    residual as "confounded with carry, not standalone alpha" (the
    finding-3 revision from `sprints/v8.3/notes.md`).

---

## Economic Hypothesis

None (House Rule 1). The purpose of v8.5 is operational: make the
attribution visible, give the operator an approval workflow for each
proposed trade, and start accumulating a Supabase-backed position log that
the v8.6 execution job will consume. Paper P&L, once it accumulates, will
be reported as-is, not interpreted as evidence for or against edge.

---

## Falsification Criteria

Engineering-correctness gates, not signal-quality gates. New `U`-series
(UI/UX correctness) continuing the v8 gate convention.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|----------------|------------------|
| U1 | Attribution panels load from `data/processed/attribution.parquet` without error; date range and shape shown in UI | panel renders with correct row count | bug; fix |
| U2 | Finding-3 caveat is visible in the factor residual panel: "residual is confounded with carry, not standalone alpha" | text present verbatim as a non-dismissible caption | missing; add before proceeding |
| U3 | Propose-next-trade panel shows the correct signal-implied target for today using only yesterday's close, with the as-of date watermarked | date shown matches `close.index[-1]`, no look-ahead | bug; fix |
| U4 | Approve writes a row to the `trade_decisions` Supabase table; Reject writes a row with `decision='REJECT'`; neither writes if the user is not authenticated | row appears in Supabase within 2s; unauthenticated user sees only a login prompt | bug; fix |
| U5 | Google OIDC login is restricted to the single authorized email from `ALLOWED_EMAIL` env var; any other logged-in user sees an "access denied" page, not the dashboard | tested by inspecting `st.user.email` in the session state | bug; fix |
| U6 | Alpaca stub is visibly labelled "TODO v8.6" in every panel that reads live positions or fills; no live Alpaca call is made in v8.5 | grep for live Alpaca client instantiation in dashboard code -> zero hits outside `execution/alpaca_paper.py` | bug; fix |
| U7 | Framing lock: every P&L panel has a sub-caption identifying the book as carry-dominated and the sample as a single rate cycle | text present and non-dismissible | missing; add |
| S4 | Guardrail text, not a test | must appear verbatim in `sprints/v8.5/notes.md`: **"This is a local attribution lab on a carry-dominated book. The historical P&L shown is from a single rate cycle. The factor residual is confounded with carry. None of this is an edge claim. Trade decisions made here are signals to the v8.6 execution layer, not commitments to trade."** | N/A |

---

## Supabase Table Schemas

All tables live in the Supabase project at `SUPABASE_URL`. Row-level
security should be enabled with a policy that only the service-role key
(used by the dashboard and the v8.6 job) can read or write.

### `trade_decisions`
```sql
create table trade_decisions (
  id            bigserial primary key,
  created_at    timestamptz default now() not null,
  signal_date   date        not null,
  decision      text        not null check (decision in ('APPROVE','REJECT')),
  ticker        text        not null,
  proposed_target_weight  float8 not null,
  proposed_delta_notional float8 not null,
  approved_by   text        not null,  -- email of the approver
  notes         text
);
```
Written by: Approve / Reject buttons in the dashboard.
Read by: the v8.6 mid-morning execution job (to decide whether to submit).

### `paper_positions`
```sql
create table paper_positions (
  id             bigserial primary key,
  updated_at     timestamptz default now() not null,
  ticker         text        not null unique,
  signed_notional float8     not null,  -- positive long, negative short
  weight         float8      not null,
  entry_date     date,
  avg_entry_price float8
);
```
Written by: v8.6 execution job (on fill confirmation).
Read by: dashboard "Open Positions" panel. Stubbed empty in v8.5.

### `paper_fills`
```sql
create table paper_fills (
  id                bigserial primary key,
  fill_at           timestamptz default now() not null,
  signal_date       date        not null,
  ticker            text        not null,
  side              text        not null,
  position_intent   text        not null,
  filled_notional   float8      not null,
  fill_price        float8      not null,
  simulated_cost    float8      not null,
  gross_pnl         float8,
  net_pnl           float8,
  status            text        not null
);
```
Written by: v8.6 execution job (after fill confirmation).
Read by: dashboard "Closed Trade Log" and cumulative P&L panels. Stubbed empty in v8.5.

---

## Dashboard Architecture

### Authentication layer

```
st.login("google")           # triggers Google OIDC redirect
user = st.user               # st.user.email, st.user.name after login
allowed = os.environ["ALLOWED_EMAIL"]
if user.email != allowed:
    st.error("Access denied."); st.stop()
```

Configured via `.streamlit/secrets.toml` (gitignored):
```toml
[auth]
redirect_uri    = "http://localhost:8501/oauth2callback"
cookie_secret   = "<random 32-char string>"

[auth.google]
client_id     = "<from Google Cloud Console>"
client_secret = "<from Google Cloud Console>"
```

The `ALLOWED_EMAIL` env var lives in `.env` (gitignored, never committed).

### Page layout (single page, tabbed sections)

```
PAGE TITLE: "Attribution Lab -- carry-dominated book, single rate cycle"

TAB 1: Attribution
  Panel A: Sleeve P&L (bar chart, cumulative; GLD and credit dominance highlighted)
  Panel B: Carry vs Price Change (bar chart; "70% carry" framing prominent)
  Panel C: Long vs Short P&L (stacked area)
  Panel D: Directional vs Selection P&L (stacked area)
  Panel E: Factor Betas over time (line chart, 4 factors)
             + Beta-explained vs Residual P&L
             + Finding-3 caveat (U2, U10)
  Panel F: Cost Drag (gross vs net equity, cost breakdown in bps)
  Panel G: Marginal Risk Contribution by sleeve (MCTR, ex-ante vs realized)

TAB 2: Operational
  Panel H: Proposed Next Trade (target weights, delta notionals, as-of date; Approve/Reject per ticker)
  Panel I: Net Equity Curve (from paper_fills; stubbed TODO v8.6)
  Panel J: Open Positions (from paper_positions; stubbed TODO v8.6)
  Panel K: Daily P&L and Drawdown (from paper_fills; stubbed TODO v8.6)
  Panel L: Closed Trade Log (from paper_fills; stubbed TODO v8.6)
```

### Data sources

| panel | source | notes |
|-------|--------|-------|
| A-G | `data/processed/attribution.parquet`, `data/processed/attribution_mctr_by_sleeve.parquet` | read-only, local files |
| H (proposed trade) | signal pipeline computed at dashboard load | uses `close.index[-1]` as the as-of date; no look-ahead |
| H (Approve/Reject) | writes to Supabase `trade_decisions` | requires authenticated session |
| I, J, K, L | Supabase `paper_fills`, `paper_positions` | stubbed empty in v8.5 with TODO label |

### Supabase connection module

New module `dashboard/supabase_client.py`:
```python
import os
from supabase import create_client

_client = None

def get_client():
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"].removesuffix("/rest/v1/")
        key = os.environ["SUPABASE_SECRET_KEY"]
        _client = create_client(url, key)
    return _client
```
`SUPABASE_URL` and `SUPABASE_SECRET_KEY` from `.env` (already set). The
`removesuffix` handles the fact that `.env` stores the REST endpoint
(`/rest/v1/`) while the Python client needs the project root URL.

---

## Data

| source | contents | status |
|--------|----------|--------|
| `data/processed/attribution.parquet` | 38,608 rows, 17 cols, 2007-2026 | existing (v8.3) |
| `data/processed/attribution_mctr_by_sleeve.parquet` | MCTR by sleeve, ex-ante and realized | existing (v8.3) |
| `data/raw/{ticker}.parquet` | adj_close for signal recomputation at load time | existing (v8.1) |
| Supabase (`trade_decisions`, `paper_positions`, `paper_fills`) | shared state | new tables, provisioned in T1 |
| Google OIDC | authentication | new; `.streamlit/secrets.toml` gitignored |

---

## Success Metrics

No Sharpe, IC, or predictive metric (House Rule 1). Metrics are the
`U1-U7`/`S4` gates above, plus these operational checks (not gated):
- Dashboard loads in under 3s on a cold start with attribution.parquet.
- Approve button round-trip to Supabase and back under 2s.
- Signal pipeline (compute today's proposed trade) runs in under 5s.

---

## Research Architecture

```
.env (SUPABASE_URL, SUPABASE_SECRET_KEY, ALLOWED_EMAIL)
.streamlit/secrets.toml (Google OIDC, cookie_secret)  -- gitignored
      |
[T1] Supabase tables provisioned + dashboard/supabase_client.py
      |
[T2] Authentication: st.login + email gate + .streamlit/secrets.toml pattern
      |
[T3] Attribution Tab: Panels A-D (sleeve P&L, carry, L/S, directional/selection)
      |
[T4] Attribution Tab: Panels E-G (factor betas, beta-explained/residual, cost, MCTR)
      |
[T5] Operational Tab: Panel H (proposed next trade + Approve/Reject + Supabase write)
      |
[T6] Operational Tab: Panels I-L (equity curve, positions, P&L, drawdown, log; stubbed)
      |
[T7] Framing lock and guardrail text (U2, U7, S4)
      |
[T8] Integration: streamlit run, smoke through all panels with auth, verify U1-U8
```

---

## Risks and Biases

- **`.streamlit/secrets.toml` must be gitignored before the first commit
  that touches the dashboard.** The Google OIDC client secret and the
  cookie secret are high-value credentials. Add the path to `.gitignore`
  in T2 before creating the file.
- **Email gate bypassed by a typo.** `ALLOWED_EMAIL` is compared
  case-sensitively to `st.user.email`. Google accounts use lowercase email
  addresses; uppercase comparison should not be needed, but the check must
  be explicit (`==`, not `in`) to prevent substring matches.
- **Supabase `SECRET_KEY` vs `PUBLISHABLE_KEY`.** The dashboard writes
  to Supabase (Approve/Reject), so it must use the `SECRET_KEY`
  (service-role key), not the publishable/anon key. The wrong key would
  silently fail row-level security and produce empty writes. Checked in T1.
- **Attribution P&L panels show the single-rate-cycle sample.** House Rule
  10 (framing lock, U7) exists specifically because showing an equity curve
  without the caveat implies forward-looking validity. The caption is
  non-negotiable.
- **Proposed-trade look-ahead.** The signal must use `close.index[-1]`
  (yesterday's close) as the as-of date. Using `today` or
  `datetime.now()` would introduce look-ahead. U3 gate verifies this.

---

## Out of Scope

- Render deployment (v8.6)
- Live Alpaca connection (v8.6, after the smoke session)
- Mid-morning execution job (v8.6)
- Mobile layout or responsive design
- Any performance claim based on the attribution panels
- Changing signal parameters, universe, or attribution logic

---

## Dependencies

- `dashboard/app.py` -- Sprint 4 Today View, extended in place
- `data/processed/attribution.parquet`, `data/processed/attribution_mctr_by_sleeve.parquet` -- v8.3
- `signals/trend_signal.py`, `signals/etf_universe.py` -- v8.2, for Panel H
- `execution/costs.py` -- v8.4, for cost-marked fill display
- `supabase` Python library -- not yet installed; added to `pyproject.toml` in T1
- `alpaca-py` -- already installed (v8.4); NOT used in v8.5, only imported in `execution/alpaca_paper.py`
- Streamlit >= 1.37 with `st.login` and `st.user` (confirmed available in 1.50.0)
- Google Cloud Console OAuth2 credentials (provisioned outside the repo)
- `.env` with `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ALLOWED_EMAIL`
- `.streamlit/secrets.toml` with Google OIDC config (gitignored, provisioned in T2)
