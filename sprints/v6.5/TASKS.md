# Sprint v6.5 — Tasks

Seven tasks (T1–T7). Hard gate: **T1 must pass R1 and R2 before any other task runs.**
If T1 fails, stop and reassess Tier 1. Do not proceed to T2–T7.

Status starts `[ ]`; flip to `[x]` as each task lands.

**Dependency order:** T1 (rescue test, gate) → T2 (engine fix) → T3 (verify fix) →
T4 (re-run all signals) → T5 (compare before/after) → T6 (notebook) → T7 (register).

---

- [ ] **Task T1: Rescue test — fixed-entry P&L for RV1_A (hard gate)**
  - Load `sprints/v6/attribution_table.csv` (94 trades, already has `delta_hy`,
    `delta_ig`, `hedge_ratio_entry`, `cost`). Compute:
    ```python
    gross_fixed = side × (delta_hy − hedge_ratio_entry × delta_ig) × 1_000_000
    net_fixed   = gross_fixed − cost
    ```
    Assign `net_fixed` to `exit_fill_date` to reconstruct a daily P&L series.
    Compute: Sharpe (annualised), hit rate, n_trades, total net P&L, max drawdown.
    Compare side-by-side with v5 registered numbers.
    Evaluate R1 (Sharpe ≥ 0.40) and R2 (hit rate ≥ 60%).
    **If R1 or R2 fails: write a one-paragraph failure note to `notes.md`, mark T1 [x],
    and stop the sprint. Do not proceed to T2.**
  - Acceptance: side-by-side table printed (old vs corrected). R1 and R2 verdicts
    explicitly stated. Bar chart saved to `sprints/v6.5/plots/rescue_pnl_comparison.png`
    showing old vs corrected cumulative P&L on the same axes.
  - Files: `sprints/v6.5/plots/rescue_pnl_comparison.png`, `sprints/v6.5/notes.md`
  - Validation: fails if `gross_fixed` uses hedge_ratio_exit instead of
    hedge_ratio_entry; fails if R1/R2 verdict is not explicitly printed; fails if
    the daily P&L series assigns P&L to entry date instead of exit date.

- [ ] **Task T2: Engine fix — add fixed-entry gross computation**
  - Modify `backtest/engine.py`. Add optional parameters `y_series: pd.Series | None`
    and `x_series: pd.Series | None` to the `run()` function. When both are provided,
    compute exit residual using **entry-date** hedge ratio:
    ```python
    # inside trade loop, at exit:
    if y is not None and x is not None:
        gross = sign * ((y[exit_fill] - y[entry_fill])
                        - hr[entry_fill] * (x[exit_fill] - x[entry_fill])) * notional
    else:
        gross = sign * (rv_exit - rv_entry) * notional  # unchanged fallback
    ```
    No other changes. Existing tests must still pass.
  - Acceptance: engine.py modified. The old call signature (no y/x args) produces
    identical results to pre-fix. Print diff of changed lines only.
  - Files: `backtest/engine.py`
  - Validation: fails if the fallback path changes; fails if `hr[entry_fill]` is not
    used (must be entry hedge ratio, not exit); fails if existing engine behaviour
    changes for callers that don't pass y/x.

- [ ] **Task T3: Verify engine fix matches rescue test**
  - Run the fixed engine for RV1_A by passing `hy_spread` and `ig_spread` series.
    Compare per-trade `gross_pnl` from the fixed engine vs `gross_fixed` from T1.
    Maximum per-trade absolute difference must be < $1 (floating-point only).
    Print the max difference and assert < $1.
  - Acceptance: assertion passes. Print "engine fix verified: max diff $X" where X < 1.
  - Files: `sprints/v6.5/notes.md`
  - Validation: fails if any trade-level difference exceeds $1.

- [ ] **Task T4: Re-run all three signals with fixed engine**
  - Run `build_strategy(features, residuals, StrategySpec(pair,'ols',gated=False))`
    with the fixed engine for RV1_A, RV2_A, RV3_A. Pass the correct y/x series for
    each pair:
    - RV1_A: y=hy_spread, x=ig_spread
    - RV2_A: y=hy_spread, x=dgs10 (from credit_market_data.parquet, reindexed)
    - RV3_A: y=hy_ig (= hy_spread − ig_spread), x=slope (= dgs10 − dgs2)
    For each signal record: Sharpe, hit rate, n_trades, total net P&L, max drawdown.
    Evaluate R3 (RV2_A and RV3_A Sharpe ≥ 0.40) and R4 (ranking preserved).
  - Acceptance: table of all three signals printed with corrected numbers. R3 and R4
    verdicts stated.
  - Files: `sprints/v6.5/notes.md`
  - Validation: fails if y/x series are not correctly matched to each pair; fails if
    the old (unfixed) engine is used for any signal.

- [ ] **Task T5: Before/after comparison — all three signals**
  - Build a side-by-side table: for each signal, v5/v5.6 registered number vs corrected
    number for Sharpe, hit rate, total net P&L, max drawdown. Compute the delta for each.
    Flag any signal whose corrected Sharpe drops below 0.40 (R3 fail).
    Save equity curve comparison plots to `sprints/v6.5/plots/`:
    - `equity_rv1_before_after.png` — old vs corrected cumulative P&L for RV1_A
    - `equity_all_corrected.png` — all three corrected equity curves on one axes
  - Acceptance: comparison table printed. Both plots saved. Deltas reported.
  - Files: `sprints/v6.5/plots/equity_rv1_before_after.png`,
    `sprints/v6.5/plots/equity_all_corrected.png`, `sprints/v6.5/notes.md`
  - Validation: fails if the "old" numbers don't match v5/v5.6 registered values;
    fails if any signal is missing from the comparison.

- [ ] **Task T6: Notebook `06_5_engine_correction.ipynb`**
  - Write `scripts/build_notebook_v6_5.py` and produce
    `notebooks/06_5_engine_correction.ipynb`. Four sections:
    (1) Rescue test: fixed-entry P&L for RV1_A — old vs corrected, R1/R2 verdict
    (2) Engine fix verification: per-trade diff between T1 formula and fixed engine
    (3) All three signals corrected — side-by-side table and equity curves
    (4) Summary scorecard: old M1 (Sharpe >0.40) vs corrected M1, pass/fail for each
    Execute via `jupyter nbconvert --execute --inplace`. Zero cell errors.
    Last cell prints `[notebook clean]`.
  - Acceptance: notebook executes clean. R1/R2/R3/R4 verdicts visible in output.
    Before/after comparison table visible. All plots rendered inline.
  - Files: `notebooks/06_5_engine_correction.ipynb`,
    `scripts/build_notebook_v6_5.py`
  - Validation: fails if any cell errors; fails if any R verdict is absent from output.

- [ ] **Task T7: Register corrected numbers + sprint close**
  - Write `sprints/v6.5/correction_summary.md` with:
    (a) Root cause explanation: why model drift is not real P&L
    (b) Corrected M1–M8 scorecard for all three signals under fixed-entry accounting
    (c) Explicit statement: "These numbers supersede all prior v5/v5.6 registered
        numbers from this date forward. v7 and v8 will use the corrected engine."
    (d) If R4 holds (rankings preserved): confirm admission decisions from
        `sprints/v5.6/signal_selection.md` remain valid under corrected accounting.
    (e) If R4 fails: state which admission decisions need revision.
    Finalise `sprints/v6.5/notes.md`. Mark all tasks [x].
  - Acceptance: correction_summary.md exists with all five sections. Every number
    has a source (either "computed in T1" or "computed in T4"). notes.md complete.
  - Files: `sprints/v6.5/correction_summary.md`, `sprints/v6.5/notes.md`
  - Validation: fails if corrected numbers are not stated explicitly; fails if the
    "supersedes" statement is absent.
