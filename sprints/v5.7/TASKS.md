# Sprint v5.7 — Tasks

Seven tasks. Hard gates at T2 (C32/C33 stationarity) and T4 (M1ʹ Sharpe).
If either gate fails, stop and write a failure note; do not proceed further.

Status starts `[ ]`; flip to `[x]` as each task lands.

**Dependency order:** T1 (characterise vs RV1_A) → T2 (stationarity, hard gate) →
T3 (DV01 audit, pre-register sizing) → T4 (backtest, hard gate) →
T5 (subperiod + grid) → T6 (bootstrap vs passive) → T7 (notebook + close).

---

- [ ] **Task T1: Signal characterisation — hy_ig vs RV1_A**
  - Load `hy_ig_z252` and `z_rv_hy_ig` from `features.parquet`.
    Compute:
    (a) Pearson correlation `corr(z_rv_hy_ig, hy_ig_z252)` over the overlapping
        non-NaN period.
    (b) Entry overlap: from the RV1_A trade ledger (94 trades), how many have an
        entry date within ±2 days of a date where `|hy_ig_z252| > 2`? Report as
        a fraction of 94.
    (c) Distribution of `hy_ig_z252` at RV1_A entry dates: plot histogram and
        report mean and std.
    (d) Count dates where `|hy_ig_z252| > 2` that do NOT overlap with any RV1_A
        entry ±2 days — these are the "new" signals hy_ig would fire that RV1_A missed.
    Print the four results. No trade/backtest in this task.
  - Acceptance: all four metrics printed. Scatter plot of `z_rv_hy_ig` vs
    `hy_ig_z252` saved to `sprints/v5.7/plots/z_overlap.png`. Overlap fraction
    and correlation explicitly stated.
  - Files: `sprints/v5.7/plots/z_overlap.png`, `sprints/v5.7/notes.md`
  - Validation: fails if correlation is not computed; fails if overlap fraction
    uses a different window than ±2 days.

- [ ] **Task T2: Stationarity and OU half-life — hard gate (C32, C33)**
  - Run three ADF tests on `hy_ig` levels (not z-score): full sample, 2007–2016,
    2017–2026. Use `statsmodels.tsa.stattools.adfuller` with `autolag='AIC'`.
    Report test statistic, p-value, and critical values (1%, 5%, 10%) for each.
    C32 passes if p < 0.05 in all three windows.
  - Fit the OU half-life via discrete AR(1): `hy_ig[t] = κ·hy_ig[t-1] + c + ε[t]`.
    Half-life = `−ln(2) / ln(κ)`. Report half-life in trading days.
    C33 passes if half-life ≤ 90 trading days on the full sample.
  - Plot: `hy_ig` level over time with rolling 252d mean overlaid, saved to
    `sprints/v5.7/plots/hyig_level.png`. Indicate the two half-period boundaries.
  - **If C32 or C33 fails: write a one-paragraph failure note in `notes.md`,
    mark T2 [x], and stop the sprint.**
  - Acceptance: ADF table printed (3 rows × 4 cols). Half-life printed.
    C32 and C33 verdicts explicitly stated.
  - Files: `sprints/v5.7/plots/hyig_level.png`, `sprints/v5.7/notes.md`
  - Validation: fails if ADF is run on the z-score instead of the raw level;
    fails if C32/C33 verdict is not printed; fails if subperiod ADF is omitted.

- [ ] **Task T3: Rate exposure audit + pre-register sizing path (C34)**
  - Compute the net DV01 of a dollar-equal long HYG / short LQD position:
    - Use published effective durations: `dur_HYG = 3.5y`, `dur_LQD = 8.5y`
      (document source in notes.md; these are approximations from ETF fact sheets).
    - `DV01_net = (dur_HYG − dur_LQD) × notional × 1bp = (3.5 − 8.5) × $1M × 0.0001`
    - Report DV01_net in dollars per basis point.
  - Run a parallel-shift attribution: for each RV1_A trade (as a proxy for expected
    trade dates), compute `rate_pnl = DV01_net × Δdgs10_hold × 100` (Δdgs10 in
    decimal → multiply by 100 to get bp). Also compute `quality_pnl = side × Δhy_ig
    × notional` for the same trades. Report mean and total rate_pnl vs quality_pnl.
    C34 passes if `|total rate_pnl / total quality_pnl| ≤ 0.30`.
  - **Pre-register sizing choice before running T4:** write one line in `notes.md`:
    "v5.7 backtest uses Path A (DV01-neutral)" or "Path B (dollar-equal, explicit
    attribution)". This must be written before T4 executes. The recommendation from
    the PRD is to start with Path B for comparability, but the choice is yours.
    If C34 fails (rate attribution > 30%), Path A must be used in T4.
  - Acceptance: DV01_net printed. Rate vs quality P&L table printed.
    C34 verdict stated. Sizing path pre-registered in notes.md.
    Bar chart of mean rate_pnl vs mean quality_pnl per trade saved to
    `sprints/v5.7/plots/dv01_audit.png`.
  - Files: `sprints/v5.7/plots/dv01_audit.png`, `sprints/v5.7/notes.md`
  - Validation: fails if DV01_net is not computed before the backtest runs;
    fails if the sizing path is not pre-registered in notes.md before T4;
    fails if Δdgs10 is computed using exit date only (must span the hold period).

- [ ] **Task T4: Corrected-engine backtest — M1ʹ–M6ʹ scorecard (hard gate)**
  - Run the hy_ig strategy using the corrected daily MTM engine:
    ```python
    # Use hy_ig directly as the "residual" — no OLS hedge ratio
    daily[d] += side × (hy_ig[d] − hy_ig[d−1]) × notional − borrow_per_day
    daily[entry_fill] -= spread_slippage
    ```
    Implement via `StrategySpec` if a `hy_ig` pair is supported, or construct
    the daily P&L series directly from the hy_ig series and trade dates
    (same approach as the v6.5 T1 fixed-entry reconstruction).
    If Path A was chosen in T3: adjust LQD leg size to `N_LQD = N_HYG × (3.5/8.5)`.
  - Report M1ʹ–M6ʹ: Sharpe, hit rate, max single-trade share, n_trades,
    total net P&L, max drawdown.
  - **If M1ʹ (Sharpe ≥ 0.40) fails: write a failure note in `notes.md`,
    mark T4 [x], and stop the sprint.**
  - Acceptance: all M1ʹ–M6ʹ metrics printed. M1ʹ verdict explicitly stated.
    Equity curve saved to `sprints/v5.7/plots/equity_hyig.png`.
  - Files: `sprints/v5.7/plots/equity_hyig.png`, `sprints/v5.7/notes.md`
  - Validation: fails if the engine uses a rolling β (OLS residual) instead of
    the raw hy_ig series; fails if Path A/B mismatch with T3 pre-registration;
    fails if the daily P&L assigns P&L to entry date (must be daily MTM).

- [ ] **Task T5: Subperiod stability + parameter grid (M4ʹ, M5ʹ)**
  - Subperiod: split at 2016-12-31 (pre-2017 = zero-rate era, post-2017 = rate
    normalisation era). Report Sharpe, hit rate, n_trades, total net P&L for each
    half. Both must show Sharpe > 0 (M5ʹ).
  - Parameter grid: sweep the 27-cell grid (entry ∈ {1.5, 2.0, 2.5} × exit ∈
    {0.3, 0.5, 0.75} × stop ∈ {3.0, 4.0, 5.0}). For each cell report Sharpe.
    Count cells with Sharpe > 0 (M4ʹ: must be ≥ 60% = 17/27 cells).
    Save heatmap of Sharpe by entry×exit (averaging over stop) to
    `sprints/v5.7/plots/param_grid.png`.
  - Acceptance: subperiod table printed, M5ʹ verdict stated. Grid heatmap saved,
    M4ʹ verdict (N/27 cells > 0) printed.
  - Files: `sprints/v5.7/plots/param_grid.png`, `sprints/v5.7/notes.md`
  - Validation: fails if the subperiod split uses a different date than 2016-12-31;
    fails if the grid uses the biased engine (must match T4's engine choice).

- [ ] **Task T6: Bootstrap vs passive buy-hold hy_ig (C35)**
  - Define the passive benchmark: a strategy that is always long hy_ig (long HYG,
    short LQD) at the same notional as T4, for every trading day in the sample,
    with no entry/exit timing. Its daily P&L is `(hy_ig[d] − hy_ig[d−1]) × notional
    − borrow_per_day`. Compute its Sharpe and equity curve.
  - Compute incremental Sharpe `ΔS = Sharpe(hy_ig_timed) − Sharpe(hy_ig_passive)`
    using stationary block bootstrap (block=21, n=1000, seed=20260613).
    Report ΔS, 2.5/97.5 CI, fraction of resamples with ΔS > 0.
    C35 passes if ΔS > 0 and CI lower bound > 0.
  - Plot: equity curves of timed vs passive on the same axes saved to
    `sprints/v5.7/plots/vs_passive.png`.
  - Acceptance: ΔS and CI printed, C35 verdict stated. Plot saved.
  - Files: `sprints/v5.7/plots/vs_passive.png`, `sprints/v5.7/notes.md`
  - Validation: fails if the passive benchmark uses any timing signal; fails if
    bootstrap is not seeded; fails if C35 verdict is absent from output.

- [ ] **Task T7: Notebook + sprint close**
  - Write `scripts/build_notebook_v5_7.py` and produce
    `notebooks/05_7_hyig_validation.ipynb`. Four sections:
    (1) Signal characterisation vs RV1_A (T1 — overlap, correlation, scatter)
    (2) Stationarity and rate exposure (T2/T3 — ADF table, OU half-life, DV01 audit)
    (3) Backtest results (T4/T5 — M1ʹ–M6ʹ scorecard, equity curve, param grid)
    (4) Bootstrap vs passive (T6 — C35 verdict, equity overlay)
    Execute via `jupyter nbconvert --execute --inplace`. Zero cell errors.
    Last cell prints `[notebook clean]`.
  - If all hard gates passed: write `sprints/v5.7/signal_selection.md` mirroring
    v5.6 format — M1ʹ–M6ʹ scorecard, sizing path, DV01 note, Bonferroni note
    (fourth signal tested), statement that Tier 2 sequence v6–v10 is now retargeted
    on hy_ig.
  - If any hard gate failed: the notebook is a diagnostic record only. Write a
    one-paragraph conclusion in `notes.md` explaining what failed and what the
    honest state of the research program is.
  - Acceptance: notebook executes clean. All hard-gate verdicts (C32, C33, M1ʹ,
    C35) visible in output. `signal_selection.md` or failure note written.
  - Files: `notebooks/05_7_hyig_validation.ipynb`,
    `scripts/build_notebook_v5_7.py`,
    `sprints/v5.7/signal_selection.md` (if passes) or note in `notes.md`
  - Validation: fails if any cell errors; fails if C32/C33/M1ʹ/C35 verdicts
    are absent from output; fails if the sizing path from T3 is not stated.
