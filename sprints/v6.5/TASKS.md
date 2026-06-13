# Sprint v6.5 — Tasks

**Gate outcome: T1 FAILED (corrected Sharpe 0.202 < 0.40). Original T2–T7 (engine fix
path) are superseded. Sprint pivots to: Option C analysis, failure notebook, and
architecture recommendation for Option B.**

Status starts `[ ]`; flip to `[x]` as each task lands.

**Revised dependency order:** T1 (rescue test, gate) → T2 (Option C: all signals) →
T3 (failure notebook) → T4 (architecture close).

---

- [x] **Task T1: Rescue test — fixed-entry P&L for RV1_A (hard gate)**
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

- [x] **Task T2: Option C — fixed-entry rescue for RV2_A and RV3_A**
  - Apply the same daily MTM reconstruction from T1 to RV2_A and RV3_A.
    - RV2_A: y = hy_spread, x = dgs10 (from `data/raw/credit_market_data.parquet`, ffill)
    - RV3_A: y = hy_ig (= hy_spread − ig_spread), x = slope (= dgs10 − dgs2)
    Both reconciliations exact ($0 diff).
  - Results (computed 2026-06-13, see notes.md):
    - RV2_A: registered 0.693 → corrected **−0.108** (net P&L $995k → −$13.1M)
    - RV3_A: registered 0.856 → corrected **−0.187** (net P&L $990k → −$10.8M)
  - Plots: `sprints/v6.5/plots/option_c_all_signals.png`,
    `sprints/v6.5/plots/option_c_sharpe_comparison.png`
  - Verdict: all three signals fail R1. RV2/RV3 invert catastrophically due to
    rolling OLS absorbing secular rate trends. Architecture is invalid.

- [x] **Task T3: Failure notebook `notebooks/06_5_engine_correction.ipynb`**
  - Write `scripts/build_notebook_v6_5.py` and produce
    `notebooks/06_5_engine_correction.ipynb`. Three sections:
    (1) **RV1_A rescue test**: registered vs fixed-entry daily MTM. Side-by-side
        table, equity curve overlay, R1/R2 verdict. Plot inline.
    (2) **All-signals rescue (Option C)**: RV2_A and RV3_A fixed-entry results.
        Complete 3-signal scorecard table. Equity curves for all three signals
        (3-panel, registered vs corrected). Sharpe bar chart vs R1 threshold.
        R1 FAIL verdict for all three.
    (3) **Architecture conclusion + Option B roadmap**: explain why rolling OLS
        intercept creates model drift (1 paragraph). Introduce Option B:
        `hy_ig = hy_spread − ig_spread` z-scored, no β, no α. Show that
        `hy_ig_z252` already exists in features.parquet. Print a preview of
        the new signal's z-score distribution (histogram) and time-series plot.
        State: "All prior Tier 1 admission decisions are withdrawn. A new Tier 1
        sprint (v7) will validate Option B under honest accounting."
    Execute via `jupyter nbconvert --execute --inplace`. Zero cell errors.
    Last cell prints `[notebook clean]`.
  - Acceptance: notebook executes clean. All three R1 FAIL verdicts visible.
    All plots rendered inline. Option B signal preview present.
  - Files: `notebooks/06_5_engine_correction.ipynb`, `scripts/build_notebook_v6_5.py`
  - Validation: fails if any cell has an error; fails if any signal is missing
    from the scorecard; fails if Option B section is absent.

- [x] **Task T4: Architecture close — `sprints/v6.5/correction_summary.md`**
  - Write `sprints/v6.5/correction_summary.md` with:
    (a) Root cause: rolling OLS intercept α = mean_y − β×mean_x continuously
        re-centres the residual, booking "reversion" that is really model drift.
        Worst for rate-spread pairs (RV2, RV3) where secular trends are large.
    (b) Disqualification table: all three signals, registered vs corrected Sharpe,
        R1 verdict.
    (c) Explicit statement: "All v5/v5.6 admission decisions are withdrawn as of
        2026-06-13. No Tier 2 work (v6–v8) will proceed under OLS residual signals."
    (d) Option B specification: new signal = `hy_ig` z-scored over trailing 252 days,
        no OLS, no hedge ratio, entry |z|>2, exit |z|<0.5. Column `hy_ig_z252`
        already in features.parquet. The engine gross formula becomes
        `side × Δhy_ig × notional` — no model drift possible.
    (e) Next sprint: v7 is a new Tier 1 sprint for the hy_ig signal under corrected
        accounting. Same M1–M8 gates as v5.
    Finalise `sprints/v6.5/notes.md`. Mark T3 and T4 [x].
  - Acceptance: correction_summary.md has all five sections. Option B signal
    spec is precise (column name, window, threshold). Withdrawal statement present.
  - Files: `sprints/v6.5/correction_summary.md`, `sprints/v6.5/notes.md`
  - Validation: fails if the withdrawal statement is absent; fails if Option B
    spec doesn't name the column and parameters.
