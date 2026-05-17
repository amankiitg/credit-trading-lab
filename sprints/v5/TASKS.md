# Sprint v5 — Tasks

Ten atomic tasks, each 10–20 minutes of focused work. Every
engine/signal task is paired with a test. Status starts at `[ ]`;
flip to `[x]` as each lands.

Pre-req: `sprint-v3` tag checked out — `features.parquet` (4784 × 56),
`regime_signal_quality.parquet`, `random_baseline.parquet`, and the
`pycredit` module all present.

See `PRD.md` §Falsification Criteria for C25–C31 definitions.

---

- [x] **Task W1: Cost model — half-spread + slippage + borrow**
  - Acceptance: `execution/costs.py` exposes a pure
    `trade_cost(notional, holding_days, n_legs=2)` returning the
    total round-trip cost in dollars, using the pre-registered
    constants (`half_spread_bp=1.5`, `slippage_bp=0.5`,
    `borrow_annual=0.004`). `tests/test_costs.py` hand-verifies the
    cost of a known trade (e.g. $1M, 20 days held) component by
    component.
  - Files: `execution/costs.py`, `tests/test_costs.py`.
  - Validation: fails if any constant deviates from the PRD; fails
    if cost is not monotonic increasing in `holding_days` and
    `notional`; fails if `trade_cost` is impure (I/O, globals).

- [x] **Task W2: Position state machine**
  - Acceptance: `execution/position.py` exposes
    `run_state_machine(z, entry, exit_t, stop, regime_gate=None)`
    returning a `pd.Series` of {−1, 0, +1} positions. Implements
    the PRD §Signal Definition transitions: flat→short at
    `z>+entry`, flat→long at `z<−entry`, →flat at `|z|<exit`
    (exit) or `|z|>stop` (stop), one position at a time. When
    `regime_gate` (a boolean Series) is passed, *entries* fire only
    where the gate is True; exits/stops always fire.
    `tests/test_position.py` checks transitions on a hand-built z
    series and confirms the gate blocks entries but never exits.
  - Files: `execution/position.py`, `tests/test_position.py`.
  - Validation: fails if two positions are open at once; fails if a
    gated entry fires on a False-gate day; fails if an exit/stop is
    ever suppressed by the gate; fails if `center=True` or future z
    is used.

- [x] **Task W3: Backtest engine + leakage check (C25)**
  - Acceptance: `backtest/engine.py` exposes `run(residual,
    positions, hedge_ratio, notional, fill_lag=1)` returning a
    trade ledger (entry/exit date, side, rv at entry/exit,
    hedge_ratio at entry/exit, holding_days, gross_pnl, cost,
    net_pnl) and a daily net-P&L Series. Fills lagged `fill_lag`
    days. `tests/test_engine.py`: (a) a synthetic sinusoid residual
    produces total P&L matching a hand calc to < 1e-6 (C25);
    (b) a leakage test perturbs a future bar and asserts no past
    position or trade changes.
  - Files: `backtest/engine.py`, `tests/test_engine.py`.
  - Validation: fails C25 if P&L mismatches the hand calc or if
    perturbing `t+k` changes any trade at `t`; fails if a trade's
    `net_pnl != gross_pnl − cost`.

- [x] **Task W4: Metrics module**
  - Acceptance: `backtest/metrics.py` exposes `summary(daily_pnl,
    trades, notional)` returning annualized Sharpe (√252),
    **Sortino** (√252, downside-deviation denominator), hit
    rate, turnover (trades/yr), max drawdown, avg holding days,
    total net P&L, trade count. `tests/test_metrics.py` verifies
    Sharpe, Sortino, and max-drawdown on hand-computable series
    (e.g. a constant-positive series → high Sharpe; a known
    drawdown path; a series with known downside deviation).
  - Files: `backtest/metrics.py`, `tests/test_metrics.py`.
  - Validation: fails if Sharpe/Sortino are not annualized; fails
    if Sortino's denominator uses full std instead of downside
    deviation; fails if max drawdown is positive-signed or computed
    on P&L instead of the cumulative equity curve; fails if an
    all-zero P&L series raises instead of returning 0 / NaN-safe.

- [ ] **Task W5: A/B comparison + bootstrap CI (C26, C27)**
  - Acceptance: `backtest/ab_test.py` exposes `compare(pnl_a,
    pnl_b, n=1000, block=21, seed=20260516)` returning ΔS, the
    2.5/97.5 percentile CI, and the fraction of resamples with
    ΔS>0, via stationary block bootstrap of the daily net-P&L
    series. Driver builds Strategy A (RV1 OLS, no gate) and
    Strategy B (RV1 OLS, `equity_first` gate), runs both through
    W3, and prints the A/B table. Saves the full ledger to
    `data/results/backtest_trades.parquet`.
  - Files: `backtest/ab_test.py`, `tests/test_ab_test.py`,
    `data/results/backtest_trades.parquet`.
  - Validation: fails C26 if Strategy A has < 30 trades or a
    non-finite Sharpe; records C27 (ΔS and CI) — pre-registered,
    pass/fail logged in `notes.md` whatever the result; fails if
    the bootstrap is not seeded or block length ≠ 21.

- [ ] **Task W6: Walk-forward out-of-sample test (C28)**
  - Acceptance: `backtest/ab_test.py` extended with
    `walk_forward(...)` — grid-search entry/exit/stop on
    2007-01-01→2018-12-31 by net Sharpe of Strategy B, lock the
    best cell, apply unchanged to 2019-01-01→2026-04-15, and
    report ΔS on the test window. Notebook cell prints train-chosen
    thresholds + OOS ΔS.
  - Files: `backtest/ab_test.py` (extended).
  - Validation: fails C28 if OOS ΔS ≤ 0; fails if any test-window
    data touched the threshold calibration (leakage); fails if the
    train/test boundary is not the pre-registered `2018-12-31`.

- [ ] **Task W7: Benchmarks — buy-hold + random p95 (C29)**
  - Acceptance: `backtest/benchmarks.py` exposes `vs_random(
    strategy_sharpe, random_baseline_df, spread="hy_spread")`
    returning the random-baseline 95th-percentile Sharpe and the
    excess Sharpe, plus a buy-hold-HYG equity curve over the same
    period. Notebook overlays Strategy A, Strategy B, buy-hold HYG,
    and the random-p95 marker on one equity-curve chart.
  - Files: `backtest/benchmarks.py`, `tests/test_benchmarks` (extend
    existing or new), `sprints/v5/plots/03_benchmarks.png`.
  - Validation: fails C29 if Strategy B excess Sharpe ≤ 0; fails if
    buy-hold uses adjusted-close incorrectly (must be cumulative
    HYG log-return, matching `HYG_buyhold_cum_log_ret`); fails if
    the random p95 is taken from the wrong `spread` rows.

- [ ] **Task W8: Failure slide (C31)**
  - Acceptance: for each of Strategy A and B, extract the worst 5
    trades by `net_pnl`; record entry/exit date, net_pnl (bps of
    notional), z at entry/exit, **all four regime labels** at
    entry (`vol_regime`, `equity_regime`, `equity_credit_lag`, and
    a crisis flag), hedge ratio at entry/exit, holding days, and a
    one-line post-mortem string. Save to
    `data/results/failure_analysis.parquet`. Notebook renders it as
    a table. Also assert C31 (no single trade > 25% of total P&L).
  - Files: `backtest/ab_test.py` (extended) or
    `backtest/failure.py`, `data/results/failure_analysis.parquet`.
  - Validation: fails C31 if any single trade exceeds 25% of its
    strategy's total P&L; fails if the parquet lacks any required
    column or the post-mortem field is empty.

- [ ] **Task W9: Robustness — parameter grid + subperiod + regime table (C30)**
  - Acceptance: re-run the A/B comparison over the 27-cell grid
    (entry∈{1.5,2.0,2.5} × exit∈{0.25,0.5,1.0} × stop∈{3,4,5}) and
    over the two halves split at 2016-09-15; report the fraction of
    grid cells with ΔS>0 and ΔS for each half. Also build a
    **regime table**: every strategy (A, B, and the RV2/RV3
    Strategy-B variants) × every regime label across all three
    classifiers (`vol_regime`, `equity_regime`,
    `equity_credit_lag`), reporting Sharpe / Sortino / maxDD /
    hit_rate of the daily P&L restricted to each regime; saved to
    `data/results/regime_performance.parquet`. Also run the A/B
    once each under Kalman and DV01 hedge methods as a
    reported-only robustness panel. Notebook renders a grid heatmap
    of ΔS, a subperiod bar chart, and the regime table.
  - Files: `backtest/ab_test.py` (extended),
    `backtest/regime_table.py`,
    `data/results/regime_performance.parquet`,
    `sprints/v5/plots/04_robustness_grid.png`,
    `sprints/v5/plots/05_subperiod.png`.
  - Validation: fails C30 if < 75% of grid cells have ΔS>0 or if
    either half has ΔS ≤ 0; fails if the grid silently reuses one
    threshold set for all cells; fails if the regime table omits
    any (strategy × regime) cell or any of the 4 metrics.

- [ ] **Task W10: Multi-signal portfolio + sprint close**
  - Acceptance: `risk/portfolio.py` combines Strategy-B-style
    backtests of RV1, RV2, RV3 (OLS hedge, `equity_first` gate)
    into one book under **two weighting schemes — equal-weight and
    inverse-volatility** (weights ∝ 1/σ of each signal's daily
    P&L, trailing); report portfolio Sharpe for each scheme vs the
    best single signal. `notebooks/05_backtest.ipynb` runs
    top-to-bottom: A/B equity curves with the difference
    highlighted, failure slide table, benchmark overlay, robustness
    heatmap + subperiod chart, portfolio curve, and a C25–C31
    falsification checklist (PASS/FAIL). `sprints/v5/notes.md`
    records every criterion's result with exact numbers;
    `sprints/v5/WALKTHROUGH.md` written per `/quant-walkthrough`.
    Full test suite green (prior sprints unchanged; v5 new tests
    pass). Commit, tag `sprint-v5`, push.
  - Files: `risk/portfolio.py`, `notebooks/05_backtest.ipynb`,
    `sprints/v5/notes.md`, `sprints/v5/WALKTHROUGH.md`,
    `sprints/v5/plots/01_ab_equity.png`,
    `sprints/v5/plots/02_failure_slide.png`.
  - Validation: fails the sprint if C25/C26 fail (engine broken),
    if the notebook errors on re-run, if any prior-sprint test
    regresses, or if the walkthrough omits a required section.
    C27–C31 are pre-registered — their PASS or FAIL is recorded
    honestly and does not block the tag; a rejected thesis that is
    correctly measured and documented is a successful sprint.
