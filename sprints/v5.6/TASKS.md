# Sprint v5.6 — Tasks

Nine atomic tasks (S1–S9). A **validation gate** sprint: no new signals, no
new code, no engine changes. Every task uses frozen v5/v5.5 infrastructure.
The output is `sprints/v5.6/signal_selection.md` — a binding document that
determines which signals enter Tier 2.

Status starts `[ ]`; flip to `[x]` as each task lands.

**Dependency order:** S1 (setup) → S2/S3 (standalone per signal) →
S4/S5 (robustness per signal) → S6 (random baseline) → S7 (portfolio) →
S8 (notebook) → S9 (selection doc).

---

- [x] **Task S1: Scaffold + baseline reconfirmation**
  - Acceptance: `sprints/v5.6/notes.md` created with: (a) RV1_A v5 benchmark
    numbers copied verbatim (Sharpe 0.591, hit 80.9%, 94 trades, total P&L
    $760,372); (b) today's informal RV2_A/RV3_A numbers flagged as
    **unregistered** with a note they must be reproduced from scratch; (c) a
    one-line confirmation that features.parquet is post-v5.5 (shape 4784×56,
    rv_hy_ig HL=17.6d). No code results yet — setup only.
  - Files: `sprints/v5.6/notes.md`
  - Validation: fails if the unregistered numbers are treated as validated;
    fails if features.parquet is not confirmed post-v5.5 before any test runs.

- [x] **Task S2: Standalone backtest — RV2_A and RV3_A**
  - Acceptance: run `build_strategy(features, residuals, StrategySpec(pair,
    'ols', gated=False))` for rv_credit_rates and rv_xterm. Record in
    `notes.md`: Sharpe, hit rate, n_trades, avg_hold, max_drawdown,
    total_net_pnl for each. Print all trades for each signal. Save equity
    curves to `sprints/v5.6/plots/equity_rv2_rv3.png` (both on same axes,
    with RV1 as grey reference line). Evaluate M1 (Sharpe > 0.40) and M2
    (hit > 65%) for each.
  - Files: `sprints/v5.6/notes.md`, `sprints/v5.6/plots/equity_rv2_rv3.png`
  - Validation: fails if parameters differ from v5 (entry=2.0, exit=0.5,
    stop=4.0, fill_lag=1); fails if RV1 reference line doesn't match v5
    numbers; fails if M1/M2 verdicts are not explicitly stated.

- [x] **Task S3: Single-trade dominance check — M3**
  - Acceptance: for each signal, compute max(|net_pnl|) / total_net_pnl.
    Record in `notes.md`. Evaluate M3 (≤ 25%) for each. If any signal has
    a single trade > 25%, identify the date and post-mortem (was it a crisis
    entry? a stop-out?). Save a bar chart of per-trade P&L for each signal
    to `sprints/v5.6/plots/trade_pnl_distribution.png`.
  - Files: `sprints/v5.6/notes.md`, `sprints/v5.6/plots/trade_pnl_distribution.png`
  - Validation: fails if the denominator uses gross rather than net P&L;
    fails if a dominant trade is noted without a post-mortem.

- [x] **Task S4: Parameter grid robustness — M4**
  - Acceptance: run `parameter_grid(features, residuals)` for rv_credit_rates
    and rv_xterm (adapting or reusing the existing function). Grid: entry ∈
    {1.5, 2.0, 2.5}, exit ∈ {0.25, 0.50, 1.00}, stop ∈ {3, 4, 5} → 27
    cells each. Record fraction of cells with Sharpe_A > 0. Evaluate M4
    (≥ 60%). Save heatmaps to `sprints/v5.6/plots/grid_rv2.png` and
    `sprints/v5.6/plots/grid_rv3.png`.
  - Files: `sprints/v5.6/notes.md`, `sprints/v5.6/plots/grid_rv{2,3}.png`
  - Validation: fails if the grid tests delta_sharpe (A vs B) instead of
    standalone Sharpe_A; fails if fewer than 27 cells are evaluated.

- [x] **Task S5: Subperiod stability — M5**
  - Acceptance: split sample at the midpoint (~2016-09) consistent with v5.
    Record Sharpe in first half and second half for RV2_A and RV3_A. Evaluate
    M5 (both > 0). Bar chart of subperiod Sharpes (RV1/RV2/RV3 side by side)
    saved to `sprints/v5.6/plots/subperiod.png`.
  - Files: `sprints/v5.6/notes.md`, `sprints/v5.6/plots/subperiod.png`
  - Validation: fails if split date differs from v5; fails if a negative
    subperiod Sharpe is not flagged as M5 FAIL.

- [x] **Task S6: Random baseline — M6**
  - Acceptance: compute per-trade Sharpe for RV2_A and RV3_A using
    `backtest.benchmarks.trade_sharpe`. Compare against the v5 random p95
    (1.70 from random_baseline.parquet). Record excess Sharpe for each.
    Evaluate M6. Note the methodological caveat: baseline was built with
    64-trade simulations; RV2 has 103 trades and RV3 has 101 → sqrt(n)
    scaling differs. Report the comparison honestly with the caveat logged
    in `notes.md`.
  - Files: `sprints/v5.6/notes.md`
  - Validation: fails if the caveat about trade-count mismatch is omitted;
    fails if a different random baseline is generated instead of reusing v5.

- [x] **Task S7: Cross-signal correlation + portfolio — M7, M8**
  - Acceptance: (a) compute pairwise Pearson ρ of daily net P&L between all
    qualifying signals (those passing M1–M6). Print correlation matrix.
    Evaluate M7: flag any pair with ρ > 0.70. (b) build equal-weight
    portfolio: sum daily_pnl of qualifying signals. Compute portfolio
    Sharpe, hit rate (fraction of trade-days with positive P&L), max
    drawdown, total P&L. Evaluate M8 (portfolio Sharpe ≥ best individual ×
    0.85). (c) save to `sprints/v5.6/plots/portfolio_equity.png`: individual
    equity curves + combined portfolio curve, all on same axes. (d) record
    regime breakdown of portfolio trades (by equity_credit_lag) in `notes.md`.
  - Files: `sprints/v5.6/notes.md`, `sprints/v5.6/plots/portfolio_equity.png`
  - Validation: fails if non-qualifying signals (those failing M1–M6) are
    included in the portfolio; fails if portfolio P&L is not the sum of
    individual daily series (no weight optimisation); fails if correlation
    matrix uses trade P&L instead of daily P&L.

- [x] **Task S8: Before/after notebook `56_multisignal_validation.ipynb`**
  - Acceptance: a runnable notebook with four sections: (1) standalone
    backtest summary table — RV1/RV2/RV3 side by side with M1–M6 pass/fail
    column; (2) parameter grid heatmaps for RV2 and RV3; (3) subperiod bar
    chart; (4) portfolio equity curve + correlation matrix. All plots saved
    to `sprints/v5.6/plots/`. Notebook executes top-to-bottom without error
    via `nbconvert`. Generator script at
    `scripts/build_notebook_v5_6.py`.
  - Files: `notebooks/56_multisignal_validation.ipynb`,
    `scripts/build_notebook_v5_6.py`, `sprints/v5.6/plots/`
  - Validation: fails if any cell errors; fails if M1–M6 verdicts are not
    shown in the summary table; fails if RV1 benchmark numbers don't match
    v5 exactly.

- [x] **Task S9: Signal selection document + sprint close**
  - Acceptance: `sprints/v5.6/signal_selection.md` written and committed.
    Must contain: (a) M1–M8 scorecard table with stored numbers for every
    criterion; (b) explicit list of signals admitted to Tier 2 with the
    reason; (c) explicit list of signals excluded with the reason; (d) a
    one-paragraph portfolio recommendation: how many signals, at what
    notional, what combined Sharpe/drawdown to expect; (e) a note on the
    multiple-testing concern and whether Bonferroni adjustment changes any
    admission decision. `sprints/v5.6/notes.md` finalised.
  - Files: `sprints/v5.6/signal_selection.md`, `sprints/v5.6/notes.md`
  - Validation: fails if any criterion is listed without a stored number;
    fails if the admission list is inconsistent with the scorecard; fails
    if the multiple-testing note is absent; fails if M9 intuition paragraph
    is missing for each admitted signal.
