# Sprint v9.1 -- Notes

## 2026-08-11 -- T1: Stop-ladder state machine

### Implementation
- Wrote `risk/stop_loss.py`: pure state machine for vol-scaled stop ladder.
  - `_run_single_episode()`: core state machine for one position episode.
  - `compute_episodes()`: episode detection from held weights, per-ticker state tracking.
  - `apply_stop_overlay()`: main entry point with input/output invariants asserted.
- Wrote `tests/test_stop_loss.py`: 18 tests covering all 5 acceptance criteria.

### Test results
- **18/18 passed** (0.65s).
- No regressions: existing 20 attribution tests still pass (38/38 combined, 8.76s).

### Acceptance coverage
| Criterion | Test | Status |
|-----------|------|--------|
| (a) Long episode NORMAL->REDUCED->STOPPED on monotone decline | `test_long_monotone_decline_normal_to_reduced_to_stopped` | PASS |
| (b) Short episode symmetric (adverse move triggers stops) | `test_short_episode_symmetric` | PASS |
| (b) Short favorable move (price down) NOT treated as drawdown | `test_short_favorable_move_not_treated_as_drawdown` | PASS |
| (c) Recovery STOPPED->REDUCED->NORMAL via hysteresis | `test_recovery_stopped_to_reduced_to_normal` | PASS |
| (d) Whipsaw guard: z oscillating in [-k1, -k1+h) does NOT flap | `test_whipsaw_guard_no_flap` | PASS |
| (e) Episode reset on signal sign flip | `test_episode_reset_on_sign_flip` + `test_episode_reset_full_pipeline` | PASS |
| Deep breach (NORMAL->STOPPED directly) | `test_skip_straight_to_stopped_on_deep_breach` | PASS |
| Zero-vol guard | `test_zero_vol_guard` | PASS |
| Running peak only from gains | `test_running_peak_updates_only_on_gains` | PASS |

### Invariants asserted in code
- Input: date index monotonic & unique, k2 > k1 > 0, 0 < m_reduced < 1, h > 0
- Output: multipliers in {1.0, m_reduced, 0.0}, no infs, no silent row drops
- Shape preservation: multipliers/states/z all match held_weights shape

### Edge cases covered
- Pre-entry NaN weights -> NaN output
- Mid-episode NaN/gap -> episode ends, new episode starts fresh
- Single-row input -> no crash
- Zero-vol -> clamps to 1e-12, no inf/crash
- Duplicate date index -> raises AssertionError

### Key numbers
- Pre-registered defaults: k1=1.5, k2=2.5, h=0.5, m_reduced=0.5
- sigma_m = sigma_annual * sqrt(21/252)
- Test sigma_m range: 0.023-0.043 (6%-15% annual vol at 1-month horizon)
- State machine: O(n) per ticker, vectorized within episode

---

## 2026-08-11 -- T2: Overlay backtest baseline vs default-parameter ladder

### Implementation
- Wrote `scripts/backtest_v9_stops.py`: full backtest harness.
  - Loads universe closes from yfinance cache.
  - Runs v8.2 pipeline (trend L=120, dead_zone=0.5, band=20%) as baseline.
  - Applies T1 stop overlay (k1=1.5, k2=2.5, h=0.5, m_reduced=0.5) post-band.
  - Computes `final_weights = multipliers * held_weights`, shifts to next day.
  - Both runs through multi_asset backtest with v6.5 costs.
  - Saves metrics CSV + 3 PNGs to `sprints/v9.1/artifacts/`.

### Backtest results (2015-01-02 to 2026-06-22, 2883 days, 8 tickers)

| Metric | Baseline | Overlay |
|--------|----------|---------|
| Net Sharpe | 0.2446 | 0.2025 |
| Max DD (return) | -32.84% | -33.86% |
| Calmar | 0.1297 | 0.0959 |
| Ann Return | 4.26% | 3.25% |
| Ann Vol | 17.41% | 16.04% |
| Ann Turnover | 5.75x | 11.30x |

### Subperiod Sharpe
| Period | Baseline | Overlay |
|--------|----------|---------|
| 2015-2018 | 0.0169 | **-0.0650** |
| 2019-2022 | 0.1596 | 0.2271 |
| 2023-end | 0.2472 | 0.2194 |

### Gate evaluation (pre-registered, default parameters only)

| Gate | Criterion | Result | Verdict |
|------|-----------|--------|---------|
| F1 | Overlay Sharpe >= 0.80 × Baseline | 0.828 | PASS (barely) |
| F2 | DD reduction >= 10% OR Calmar improvement | DD -3.1% (worse), Calmar 0.096 vs 0.130 | **FAIL** |
| F3 | Overlay doesn't underperform all 3 subperiods | Underperforms 2/3 | **FAIL** |
| F4 | Default not a spike (grid pending T5) | — | PENDING |
| F5 | Naive ladder < 90% of vol-ladder DD benefit (pending T4) | — | PENDING |

**Pre-registered contingency: F2 and F3 fail → REJECTED.**
The stop ladder ships in **advisory mode only** — states computed, displayed,
but multiplier never applied to live weights.

### Hit-rate
- 9,770 trigger days (multiplier < 1.0)
- 4,017 where next 21d return was negative (had position been held at full size)
- Hit rate = **41.12%** (95% CI: 40.14% – 42.09%)
- Below 50% → stops systematically sell local bottoms, confirming the PRD's
  "honest counter-story" (mean reversion at daily horizon dominates)

### State distribution (full history)
| State | EEM | EFA | GLD | HYG | IEF | LQD | SPY | TLT |
|-------|-----|-----|-----|-----|-----|-----|-----|-----|
| NORMAL | 3293 | 3662 | 3165 | 3685 | 3343 | 3699 | 3714 | 3349 |
| REDUCED | 1114 | 740 | 1061 | 735 | 1102 | 770 | 724 | 1013 |
| STOPPED | 303 | 308 | 484 | 290 | 265 | 241 | 272 | 348 |

- 25.3% of weight-days have multiplier < 1.0
- GLD has most STOPPED days (484), consistent with known GLD drawdown
- Turnover doubles (5.75→11.30) — expected for a state that flaps

### Artifacts
- `sprints/v9.1/artifacts/stops_metrics.csv`
- `sprints/v9.1/artifacts/stops_equity_drawdown.png`
- `sprints/v9.1/artifacts/stops_return_distribution.png`
- `sprints/v9.1/artifacts/stops_zscore_distribution.png`

### Regression note
- Baseline numbers not compared to v8.2 reference (different eval window:
  2015-2026 here vs 2007-2026 in v8.2 notes). The regression check
  specified in TASKS.md requires the v8.2 backtest re-run on the same
  2015+ window — noted as a T2 follow-up.

---

## 2026-08-11 -- T3: Leakage / lookahead checks

### Tests added
- `test_a_trigger_day_loss_is_borne`: Verifies day-t P&L uses pre-stop weight (1.0),
  day-t+1 uses post-stop weight (0.0). Confirms `shift_to_next_day` convention
  correctly defers the stop effect.
- `test_b_no_future_peak_truncation`: Truncates price series at day t, verifies
  states through t identical to full-series run. Confirms M_i(t) uses no future data.
- `test_c_sigma_alignment_with_compute_trend`: Verifies the sigma passed to the
  overlay is byte-identical to `compute_trend`'s sigma column. Confirms the 63d
  window ending t matches.

### Results
- **21/21 tests pass** (0.59s)
- No lookahead detected in any path.

---

## 2026-08-11 -- T4: Sanity baselines

### Naive -5%/-10% ladder
- Same state-machine logic but on raw return units (not z-scores)
- Fixed thresholds: REDUCED at -5%, STOPPED at -10%, 1% hysteresis

| Metric | Baseline | Vol-Ladder | Naive |
|--------|----------|------------|-------|
| Net Sharpe | 0.2446 | 0.2025 | **0.2298** |
| Max DD (return) | -32.84% | -33.86% | **-23.12%** |
| Calmar | 0.1297 | 0.0959 | **0.1397** |

**Finding: Naive ladder significantly outperforms vol-ladder.** DD improves by
9.72pp vs baseline, while vol-ladder makes DD 1.02pp WORSE. Both Sharpe and
Calmar are better for the naive version.

### Placebo (shuffled vol)
- Each ticker uses another ticker's sigma (derangement, seed=123 recorded)
- Sharpe 0.2670, DD -25.42%, Calmar 0.1529

**Finding: Placebo outperforms vol-ladder and even baseline on Sharpe.**
This suggests the per-name vol-scaling interaction with the stop logic is
unstable — the exact mapping of sigma to ticker matters significantly.

### F5 verdict
- Vol-ladder did not improve DD (made it worse by 1.02pp)
- Naive improved DD by 9.72pp
- **F5: FAIL** — vol scaling adds nothing over the dumb version; the dumb
  version is actually better. Pre-registered rejection criterion met.

---

## 2026-08-11 -- T5: Parameter sensitivity grid

### Grid: k1 in {1.0, 1.5, 2.0} x k2 in {2.0, 2.5, 3.0} x h in {0.25, 0.5}, k2 > k1 only
16 cells tested. Full results in `sprints/v9.1/artifacts/stops_grid.csv`.

### Key observations
- Default cell (k1=1.5, k2=2.5, h=0.5): Sharpe=0.1406, DD=-33.86%
- Best cell: k1=1.0, k2=2.0, h=0.25: Sharpe=0.2056, DD=-26.75%
- Default is among the worst cells in the grid on both Sharpe and DD
- Only 1/15 neighbors (6.67%) share the default's qualitative result

### F4 verdict
- **FAIL (spike)**: Default parameter result is an outlier, not a plateau
- 1/15 < 50% required → pre-registered rejection criterion met

### Artifacts
- `sprints/v9.1/artifacts/stops_grid.csv`
- `sprints/v9.1/artifacts/stops_grid_heatmap.png`

---

## 2026-08-11 -- T6: Gate decision

### Complete gate table

| Gate | Criterion | Result | Numeric | Verdict |
|------|-----------|--------|---------|---------|
| F1 | Overlay Sharpe >= 0.80 × Baseline | Overlay=0.203, Baseline=0.245, Ratio=0.828 | 0.828 > 0.80 | PASS (barely) |
| F2 | DD reduction >= 10% OR Calmar improvement | DD: -3.1% (worse), Calmar: 0.096 vs 0.130 | — | **FAIL** |
| F3 | Not underperform all 3 subperiods | Underperforms 2/3 | 2015-18: -0.07 vs 0.02, 2023-end: 0.22 vs 0.25 | **FAIL** |
| F4 | Default not a spike (plateau) | 1/15 neighbors share result | 6.67% < 50% | **FAIL** |
| F5 | Naive < 90% of vol-ladder DD benefit | Naive IMPROVES DD, vol-ladder does not | — | **FAIL** |

### Decision: REJECTED — ship in ADVISORY MODE ONLY
- 4 of 5 gates fail (F2, F3, F4, F5)
- The stop ladder does not improve risk-adjusted returns and systematically
  underperforms the baseline
- Hit rate 41.1% (stops sell local bottoms more often than they dodge losses)
- Turnover doubles without compensating benefit

### Contingency action
Per PRD pre-registration:
- State machine still ships (T1 code unchanged)
- Stop states computed daily, written to Supabase, displayed in Panel H
- Multiplier NEVER applied to live weights
- Dashboard labels mode explicitly as "ADVISORY (not traded)"

### Wiring (run_signal.py)
- After band step: recompute episodes from price history + positions
- Compute multipliers, but DO NOT apply (`w_final = w_band`, not `m * w_band`)
- Set `advisory=true` in Supabase `decisions` table
- Persist per-ticker rows to `stop_states` for display only


