# Sprint v8.3 - Tasks

**Sprint closed. T1-T10 all done.** See notes.md for full reconciliation
results, standout forensic findings (carry is 70% of gross P&L; GLD leads
all sleeves; Decomposition 4's beta-explained component is net negative
while the exposure-timing residual carried the positive P&L), and the
gate-status table (G1 N/A by design, G2 PASS, E1' PASS, R1-R7 PASS).

Status: `[ ]` = not done, `[x]` = done.

**Dependency order:** T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10.
T1 is a hard gate on G2 for Decomposition 5 specifically. G1 is not a probe
in this sprint (see notes.md decision log) -- the intermediary capital
factor is omitted from Decomposition 4 by design, so T4 does not depend on
T1 at all.

---

- [x] **Task T1: Data probe -- dividend history (G2)**
  - G2: fetch per-ticker dividend/distribution history for all 8 ETFs via
    the existing yfinance boundary (a new function, not a reuse of
    `signals.load.fetch`'s `actions=False` call). Validate: non-negative,
    sparse (mostly zero), at least one nonzero distribution per ticker
    over the sample.
  - No G1 probe in this task. The He-Kelly-Manela intermediary capital
    factor is omitted from Decomposition 4 by design (House Rule 8) --
    its frequency mismatch with this daily attribution is the reason for
    omission regardless of what might be retrievable, so no retrieval is
    attempted.
  - Acceptance: G2 verdict stated explicitly with evidence (coverage
    table, source, method). `data/raw/{ticker}_dividends.parquet` x8
    written.
  - Files: `signals/dividends.py`, `data/raw/*_dividends.parquet`,
    `sprints/v8.3/notes.md`
  - Validation: fails if a synthetic/interpolated proxy is silently
    substituted for G2 instead of documenting the real outcome; fails if
    any code in this task attempts to fetch or construct an intermediary
    capital series of any kind.

- [x] **Task T2: Per-instrument / per-asset-class P&L (1) + long/short split (2)**
  - Implement `backtest/attribution.py::per_instrument_pnl` and
    `per_asset_class_pnl` (using the v8.1 universe table's asset-class
    grouping) and `long_short_pnl`, applied to the v8.2 closing book.
  - Acceptance: R1 and R2 reconciliation printed (residual ≤ 1e-6 ×
    notional, every day). Per-asset-class P&L plot saved.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/pnl_by_sleeve.png`
  - Validation: fails if any ticker is double-counted or omitted from the
    asset-class grouping; fails if R1/R2 residual exceeds tolerance on any
    day.

- [x] **Task T3: Directional vs selection split (3)**
  - Implement using the equal-weighted 8-name market proxy (the same
    basket as v8.2's T8 buy-and-hold baseline). Compute `directional(t)`
    and `selection(t) = gross_pnl(t) - directional(t)`.
  - Acceptance: R3 reconciliation printed; cumulative directional vs
    selection P&L plotted over the sample.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/directional_vs_selection.png`
  - Validation: fails if a different market proxy is used without being
    pre-registered first; fails if `selection` is computed any way other
    than as the residual of `directional`.

- [x] **Task T4: Factor regression (4) -- the sprint's one genuinely empirical output**
  - Rolling 252-day OLS, refit daily, point-in-time (data through `t-1`
    only) on exactly four daily, exposure-matched factors: `SPY_ret`,
    `IEF_ret`, `HYG_ret - IEF_ret`, `GLD_ret`. No intermediary capital
    term, no monthly factor of any kind (House Rule 8) -- this is fixed,
    not conditional on any probe result. Compute `beta_explained(t)` and
    `residual(t)`.
  - Acceptance: R4 reconciliation printed. Rolling betas and rolling R^2
    plotted over time. **The residual is explicitly labelled
    "exposure-timing, not security selection, not alpha" in the plot
    caption, the printed output, and notes.md** -- checked as part of
    acceptance, not assumed.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/factor_betas.png`,
    `sprints/v8.3/plots/factor_r_squared.png`
  - Validation: fails if any beta used to explain day `t` was fit using
    data from `t` or later (look-ahead in the regression); fails if the
    residual is described anywhere as selection or alpha; fails if any
    monthly or lower-frequency series enters the regression in any form,
    interpolated or otherwise.

- [x] **Task T5: Carry vs price change (5)**
  - Using G2's dividend/distribution data, compute `carry_i(t)` and
    `price_change_i(t) = pnl_i(t) - carry_i(t)` per instrument.
  - Acceptance: R5 reconciliation printed per instrument. Aggregate carry
    as a fraction of total gross P&L reported. Distribution-date lumpiness
    visible and explained in the plot caption, not hidden by smoothing.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/carry_vs_price.png`
  - Validation: fails if carry is smoothed/amortized across non-payment
    days instead of booked on the actual distribution date.

- [x] **Task T6: Gross/net/turnover-cost re-exposure (6)**
  - Re-expose `backtest.multi_asset.run_multi_asset`'s existing
    `turnover_cost`/`borrow_cost`/`daily_pnl` per day, in bps of notional,
    rather than recomputing independently.
  - Acceptance: R6 reconciliation printed (should be exact to machine
    precision, since this is the same formula re-read, not re-derived).
    Gross vs net cumulative P&L plotted with the cost drag shaded.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/gross_vs_net.png`
  - Validation: fails if costs are recomputed via a separate formula
    instead of re-exposing `run_multi_asset`'s own values (would defeat
    the point of R6 being exact by construction).

- [x] **Task T7: Marginal contribution to vol by sleeve, ex-ante vs realized (7)**
  - Implement the Euler MCTR decomposition for the four asset-class
    sleeves, using a trailing-63d covariance matrix: ex-ante (`t-63..t-1`)
    and realized (`t-62..t`).
  - Acceptance: R7 reconciliation printed for both ex-ante and realized
    (residual ≤ 1e-6 × σ_portfolio). Sleeve MCTR ranking plotted, ex-ante
    vs realized side by side.
  - Files: `backtest/attribution.py`, `sprints/v8.3/plots/mctr_by_sleeve.png`
  - Validation: fails if the realized-window hindsight use is not
    explicitly captioned as a forensic diagnostic, not a trading input.

- [x] **Task T8: Reconciliation test suite (R1-R7, E1')**
  - One pytest module covering every reconciliation gate (R1-R7) and the
    look-ahead re-verification (E1') for the attribution layer
    specifically (perturb data after a cutoff, confirm every decomposition
    above the cutoff is unchanged).
  - Acceptance: all tests pass; this is the sprint's actual
    correctness-proof, not the notebook output.
  - Files: `tests/test_attribution.py`
  - Validation: fails if any reconciliation tolerance is loosened beyond
    1e-6 x notional (or x sigma_portfolio for R7) to make a test pass.

- [x] **Task T9: Sanity baseline -- hand-verify 2-3 known dates**
  - Pick 2-3 specific dates (e.g. a high-turnover day, a distribution-date
    for some ticker, a date inside a known vol regime shift) and manually
    recompute all seven decompositions for those dates by hand /
    independent script, compare to the pipeline's output.
  - Acceptance: hand-computed and pipeline values match within the same
    1e-6 tolerance, printed side by side for each chosen date.
  - Files: `sprints/v8.3/notes.md`
  - Validation: fails if the "hand" computation reuses the same code path
    being validated (must be an independently written recomputation).

- [x] **Task T10: Notebook + sprint close**
  - `notebooks/08_3_attribution.ipynb`: all seven decompositions, their
    reconciliation checks, and every plot from T2-T7, executing clean.
  - S3 guardrail written verbatim in `notes.md`. Gate-status table
    (G1, G2, E1', R1-R7, S3) in the close-out.
  - Files: `notebooks/08_3_attribution.ipynb`,
    `scripts/build_notebook_v8_3.py`, `sprints/v8.3/notes.md`
  - Validation: fails if the guardrail is paraphrased; fails if any
    decomposition's residual is reported without its tolerance stated
    alongside it.
