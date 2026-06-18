# Sprint v8.6 -- Notes

---

## S4 Guardrail (verbatim, required)

"This is a local attribution lab on a carry-dominated book. The historical
P&L shown is from a single rate cycle. The factor residual is confounded
with carry. None of this is an edge claim. Trade decisions made here are
signals to the v8.6 execution layer, not commitments to trade."

---

## T1 -- Shorts via qty and dust cleanup (2026-06-17)

### Problem (surfaced in v8.5 smoke gate, item 2)

Alpaca paper rejects fractional `sell_to_open` with HTTP 422:
"fractional orders cannot be sold short."

### Fix

`execution/alpaca_paper.py::submit_orders` now checks `position_intent`:
- `sell_to_open` and `buy_to_close` use `qty = int(notional / price)` (floor division).
- `buy_to_open` and `sell_to_close` (closing a long) continue to use `notional`.

The asymmetry is expected and intentional. Longs are notional-precise;
shorts are quantized to whole shares. Documented in the module comment block
(`_SHORT_QTY_INTENTS` constant) and in the `submit_orders` docstring.

If `floor(notional / price) == 0`, the order is skipped and the sentinel
`"SKIPPED_QTY_ZERO"` is written to the submitted_ids list. `build_fill_records`
treats this as `status="SKIPPED_QTY_ZERO"` with zero fill, so it appears in the
reconciliation log (P1 gate maintained).

### Dust cleanup

`close_dust_positions(client, dry_run)` added. After the order submission loop,
it calls `client.get_all_positions()` and calls `client.close_position(ticker)`
for any UNIVERSE position with `abs(market_value) < DUST_THRESHOLD_USD (= $1.0)`.
In dry_run, logs but does not call the API.

### Tests (all pass)

- `test_short_submit_uses_qty`: GLD $3,000 / $200 -> qty=15, verified via `MarketOrderRequest` call args
- `test_long_submit_uses_notional`: SPY $5,000 buy_to_open -> notional used, not qty
- `test_qty_zero_skipped`: $5 notional / $200 price -> qty=0 -> SKIPPED_QTY_ZERO sentinel
- `test_buy_to_close_uses_qty`: HYG $2,000 / $80 -> qty=25
- `test_dust_close_called_in_live_mode`: $0.003 SPY position -> close_position("SPY") called
- `test_dust_not_closed_in_dry_run`: dry_run=True -> no API calls
- `test_non_universe_dust_ignored`: AAPL (not in UNIVERSE) -> close_position not called

---

## T2 -- Live NAV wiring (2026-06-17)

`get_live_nav(client) -> float` added to `execution/alpaca_paper.py`.

Calls `client.get_account().equity`, casts to float, validates > 0. Falls back
to `PAPER_NAV_DEFAULT` on any exception with a warning log. The cron script
(`run_execution.py`) calls this at startup and passes the result as `_nav` to
`apply_guards`. The live equity is also written to `Supabase settings` table
under key `"live_nav"` so the dashboard sidebar can display it.

Tests: `test_get_live_nav_returns_equity`, `test_get_live_nav_fallback_on_error`.

---

## T3 -- feed_attribution (2026-06-17)

`feed_attribution(fills, close_prices, run_date, nav, parquet_path)` implemented in
`execution/alpaca_paper.py`.

For each FILLED FillRecord:
- `signed_notional`: positive for buys (long), negative for sells (short)
- `weight`: `signed_notional / nav`
- `day_ret = (close - fill_price) / fill_price`
- `pnl = signed_notional * day_ret`
- `carry = 0.0` (approximation: dividend accrual not tracked at fill granularity)
- `gross_pnl = pnl`, `net_pnl = gross_pnl - simulated_cost`
- Factor-regression columns (`directional`, `selection`, `beta_explained`, `residual`,
  `r_squared`) are set to NaN -- they require rolling OLS history unavailable from a
  single fill.

Schema matches v8.3 tidy frame exactly (17 columns verified by
`test_feed_attribution_schema_match`).

Tests (all pass):
- `test_feed_attribution_appends_one_row`: 1 FILLED record -> 1 row in parquet
- `test_feed_attribution_dry_run_noop`: empty fills -> parquet not created
- `test_feed_attribution_schema_match`: 17 columns match expected list
- `test_feed_attribution_short_sign`: short fill has negative weight, positive pnl when price falls
- `test_feed_attribution_appends_to_existing`: appends to existing parquet without overwriting

---

## T4 -- render.yaml blueprint (2026-06-17)

`render.yaml` committed at repo root. Three services declared:
- `credit-lab-dashboard`: web service, `streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0`
- `credit-lab-signal`: cron `"30 21 * * 1-5"` UTC, `python scripts/run_signal.py`
- `credit-lab-execution`: cron `"30 14 * * 1-5"` UTC, `python scripts/run_execution.py`

All env var VALUES declared as `sync: false` (set manually in Render UI, never committed).
ALPACA keys absent from the dashboard service entry.

---

## T5 -- NYSE calendar check and idempotency (2026-06-17)

`execution/calendar_utils.py` added.

- `is_trading_day(date_str)`: uses `exchange_calendars.get_calendar("XNYS").is_session()`.
  Fails open (returns True) on lookup error so live days are not silently skipped.
- `check_already_ran(job_name, run_date)`: reads `cron_runs` table via supabase_client.
- `record_run(job_name, run_date)`: writes to `cron_runs` at END of successful run only
  so partial failures retry on the next cron tick.

`exchange-calendars>=4.2` added to `pyproject.toml`.

Calendar tests (7 pass):
- 2026-06-17 (Wednesday): trading day
- 2026-01-02 (Friday after New Year's): trading day
- 2026-06-20 (Saturday): not a trading day
- 2026-06-21 (Sunday): not a trading day
- 2026-12-25 (Christmas): not a trading day
- 2026-01-01 (New Year's Day): not a trading day
- 2025-07-04 (Independence Day, Friday): not a trading day

---

## T6 -- Cron scripts (2026-06-17)

`scripts/run_signal.py` and `scripts/run_execution.py` written.

Both scripts:
1. NYSE calendar check (exit 0 if not a trading day)
2. Idempotency check (exit 0 if already ran for today)
3. Do their work
4. `record_run` at the end (only on success)

`run_signal.py`: loads closes, runs v8.2 signal, writes `decision='proposed'` for as_of_date.

`run_execution.py`: reads decision, applies approve/auto_approve logic, connects to Alpaca,
reads live NAV, computes delta orders, applies guards with live NAV, submits (longs notional /
shorts qty), polls fills, marks costs, closes dust, reconciles, runs feed_attribution,
writes positions + pnl_log + live_attribution to Supabase, writes live_nav to settings.

---

## Test summary (2026-06-17)

52 tests pass across `test_paper_execution.py` and `test_calendar_utils.py`.
Zero failures. Baseline before v8.6 was 31; 21 new tests added.

---

## Deployment checklist

See `sprints/v8.6/DEPLOYMENT_CHECKLIST.md`. Gates A1-F1 require manual execution.
D12 gate (G1-G4 in checklist) is required for sprint close.

---

## Gates status

| Gate | Status | Notes |
|------|--------|-------|
| E1 (shorts via qty) | PASS (code + tests) | Awaits live verification in E3 of deployment checklist |
| E2 (dust close) | PASS (code + tests) | Awaits live verification |
| E3 (live NAV) | PASS (code + tests) | Awaits live verification |
| E4 (feed_attribution) | PASS (code + tests) | Schema match verified against v8.3 parquet |
| E5 (render.yaml) | PASS (committed) | 3 services, no credentials in file |
| E6 (signal cron) | pending deployment | |
| E7 (execution cron) | pending deployment | |
| E8 (dashboard live panels) | pending deployment | |
| E9 (Google OIDC on Render) | pending deployment | |
| E10 (idempotency) | PASS (code) | Live verification in E2 of deployment checklist |
| E11 (NYSE calendar) | PASS (7 tests) | Live verification in F1 of deployment checklist |
| D1 (Alpaca keys absent from dashboard) | pending deployment | Verified via render.yaml structure |
| D12 (one live scheduled cycle) | pending | Sprint close gate |
