# Sprint v6.6 — Tasks

**Sprint closed at T2. C36 IC gate failed — entry signal is a coin flip.
T3–T6 (backtest path) are superseded and not executed.
T7 (closing notebook) executed as failure documentation.**

Status: `[x]` = done, `[~]` = superseded/skipped per gate protocol.

**Dependency order:** T1 → T2 (hard gate: C32/C33/C36) → T3–T6 skipped → T7 (close).

---

- [x] **Task T1: Signal characterisation — hy_ig vs RV1_A**
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
    `hy_ig_z252` saved to `sprints/v6.6/plots/z_overlap.png`. Overlap fraction
    and correlation explicitly stated.
  - Files: `sprints/v6.6/plots/z_overlap.png`, `sprints/v6.6/notes.md`
  - Validation: fails if correlation is not computed; fails if overlap fraction
    uses a different window than ±2 days.

- [x] **Task T2: Stationarity, OU half-life, and IC test — hard gate (C32, C33, C36)**
  - **C32** — ADF on `Δhy_ig` (first differences), full sample only.
    Use `statsmodels.tsa.stattools.adfuller` with `autolag='AIC'`.
    C32 passes if p < 0.05. (Tests that daily P&L increments are finite-variance.)
  - **C33** — OU half-life on `hy_ig_z252` (the z-score, not the raw level).
    AR(1): `z[t] = κ·z[t-1] + c + ε[t]`. Half-life = `−ln(2)/ln(|κ|)`.
    C33 passes if half-life ≤ 90 trading days. (Tests that the entry signal
    reverts on a tradeable timescale.)
  - **C36** — IC test: does the entry signal predict the direction of the next
    price move? For each date t where `|hy_ig_z252[t]| > 2`, define:
    `hit_h = 1 if sign(z[t]) == sign(hy_ig[t+h] − hy_ig[t]) else 0`
    for h ∈ {5, 10, 20} trading days. Compute hit rate and one-sample t-stat
    (H0: hit rate = 50%) at each horizon across all entry dates.
    C36 passes if hit rate > 50% **and** t-stat > 1.5 at ≥ 2 of 3 horizons.
  - Plot: `hy_ig_z252` level over time with ±2σ bands, saved to
    `sprints/v6.6/plots/hyig_zscore.png`. Also save an IC decay bar chart
    (hit rate at h=5/10/20) to `sprints/v6.6/plots/ic_decay.png`.
  - **If any of C32, C33, C36 fails: write a failure note in `notes.md`,
    mark T2 [x], and stop the sprint.**
  - Acceptance: C32 ADF result printed. C33 half-life printed. C36 hit-rate
    and t-stat table printed (3 rows × 3 cols). All three verdicts explicitly stated.
  - Files: `sprints/v6.6/plots/hyig_zscore.png`, `sprints/v6.6/plots/ic_decay.png`,
    `sprints/v6.6/notes.md`
  - Validation: fails if C32 tests the raw level instead of first differences;
    fails if C33 tests raw level instead of the z-score; fails if C36 IC test
    is not computed; fails if any verdict is absent.

- [~] **Task T3: Rate exposure audit (skipped — C36 gate closed sprint)**
  - Superseded. C36 failed before reaching the backtest path.
    DV01 analysis documented in PRD.md §Rate Exposure for reference.

- [~] **Task T4: Corrected-engine backtest (skipped — C36 gate closed sprint)**
  - Superseded. No entry signal predictive content; running a backtest would
    not change the gate outcome.

- [~] **Task T5: Subperiod + parameter grid (skipped)**
  - Superseded.

- [~] **Task T6: Bootstrap vs passive (skipped)**
  - Superseded.

- [x] **Task T7: Closing notebook + programme close**
  - `notebooks/06_6_hyig_validation.ipynb` — 3 sections: signal characterisation
    (T1), stationarity + IC gate (T2: C32/C33/C36), programme conclusion.
    Executes clean. Last cell prints `[notebook clean]`.
  - `scripts/build_notebook_v6_6.py` — generator.
  - Programme conclusion written in `notes.md` and `README.md`.
  - Gate verdicts: C32 PASS, C33 PASS, **C36 FAIL** (0/3 horizons).
  - Files: `notebooks/06_6_hyig_validation.ipynb`, `scripts/build_notebook_v6_6.py`
