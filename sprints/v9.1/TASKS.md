# Sprint v9.1 -- Tasks

**Status:** complete
Status legend: `[ ]` = not done, `[x]` = done, `[~]` = partially done.

**Dependency order:** T1 -> T2 -> (T3, T4, T5 in any order) -> T6 gate decision ->
T7 -> T8 -> (T9, T10). T8-T10 (attribution panel) are independent of the T6 gate
outcome and can proceed in parallel after T1.

**Gate rule:** the adoption decision (traded vs advisory mode) is made ONCE, at T6,
against the pre-registered F1-F5 gates in the PRD, at default parameters
(k1=1.5, k2=2.5, h=0.5, m_reduced=0.5). The T5 grid never picks parameters.

---

- [x] **T1: Stop-ladder state machine (pure module)**

  Write `risk/stop_loss.py`: episode detection (anchor on sign change / zero-cross of
  held weight), `r/M/D/z` series per the PRD math, state transitions with hysteresis,
  and `apply_stop_overlay(held_weights, close, sigma, k1, k2, h, m_reduced) ->
  (multipliers, states, z)` as pure DataFrame-in/DataFrame-out functions. No I/O.

  Acceptance: unit tests pass covering (a) long episode: NORMAL->REDUCED->STOPPED on a
  monotone decline with hand-computed z at each step; (b) short episode symmetric;
  (c) recovery path STOPPED->REDUCED->NORMAL via hysteresis; (d) whipsaw guard: z
  oscillating inside `[-k1, -k1+h)` does NOT flap states; (e) episode reset on signal
  sign flip re-anchors entry and returns state to NORMAL.
  Files: `risk/stop_loss.py`, `tests/test_stop_loss.py`
  Validation: fails if any transition threshold is off by one day; fails if a short
  position's favorable move (price down) is treated as drawdown.

- [x] **T2: Overlay backtest -- baseline vs default-parameter ladder**

  Add `scripts/backtest_v9_stops.py`: run the v8.2 pipeline (trend -> band ->
  shift_to_next_day) as baseline, then the same with the T1 overlay applied post-band,
  both net of the existing cost model, 2015-01-01 to latest close. Save to
  `sprints/v9.1/artifacts/`: equity curves (both, one plot), drawdown curves, and a
  metrics table (net Sharpe, max DD, Calmar, annual turnover, hit-rate stat from PRD)
  for full period + the three subperiods.

  Acceptance: `sprints/v9.1/artifacts/stops_metrics.csv` and two PNGs exist; script
  prints the F1-F3 gate inputs; baseline full-period numbers match the v8.2 sprint's
  recorded backtest within 1% (regression check that the harness is wired right).
  Files: `scripts/backtest_v9_stops.py`, `sprints/v9.1/artifacts/`
  Validation: fails if baseline does not reproduce v8.2 results; fails if overlay run
  shares mutable state with baseline run (must be independent passes).

- [x] **T3: Leakage / lookahead check (paired validation for T1+T2)**

  Test A -- trigger-day loss is borne: construct a synthetic series where the z <= -k2
  crossing happens on day t; assert the backtest P&L includes the full day-t loss at
  the pre-stop weight and the reduction only affects day t+1 onward.
  Test B -- no future peak: truncate the price series at day t, recompute states;
  assert states through t are identical to the full-series run (M_i uses no future).
  Test C -- sigma alignment: assert the sigma used for day-t thresholds is the sizing
  sigma at t (63d window ending t), byte-identical to `compute_trend`'s sigma column.

  Acceptance: three tests pass in `tests/test_stop_loss.py`.
  Files: `tests/test_stop_loss.py`
  Validation: fails if the overlay's backtest Sharpe improves when tests force the
  weight change to day t (that improvement would be the lookahead smoking gun).

- [x] **T4: Sanity baselines -- naive ladder and shuffled-vol ladder**

  Run T2's harness twice more: (a) fixed -5%/-10% ladder, same hysteresis logic in
  return units (F5 input); (b) placebo: vol-scaled ladder where each ticker uses
  another ticker's sigma (fixed derangement, seed recorded). Append both to
  `stops_metrics.csv`.

  Acceptance: table now has 4 rows (baseline, vol-ladder, naive, placebo); F5
  comparison printed. Placebo expectation stated in the artifact: if the placebo
  matches the true ladder, per-name vol scaling is doing nothing.
  Files: `scripts/backtest_v9_stops.py`, `sprints/v9.1/artifacts/stops_metrics.csv`
  Validation: fails if the placebo run accidentally reuses correct sigmas (assert the
  mapping is a derangement in code).

- [x] **T5: Parameter sensitivity grid (falsification input F4, not tuning)**

  Grid: k1 in {1.0, 1.5, 2.0} x k2 in {2.0, 2.5, 3.0} x h in {0.25, 0.5}, k2 > k1
  cells only. For each: net Sharpe, max DD, Calmar. Save heatmap PNG + CSV. Print the
  F4 plateau verdict for the default cell's neighborhood.

  Acceptance: `sprints/v9.1/artifacts/stops_grid.csv` + heatmap PNG; F4 verdict printed.
  Files: `scripts/backtest_v9_stops.py`, `sprints/v9.1/artifacts/`
  Validation: fails review if any language in artifacts frames a non-default cell as
  "better" -- the grid is falsification evidence only (PRD multiple-testing rule).

- [x] **T6: Gate decision + live wiring (traded or advisory)**

  Evaluate F1-F5 from the T2/T4/T5 artifacts; record the verdict with numbers in
  `sprints/v9.1/notes.md`. Then wire `scripts/run_signal.py`: after the band step,
  recompute episodes from price history + drift-corrected positions, compute
  multipliers, and (PASS) apply `w_final = m * w_band` / (FAIL) leave weights
  untouched and set `advisory=true`. Persist per-ticker rows to Supabase
  `stop_states` (schema in PRD); add table to `sprints/v9.1/supabase_schema.sql` and
  run it. Multiplier trades bypass the 20% band (risk trades always execute).

  Acceptance: notes.md records the gate verdict; on a dry run, the proposed decision
  row reflects multiplied weights (or unchanged + advisory flag); `stop_states` has 8
  rows after one run; rerun same day is idempotent (existing cron_runs guard).
  Files: `scripts/run_signal.py`, `risk/stop_loss.py` (thin adapter only),
  `sprints/v9.1/supabase_schema.sql`, `sprints/v9.1/notes.md`
  Validation: fails if the multiplier is read from the cached Supabase row instead of
  recomputed (PRD live-state corruption rule); fails if a stop trade is suppressed by
  the rebalance band.

- [x] **T7: Panel H risk-status comment + banner**

  In `dashboard/views/operational.py`: add a `risk status` column to the Panel H table
  from `stop_states` (`NORMAL` / `REDUCED -50% (DD -8.2% = -1.9 sigma)` / `STOPPED
  (...)`) and a warning banner when any ticker is non-NORMAL listing ticker, z,
  threshold crossed, and the z level that restores it. Prefix `ADVISORY (not traded)`
  when the advisory flag is set. Graceful when `stop_states` is empty (pre-first-run).

  Acceptance: test with a mocked 3-state fixture asserts the exact reason strings and
  banner content (not just render-no-crash); empty-table case shows plain NORMAL rows.
  Files: `dashboard/views/operational.py`, `tests/test_dashboard_stops.py`
  Validation: fails if banner shows in the all-NORMAL case; fails if advisory mode is
  visually indistinguishable from traded mode.

- [x] **T8: Live risk module -- MCTR/PCTR (pure) + paired validation**

  Write `risk/live_risk.py`: `mctr_pctr(weights, returns_63d) -> DataFrame` per the
  PRD math (annualized, per ticker). Unit test on a 2-asset toy book against the
  closed-form answer to 1e-10; test that PCTR sums to 1.0 within 1e-6 (gate A1) and
  that dropping the last return row changes MCTR (i.e. cov window is anchored at t --
  gate A3 companion).

  Acceptance: tests pass; a __main__ smoke prints the live book's MCTR/PCTR table
  from Supabase positions + data/raw closes.
  Files: `risk/live_risk.py`, `tests/test_live_risk.py`
  Validation: fails if PCTR is computed off gross (unsigned) weights -- shorts must
  contribute with sign through the covariance.

- [x] **T9: Panel M -- render below Panel H**

  In `dashboard/views/operational.py`, insert "Panel M -- Live Risk & Attribution"
  directly below Panel H (before Panel I): (1) MCTR/PCTR bar chart from T8 on live
  weights; (2) P&L contribution table (1d / 5d / since-entry, share of book) from
  `live_attribution` + `pnl_log`; (3) factor attribution: latest betas, cumulative
  beta-explained vs residual over the live window, rolling R^2 -- via
  `rolling_factor_regression` on the reconstructed full-history book series, cached
  with `@st.cache_data` (v8.5 OOM lesson: one fit per session, panel labeled
  "betas from full-history reconstruction"). Empty-state handling for every block.

  Acceptance: panel renders under 5s cold (gate A4) with all three blocks on live
  data; screenshot saved to `sprints/v9.1/artifacts/panel_m.png`.
  Files: `dashboard/views/operational.py`, `dashboard/loader.py` (if a cached loader
  is added)
  Validation: fails if the factor fit runs on the days-long live series; fails if
  panel appears after Panel I instead of directly below H.

- [x] **T10: Panel M reconciliation checks (paired validation for T8+T9)**

  Tests: (a) A2 identity -- beta_explained + residual equals book P&L exactly on the
  overlap window; (b) A1 on the live snapshot -- PCTR sum within 1e-6; (c) A3
  lookahead -- recompute Panel M inputs with data through t-1; assert day-t display
  values unchanged; (d) P&L contribution table totals match `pnl_log` book totals
  within $0.01 per window.

  Acceptance: four tests pass in CI; results recorded in `sprints/v9.1/notes.md`.
  Files: `tests/test_live_risk.py`, `tests/test_dashboard_stops.py`,
  `sprints/v9.1/notes.md`
  Validation: fails if any reconciliation uses a tolerance looser than stated to pass.
