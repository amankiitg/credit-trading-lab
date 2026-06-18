# Sprint v8.6 -- Tasks

**Status:** in progress
Status legend: `[ ]` = not done, `[x]` = done, `[~]` = partially done.

**Dependency order:** T1 -> T2 -> T3 -> T4 (execution fixes, in parallel OK) -> T5 -> T6
-> T7 -> T8 -> T9.

Pre-deployment gate: T1-T4 must pass their acceptance criteria before T5 is written.
The cron scripts (T5) call the corrected execution layer; shipping broken shorts would
trade incorrectly on the live account.

Security gate (enforced at every task): if ALPACA_PAPER_API_KEY, ALPACA_PAPER_SECRET_KEY,
or SUPABASE_SECRET_KEY appears in any committed file, stop and rotate before continuing.

---

- [x] **T1: Shorts via qty -- whole-share conversion and dust close** (E1, E2)

  **Part A -- qty conversion for short legs**

  In `execution/alpaca_paper.py::submit_orders`, add a short-path branch:
  before constructing `MarketOrderRequest`, if `position_intent` is one of
  `sell_to_open`, `buy_to_close`, or `sell_to_close`, compute
  `qty = int(abs(order.notional) / last_price)` (floor division). If `qty == 0`,
  skip the leg and set `guard_status = 'SKIPPED_QTY_ZERO'`; log a warning with
  the ticker and notional. Submit with `qty=qty` instead of `notional=...`.
  Long-side intents (`buy_to_open`, `sell_to_close` closing a long) continue to
  use `notional`. Add a comment stating the asymmetry is expected, not a bug.

  `last_price` for the conversion must come from the close prices already passed
  into the execution pipeline (same source used for NAV computation). Do not make
  an extra Alpaca API call to fetch a quote.

  **Part B -- dust close**

  Add `DUST_THRESHOLD_USD = 1.0` constant.

  After the order submission loop completes, call `client.get_all_positions()`.
  For each position where `abs(float(pos.market_value)) < DUST_THRESHOLD_USD` and
  the ticker is in UNIVERSE: in live mode, call `client.close_position(ticker)`;
  log "closed dust position: ticker, notional=XXX". In dry_run, log only.
  Accumulate tickers closed as `dust_closed: list[str]` and include them in the
  reconciliation output.

  **Tests (add to `tests/test_paper_execution.py`)**
  - `test_short_submits_as_qty`: mock Alpaca; verify `MarketOrderRequest` uses `qty`,
    not `notional`, for a `sell_to_open` order. Verify `qty = floor(notional/price)`.
  - `test_qty_zero_skipped`: notional=$5, price=$200 -> qty=0 -> SKIPPED_QTY_ZERO.
  - `test_long_still_uses_notional`: `buy_to_open` uses `notional`, not `qty`.
  - `test_dust_close_called`: mock `get_all_positions` returning a $0.003 SPY
    position; verify `close_position("SPY")` is called in live mode and NOT called
    in dry_run mode.

  Acceptance: all four tests pass; 31 existing tests remain green.
  Files: `execution/alpaca_paper.py`, `tests/test_paper_execution.py`
  Validation: fails if a 422-class order reaches Alpaca for a short leg; fails if
  existing tests regress.

---

- [x] **T2: Live NAV wiring** (E3)

  In the execution cron script (`scripts/run_execution.py`, created in T5, but
  define the helper here first):

  Add `execution/alpaca_paper.py::get_live_nav(client) -> float`:
  calls `client.get_account()`, reads `account.equity`, converts to float, validates
  > 0. Returns the value. On any exception, logs a warning and returns
  `PAPER_NAV_DEFAULT`. This function is called once at the start of the execution
  run and the result is passed as `_nav` to `apply_guards`.

  Also write the live equity to Supabase `settings` table (key `"live_nav"`, value
  str of float) using `dashboard.supabase_client.set_setting("live_nav", str(equity))`.
  If Supabase write fails, log and continue (the guard still uses the live value).

  In `dashboard/views/operational.py`, read `get_setting("live_nav")` and display
  it in the sidebar as "Live NAV: $XXX,XXX (updated by last execution run)".

  Add `get_setting` / `set_setting` to `dashboard/supabase_client.py` (generic
  key-value ops against the `settings` table) if not already present.

  **Tests**
  - `test_get_live_nav_returns_equity`: mock `get_account()` returning equity=120_000;
    assert returns 120_000.0.
  - `test_get_live_nav_fallback_on_error`: mock raises; assert returns PAPER_NAV_DEFAULT.

  Acceptance: both tests pass; guard log shows the live NAV value (not 100_000 hardcode)
  when run against the paper account.
  Files: `execution/alpaca_paper.py`, `dashboard/supabase_client.py`,
         `dashboard/views/operational.py`, `tests/test_paper_execution.py`
  Validation: fails if `apply_guards` is called with PAPER_NAV_DEFAULT when a live
  account is reachable.

---

- [x] **T3: feed_attribution** (E4, v8.4 T8 carryover)

  Implement `execution/alpaca_paper.py::feed_attribution(fills, close_prices,
  dividends, date) -> int` (returns row count appended).

  For each FillRecord in `fills` where `status == "filled"`:
  - Compute `pnl = filled_notional * (close_prices[ticker] / entry_price - 1)`.
    Use `fill_price` from FillRecord as `entry_price` for the day-of fill.
  - Carry accrual for the day: `dividends.get(ticker, 0.0)` as proxy
    (daily dividend per share * filled_qty).
  - price_change = pnl - carry.
  - turnover_cost and borrow_cost from the FillRecord (already set by `mark_costs`).
  - gross_pnl = pnl; net_pnl = pnl - turnover_cost - borrow_cost.
  - asset_class: look up from a `TICKER_ASSET_CLASS` dict in `signals/etf_universe.py`
    (add this dict if not present: SPY->equity, IEF->rates, TLT->rates, LQD->credit,
    HYG->credit, GLD->commodity, GDX->commodity, SLV->commodity).
  - weight: `abs(filled_notional) / _nav` (use live nav passed in or PAPER_NAV_DEFAULT).
  - Build a DataFrame row matching the v8.3 tidy schema exactly.

  Load existing `data/processed/attribution.parquet`, append the new rows,
  and write back. If the file does not exist, create it. Assert schema columns
  match the v8.3 column list before writing. In dry_run (zero fills list), return 0
  without touching the file.

  **Tests**
  - `test_feed_attribution_appends_one_row`: synthetic FillRecord; assert parquet row
    count increases by 1; assert all schema columns present.
  - `test_feed_attribution_dry_run_noop`: empty fills list; assert parquet unchanged.
  - `test_feed_attribution_schema_match`: new rows have all and only the v8.3 columns.

  Acceptance: all three tests pass; schema matches v8.3 column list verified in the test.
  Files: `execution/alpaca_paper.py`, `signals/etf_universe.py`,
         `tests/test_paper_execution.py`
  Validation: fails if dry-run appends rows; fails if any schema column is missing.

---

- [x] **T4: render.yaml blueprint** (E5)

  Write `render.yaml` at the project root. Declare three services:

  ```yaml
  services:
    - type: web
      name: credit-lab-dashboard
      env: python
      buildCommand: pip install -e .
      startCommand: streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0
      envVars:
        - key: SUPABASE_URL
          sync: false
        - key: SUPABASE_SECRET_KEY
          sync: false
        - key: ALLOWED_EMAIL
          sync: false
        - key: STREAMLIT_AUTH_REDIRECT_URI
          sync: false

    - type: cron
      name: credit-lab-signal
      env: python
      buildCommand: pip install -e .
      schedule: "30 21 * * 1-5"
      startCommand: python scripts/run_signal.py
      envVars:
        - key: SUPABASE_URL
          sync: false
        - key: SUPABASE_SECRET_KEY
          sync: false
        - key: ALPACA_PAPER_API_KEY
          sync: false
        - key: ALPACA_PAPER_SECRET_KEY
          sync: false

    - type: cron
      name: credit-lab-execution
      env: python
      buildCommand: pip install -e .
      schedule: "30 14 * * 1-5"
      startCommand: python scripts/run_execution.py
      envVars:
        - key: SUPABASE_URL
          sync: false
        - key: SUPABASE_SECRET_KEY
          sync: false
        - key: ALPACA_PAPER_API_KEY
          sync: false
        - key: ALPACA_PAPER_SECRET_KEY
          sync: false
  ```

  The `sync: false` directive means secrets are set manually in the Render dashboard,
  not from a `.env` file in the repo. Verify `render.yaml` does not contain any
  credential values.

  Write `scripts/run_signal.py`: NYSE calendar check, idempotency check (deferred to T5,
  write the stub with a TODO comment for now), then signal pipeline -> write proposed
  trade to Supabase `decisions`.

  Write `scripts/run_execution.py`: NYSE calendar check, idempotency check stub,
  then read decision from Supabase, call execution pipeline if approved/none, write
  fills to positions table, write P&L row to pnl_log.

  Acceptance: `render.yaml` passes `render validate` or equivalent lint; no credentials
  in the file; `git grep "ALPACA_PAPER_API_KEY" render.yaml` returns zero non-comment hits.
  Files: `render.yaml`, `scripts/run_signal.py`, `scripts/run_execution.py`
  Validation: fails if any ALPACA key appears in `render.yaml` as a value (not a key name).

---

- [x] **T5: NYSE calendar check and idempotency** (E10, E11)

  Add `execution/calendar_utils.py` with two functions:

  `is_trading_day(date_str: str) -> bool`: uses `exchange_calendars.get_calendar("XNYS")`
  (add `exchange-calendars` to `pyproject.toml`). Returns True if the date is a regular
  or early-close session.

  `check_already_ran(job_name: str, run_date: str, supabase_client) -> bool`:
  queries Supabase `cron_runs` table for a row matching `(job_name, run_date)`. Returns
  True if found.

  `record_run(job_name: str, run_date: str, supabase_client) -> None`:
  inserts a row into `cron_runs` (columns: `run_date TEXT`, `job_name TEXT`,
  `completed_at TIMESTAMPTZ`). Called at the END of a successful run.

  Provision the `cron_runs` table in Supabase:
  ```sql
  CREATE TABLE cron_runs (
    run_date TEXT NOT NULL,
    job_name TEXT NOT NULL,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_date, job_name)
  );
  ```
  Add this to `sprints/v8.5/supabase_schema.sql` (or a new `sprints/v8.6/supabase_schema.sql`).

  Wire both `scripts/run_signal.py` and `scripts/run_execution.py` to call
  `is_trading_day` and `check_already_ran` at the top, replacing the TODO stubs from T4.

  **Tests** (add to a new `tests/test_calendar_utils.py`)
  - `test_trading_day_weekday`: a known Wednesday is a trading day.
  - `test_non_trading_day_weekend`: a Saturday is not.
  - `test_non_trading_day_holiday`: Christmas 2024 is not.

  Acceptance: three tests pass; both cron scripts log "skipping: NYSE closed" on a
  test weekend date; both scripts log "already ran" on a second invocation.
  Files: `execution/calendar_utils.py`, `pyproject.toml`, `scripts/run_signal.py`,
         `scripts/run_execution.py`, `tests/test_calendar_utils.py`,
         `sprints/v8.6/supabase_schema.sql`
  Validation: fails if a script runs orders on a NYSE-closed date; fails if a second
  same-day run submits any order.

---

- [ ] **T6: Secret isolation and Google OIDC on Render** (D1, E9)

  **Secret isolation verification**

  After deploying to Render:
  - Inspect the Render dashboard -> credit-lab-dashboard service -> Environment.
    Confirm ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY are absent.
  - Document the verification result in `sprints/v8.6/notes.md`.

  **Google OIDC Render redirect URI**

  1. In Google Cloud Console, add the Render service URL to authorized redirect URIs:
     `https://<render-service-name>.onrender.com/oauth2callback`
  2. In Render dashboard -> credit-lab-dashboard -> Environment, set
     `STREAMLIT_AUTH_REDIRECT_URI = https://<render-service-name>.onrender.com/oauth2callback`
  3. Update `.streamlit/secrets.toml.example` to include a `redirect_uri` placeholder
     (gitignored file gets the real value; the example shows the pattern).
  4. In `dashboard/app.py`, pass `redirect_uri=os.environ.get("STREAMLIT_AUTH_REDIRECT_URI")`
     to `st.login("google")` if the parameter is supported, or set it via `secrets.toml`.

  **Acceptance:** smoke test on the Render URL:
  - Authorized email (amank.iitg@gmail.com) completes Google OIDC and lands on the
    dashboard (U5a).
  - A second Google account sees "Access denied" and cannot view any panel (U5b).
  - Document both results in `sprints/v8.6/notes.md`.

  Files: `.streamlit/secrets.toml.example`, `dashboard/app.py`, `sprints/v8.6/notes.md`
  Validation: fails if U5b passes (unauthorized user can see panels); fails if the
  authorized user is stopped at the OIDC redirect.

---

- [ ] **T7: Dashboard live panels I-L** (E8)

  Remove the `st.warning("TODO v8.6: ...")` stubs from panels I-L in
  `dashboard/views/operational.py`.

  Wire each panel to Supabase:

  **Panel I -- Equity curve**
  `SELECT run_date, cumulative_nav FROM pnl_log ORDER BY run_date ASC`
  Plot as a line chart. Overlay a horizontal reference at `settings.live_nav`.
  If `pnl_log` is empty (no execution runs yet), show `st.info("No execution runs yet.")`.

  **Panel J -- Open positions**
  `SELECT ticker, signed_notional, entry_date, avg_entry_price FROM positions
  WHERE closed_at IS NULL`
  Add a `weight` column: `signed_notional / live_nav`. Show as a dataframe.
  If empty, show `st.info("No open positions.")`.

  **Panel K -- Daily P&L and drawdown**
  `SELECT run_date, daily_pnl FROM pnl_log ORDER BY run_date ASC`
  Compute cumulative P&L and drawdown in Python (no SQL aggregation).
  Plot daily P&L bars and drawdown line on a dual-axis chart.

  **Panel L -- Closed trade log**
  `SELECT * FROM pnl_log WHERE closed_ticker IS NOT NULL ORDER BY run_date DESC LIMIT 50`
  Show as dataframe. (Or join `positions` on close events -- adapt to actual schema.)

  Add `live_nav` to the sidebar: read `get_setting("live_nav")` and display as
  "Book NAV: $XXX,XXX". Cache with TTL=60s.

  **Acceptance:** after one manual execution cron run (triggered via Render dashboard),
  panels I-L show data rows without stub warnings. `live_nav` appears in the sidebar.
  Files: `dashboard/views/operational.py`, `dashboard/supabase_client.py`
  Validation: fails if any "TODO v8.6" string remains in the panel rendering path;
  fails if the panel crashes on an empty Supabase table (must handle empty gracefully).

---

- [x] **T8: Supabase schema provisioning** (supporting T5, T7)

  Write `sprints/v8.6/supabase_schema.sql` with the new tables needed for v8.6:

  ```sql
  -- idempotency log for cron jobs
  CREATE TABLE IF NOT EXISTS cron_runs (
    run_date  TEXT NOT NULL,
    job_name  TEXT NOT NULL,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_date, job_name)
  );

  -- live P&L rows written by execution cron
  CREATE TABLE IF NOT EXISTS pnl_log (
    run_date        TEXT PRIMARY KEY,
    daily_pnl       NUMERIC,
    cumulative_nav  NUMERIC,
    gross_pnl       NUMERIC,
    net_pnl         NUMERIC,
    turnover_cost   NUMERIC,
    borrow_cost     NUMERIC
  );

  -- live position snapshot written after each execution run
  CREATE TABLE IF NOT EXISTS positions (
    ticker          TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    signed_notional NUMERIC,
    avg_entry_price NUMERIC,
    entry_date      TEXT,
    closed_at       TIMESTAMPTZ,
    PRIMARY KEY (ticker, trade_date)
  );
  ```

  Run this SQL in the Supabase SQL editor. Document the run in `sprints/v8.6/notes.md`.

  Also add `live_attribution` table for v8.6 fill-based attribution data (to avoid
  overwriting the historical parquet on ephemeral Render disk):

  ```sql
  CREATE TABLE IF NOT EXISTS live_attribution (
    run_date    TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    asset_class TEXT,
    weight      NUMERIC,
    pnl         NUMERIC,
    carry       NUMERIC,
    price_change NUMERIC,
    gross_pnl   NUMERIC,
    net_pnl     NUMERIC,
    turnover_cost NUMERIC,
    borrow_cost  NUMERIC,
    PRIMARY KEY (run_date, ticker)
  );
  ```

  Acceptance: each table visible in Supabase dashboard; connection test:
  `python -c "from dashboard.supabase_client import get_client; c = get_client(); print(c.table('cron_runs').select('*').limit(1).execute())"` returns without error.
  Files: `sprints/v8.6/supabase_schema.sql`, `sprints/v8.6/notes.md`
  Validation: fails if any table is missing when the cron scripts run.

---

- [ ] **T9: Deployment smoke and live cycle observation** (E5-E11, D1-D12)

  Run the full deployment test checklist from the PRD. Document each gate result in
  `sprints/v8.6/notes.md` with timestamp, pass/fail, and any notes.

  The sprint is not closed until gate D12 passes: one complete live scheduled cycle
  (NOT a manual trigger) where:
  1. The evening signal cron fires at 21:30 UTC and writes a proposed trade to Supabase.
  2. The operator approves (or auto-approve triggers) in the dashboard.
  3. The mid-morning execution cron fires at 14:30 UTC and fills orders, writes positions
     and P&L to Supabase.
  4. Dashboard panels I-L show the fill data without errors.

  Record the order IDs from the live cycle in notes.md.

  Acceptance: all D1-D12 gates documented as passing; at least one scheduled cycle
  end-to-end without manual trigger.
  Files: `sprints/v8.6/notes.md`
  Validation: sprint cannot be closed with D12 marked as "manual trigger only".
