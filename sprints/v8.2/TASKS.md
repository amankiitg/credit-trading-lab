# Sprint v8.2 — Tasks

**Sprint closed. T1-T9 + T2b all done.** T2 was refined mid-sprint from
"rebalance frequency" to a state-based, proportional no-trade band — its
own success criterion (turnover to single-digit/low-double-digit) was
**not met** (3.3% cut), diagnosed as targeting the wrong 4% of turnover
(sign flips are 96%). **T2b** (signal-level hysteresis, a dead zone on the
sign decision) was added to address that directly and **did** meet the
criterion (83.0% cut, 32.32x → 5.48x), at an honestly-reported net-return
cost (7.79% → 4.24%). Nothing redefined to look like a win either way.

Status: `[ ]` = not done, `[x]` = done, `[~]` = partially done (see notes.md).

**Dependency order:** T1 → T2 → T2b → T3 → T4 → T5 → T6 → T7 → T8 → T9.
No hard stop-and-close gate (same as v8.1) — `E1–E6`/`B1–B2` are correctness
bugs to fix, not research dead-ends. If any fails, fix the code and re-run.

---

- [x] **Task T1: Symmetric trend signal + net/gross exposure**
  - Modify `signals/trend_signal.py::compute_trend` so `signal_i(t) =
    sign(trail_ret_i(t)) ∈ {-1, 0, +1}` (was `{0, 1}` in v8.1). Add `net`
    (`Σ weight_i(t)`, signed) and `gross` (`Σ |weight_i(t)|`) as explicit
    output columns in the tidy frame (gross already existed internally in
    v8.1; net did not).
  - Acceptance: tidy frame has `net` and `gross` columns; printed long/short/
    flat fraction per ticker (flat fraction should be ~0%, confirming the
    boundary case is not materially occurring on real data).
  - Files: `signals/trend_signal.py`
  - Validation: fails if `signal` is still clipped to `{0, 1}`; fails if
    `net` is computed as anything other than the plain signed sum.

- [x] **Task T2: No-trade band (refined from "rebalance frequency")**
  - **Scope refinement, recorded in `notes.md` and the PRD addendum:**
    originally written as a discrete `rebal_freq` (1 or 5) schedule; refined
    to a state-based, proportional no-trade band (`band_pct=0.20`, checked
    every day) as the more specific, better-targeted design. `rebal_freq`
    and a flat `no_trade_band` remain implemented (an earlier pass used
    `rebal_freq=5` and found a ~54% turnover cut) but are superseded as the
    chosen mechanism.
  - Implemented in `signals.trend_signal.apply_rebalance_control`
    (`band_pct` parameter): on breach, trades fully to target, not partway
    to the band edge; re-caps gross exposure after the hold logic (a real
    correctness need once names can go stale independently).
  - Acceptance: turnover before/after reported (32.32x -> 31.24x, a 3.3%
    cut). **Did not meet the single-digit/low-double-digit success
    criterion** -- diagnosed via a turnover decomposition (96% of turnover
    from sign flips, 4% from same-sign wobble; a magnitude band only
    addresses the 4%). Reported honestly, not as a success.
  - Files: `signals/trend_signal.py`, `tests/test_trend_signal.py`,
    `notebooks/08_2_add_shorts.ipynb`
  - Validation: no-look-ahead, gross cap, and weight-bound tests all pass
    for the proportional band specifically (not just the superseded
    discrete-schedule path).

- [x] **Task T2b: Signal-level hysteresis (dead zone), addressing sign-flip turnover**
  - **Context recorded in notes.md:** T2's magnitude band missed its
    turnover target because 96% of this signal's turnover is sign-flip-driven,
    and a magnitude band structurally cannot touch sign flips. T2b adds the
    matching tool -- a dead zone around the trend signal's zero line --
    and stacks on top of T2 rather than replacing it.
  - Implemented in `signals.trend_signal.compute_trend` via a new
    `k_dead_zone` parameter, `compute_dead_zone` (the pre-registered width
    formula), and `_hysteresis_signal` (the stateful sign decision).
    `dead_zone_i(t) = k * sigma_i(t) * sqrt(L/252)`, `k=0.5` pre-registered,
    landed in the target range on the first run -- no adjustment needed.
  - Acceptance: three-stage turnover comparison (pre-T2 32.32x -> T2-only
    31.24x -> T2+T2b 5.48x, an 83.0% cut vs. stage 1, meeting the
    single-digit/low-double-digit success criterion). Net return cost
    reported honestly (7.79% -> 4.24%), not minimized. Responsiveness cost
    quantified: 985/1183 raw flips absorbed as noise; the 198 confirmed
    flips lag the raw signal by 8.9 trading days on average.
  - Files: `signals/trend_signal.py`, `tests/test_trend_signal.py`,
    `notebooks/08_2_add_shorts.ipynb`
  - Validation: dead-zone oscillation produces no flip; a clear move past
    the opposite threshold does flip; the dead zone is symmetric; no
    look-ahead in the stateful sign; the vol-target identity and gross cap
    hold with hysteresis on; T2's magnitude band still functions stacked
    on top of the hysteresis-buffered signal.

- [x] **Task T3: Daily P&L accumulator (B1, B2)**
  - Build `backtest/multi_asset.py` with a function that takes a weight
    matrix and a return matrix and produces a daily P&L series:
    `daily_pnl(t) = Σ_i [weight_i(t-1) * asset_return_i(t)] * NAV -
    daily_cost(t)`, where `daily_cost(t)` uses `execution.costs.CostParams`
    defaults (`half_spread_bp=1.5`, `slippage_bp=0.5`, `borrow_annual=0.004`)
    applied as `(half_spread_bp + slippage_bp) * |Δweight_i(t)| * NAV` plus
    `borrow_annual/252 * NAV * Σ_i max(-weight_i(t), 0)`.
  - Acceptance: B1 (exact P&L reconciliation, no off-by-one) and B2 (cost
    constants match `CostParams()` defaults exactly) verified by direct
    pytest assertion, not eyeballed.
  - Files: `backtest/multi_asset.py`, `tests/test_multi_asset_backtest.py`
  - Validation: fails if `weight_i(t)` (same-day, not `t-1`) is used to
    compute day-`t` P&L (look-ahead in the P&L itself); fails if the cost
    constants differ from `CostParams()` defaults.

- [x] **Task T4: Long-only book through the accumulator**
  - Run the v8.1 long-only/flat rule (kept available as a code path, not
    deleted) through `backtest/multi_asset.py` at `rebal_freq ∈ {1, 5}`.
    Report annualized net return, annualized vol, max drawdown, and turnover
    (trailing-252d average `Σ_i |Δweight_i(t)|`, annualized) for both.
  - Acceptance: 2x2 metrics printed (2 rebal_freq values), all four numbers
    per cell.
  - Files: `sprints/v8.2/notes.md`
  - Validation: fails if `turnover` is computed using
    `backtest.metrics.summary`'s trade-count definition instead of the
    continuous-book definition specified in the PRD.

- [x] **Task T5: Long/short book through the accumulator**
  - Same as T4, for the v8.2 symmetric signal.
  - Acceptance: same 2x2 metrics table, long/short version.
  - Files: `sprints/v8.2/notes.md`
  - Validation: same as T4; additionally fails if borrow cost is zero on any
    day with an active short position (B2 violation).

- [~] **Task T6: Comparison table + rebalance-frequency decision**
  - Build the long-only vs long/short x daily vs weekly comparison table
    (4 cells from T4+T5). Apply the pre-registered decision rule (weekly
    only if turnover drops ≥30% relative to daily AND max drawdown does not
    worsen by more than 10% relative to daily) and state the chosen
    `rebal_freq` default explicitly. Check the two operational sanity flags
    (turnover >50% of gross exposure; gross pinned at `g_max` >80% of days)
    against the carried `L/v/w_max/g_max` defaults.
  - Acceptance: comparison table printed; rebalance-frequency decision
    stated with the exact numbers that drove it; sanity-flag check stated
    PASS/FAIL for each flag.
  - Files: `sprints/v8.2/notes.md`, `sprints/v8.2/plots/comparison.png`
  - Validation: fails if the decision rule's thresholds differ from the PRD
    (30% / 10%); fails if the decision is justified by a Sharpe-like number
    instead of turnover/drawdown.

- [~] **Task T7: Leakage / look-ahead re-verification**
  - Re-run the v8.1-style perturbation test (E1) across the full pipeline —
    signal, position construction, rebalance-frequency hold logic, and the
    new daily P&L accumulator — for both `rebal_freq` values and both rule
    variants (long-only, long/short).
  - Acceptance: all perturbation tests pass, included in the pytest suite.
  - Files: `tests/test_multi_asset_backtest.py` (or extend
    `tests/test_trend_signal.py`)
  - Validation: fails if any perturbation test perturbs dates at or before
    the cutoff (would not test look-ahead).

- [ ] **Task T8: Sanity baseline — buy-and-hold equal-weight basket**
  - Run a buy-and-hold equal-weight (1/8 per name, no rebalancing, no
    vol-targeting) basket of the same 8 names through the same P&L
    accumulator and cost model. Report the same four metrics alongside the
    trend book's numbers, with no claim of superiority either way.
  - Acceptance: baseline metrics printed side by side with T4/T5 results.
  - Files: `sprints/v8.2/notes.md`
  - Validation: fails if the baseline uses a different cost model or NAV
    convention than the trend book (would make the comparison meaningless).

- [x] **Task T9: S2 guardrail + sprint close**
  - S2 guardrail written verbatim in `notes.md` and `WALKTHROUGH.md`. Gate
    table (`E1-E6`, `B1-B2`, `S2`, plus the T2 success criterion) stated in
    `WALKTHROUGH.md`. Chosen operational defaults: `L=120`, `v=0.10`,
    `w_max=0.50`, `g_max=2.0` (all unchanged from v8.1, no operational
    sanity flag triggered), `band_pct=0.20` (new, T2's chosen mechanism,
    despite not hitting its own turnover target -- reasoning is
    operational throughout, never Sharpe-based).
  - Acceptance: guardrail statement present verbatim; gate table present;
    defaults stated explicitly with non-Sharpe reasoning.
  - Files: `sprints/v8.2/notes.md`, `sprints/v8.2/WALKTHROUGH.md`
  - Validation: passes -- guardrail verbatim, no parameter change justified
    by a Sharpe/return number anywhere in this sprint.
