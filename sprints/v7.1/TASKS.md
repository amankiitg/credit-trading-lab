# Sprint v7.1 — Tasks

**Sprint closed at T1. G0a FAILED — daily NAV for HYG/LQD is not retrievable via a
free, scriptable endpoint. T2–T8 are superseded and not executed.**

Status: `[x]` = done, `[~]` = superseded/skipped per gate protocol.

**Dependency order:** T1 → T2 → T3 (sequential hard gate: G0a/G0b/G0c) → T4 → T5 → T6 → T7 → T8.
**If T1, T2, or T3 fails its gate, stop the sprint there.** Mark the failed task `[x]`
with the gate verdict documented in `notes.md`, mark all remaining tasks `[~]`, and do
not proceed.

---

- [x] **Task T1: NAV data-availability probe (G0a)**
  - Determine whether at least 5 years of daily NAV is freely retrievable for HYG
    (iShares product 239565) and LQD (iShares product 239566). Try the official
    iShares fund "performance"/NAV history export first; document the exact source,
    URL pattern, and method used (or attempted and failed).
  - If retrievable: persist to `data/raw/HYG_nav.parquet` and `data/raw/LQD_nav.parquet`
    (columns: date index, `nav`). Print row count and date range for each.
  - Acceptance: G0a verdict (PASS/FAIL) stated explicitly for each ticker with the
    trading-day count. If FAIL for either ticker, write the failure note per the PRD
    ("not testable on free data") and stop — do not proceed to T2.
  - Files: `data/raw/HYG_nav.parquet`, `data/raw/LQD_nav.parquet`, `sprints/v7.1/notes.md`
  - Validation: fails if the data source/method is not documented; fails if fewer
    than 1260 trading days are retrieved for either ticker but the task is marked
    PASS anyway.

- [~] **Task T2: Date-alignment check (G0b) — superseded, G0a closed the sprint first**
  - Merge each ticker's NAV series with `data/raw/{HYG,LQD}.parquet` close prices on
    the date index. Compute daily return correlation between NAV and close at lags
    −2..+2 trading days, for both tickers.
  - Acceptance: lag-correlation table printed for both tickers; G0b verdict stated.
    Gate fails if the correlation argmax is not at lag 0 for either ticker. If it
    fails, attempt exactly one documented corrective realignment (e.g. shift NAV by
    one trading day) and retest once. If still failing, stop and document.
  - Files: `sprints/v7.1/plots/lag_corr.png`, `sprints/v7.1/notes.md`
  - Validation: fails if correlation is computed on price/NAV levels instead of
    returns (non-stationary series inflate spurious correlation at every lag);
    fails if the lag range tested does not include 0.

- [~] **Task T3: End-of-day striking spot-check (G0c) — superseded**
  - Cross-check NAV values on ≥ 3 specific dates — including at least one date
    inside the 2020-03 stress window — against an independently sourced end-of-day
    NAV reference (e.g. an iShares fact sheet PDF, or a second vendor). Confirm the
    retrieved series is the official EOD NAV, not an intraday indicative value
    (IIV/INAV).
  - Acceptance: spot-check table (date, our NAV, reference NAV, diff) printed for
    all checked dates; G0c verdict stated explicitly.
  - Files: `sprints/v7.1/notes.md`
  - Validation: fails if the "independent" reference is actually the same
    source/vendor as T1; fails if no date inside the 2020-03 window is checked.

- [~] **Task T4: Persist merged dataset + hygiene assertions — superseded**
  - Combine NAV and close into one dataframe per ticker on a shared trading-date
    index. Assert: no NaN/inf, monotonic unique index, date range matches the
    close-price series. Log any row drops explicitly (no silent drops).
  - Acceptance: all assertions printed and passing; row count and date range stated.
  - Files: `data/processed/nav_wedge_inputs.parquet`
  - Validation: fails if assertions are not explicit in code (no eyeballing); fails
    if any date mismatch between NAV and close is silently dropped without a logged count.

- [~] **Task T5: Construct wedge_t and z_wedge_t — code built ahead of data, not run on real NAV**
  - `signals/nav_wedge.py` implements `compute_wedge` and `compute_z_wedge` (63d,
    pre-registered, no regression) exactly as specified below, and is unit-tested
    on synthetic fixtures in `tests/test_nav_wedge.py`. It has not been run against
    real NAV because none exists (G0a FAIL). Ready to use the moment a NAV source
    is identified.
  - Compute `wedge = close/nav − 1` for HYG and LQD. Compute `z_wedge` over a fixed
    trailing 63-day window (mean/std), as pre-registered in the PRD. State in
    `notes.md`, before looking at any output, that 63d is fixed for this sprint and
    will not be retuned.
  - Acceptance: wedge and z_wedge series saved; summary stats (mean, std, skew,
    kurtosis) printed for both tickers; distribution histogram saved.
  - Files: `data/processed/nav_wedge_inputs.parquet` (append columns) or a new
    `data/processed/wedge_features.parquet`, `sprints/v7.1/plots/wedge_distribution.png`
  - Validation: fails if any window other than 63d is used; fails if any
    regression/OLS/Kalman/fitted-intercept step appears anywhere in the construction
    (House Rule 2 violation — automatic fail regardless of other results).

- [~] **Task T6: Leakage / look-ahead check — verified at the code level only**
  - `tests/test_nav_wedge.py::test_z_wedge_no_lookahead` asserts the rolling window
    is right-aligned and that perturbing future values leaves past z_wedge values
    unchanged. The NAV-restatement check cannot run without a real NAV series.
  - Confirm `z_wedge_t` uses only wedge values up to and including `t` (right-aligned
    rolling window, not centered). Confirm the NAV value used for date `t` is the
    value as originally published for `t` — check for and document any known
    restatement risk identified in T1/T3.
  - Acceptance: explicit assertion/test that the rolling window is right-aligned
    (`pandas.rolling()` default; `center=False`); restatement check documented even
    if the answer is "none found."
  - Files: `sprints/v7.1/notes.md`
  - Validation: fails if the rolling window is centered or otherwise uses future data.

- [~] **Task T7: S1a — stress-episode sign/magnitude sanity check — superseded, no real wedge series to plot**
  - Plot `wedge_hyg` and `wedge_lqd` (with `z_wedge` overlay) through 2020-03 and
    2022. Report the sign and magnitude at the trough of each episode, for both
    tickers (4 numbers total). State explicitly whether the observed behaviour
    matches the "discount during liquidity stress" prior from the PRD. This is the
    sprint's sanity-baseline check (no quantitative backtest is in scope) — it
    calibrates whether the signal behaves as economically expected against two
    known historical episodes.
  - Acceptance: plot saved; all 4 sign/magnitude numbers reported; explicit
    match/mismatch statement for each episode (informational, not a gate).
  - Files: `sprints/v7.1/plots/stress_episodes.png`, `sprints/v7.1/notes.md`
  - Validation: fails if either episode or either ticker is omitted from the report.

- [x] **Task T8: S1b guardrail statement + sprint close**
  - Write the S1b guardrail verbatim in `notes.md`: "z_wedge stationarity, if
    observed, is not evidence of tradeability. No IC test, no backtest, and no
    Sharpe/hit-rate claim is in scope for v7.1." Optionally run ADF/OU half-life on
    `z_wedge` as a purely informational diagnostic (clearly labeled as such, not a
    gate). Close the sprint with an explicit gate-status table (G0a, G0b, G0c, S1a,
    S1b) and state the v7.2 scope (IC test on z_wedge) as contingent on this
    sprint's gates having passed.
  - Acceptance: guardrail statement present verbatim; gate-status table present;
    next-sprint scope stated.
  - Files: `sprints/v7.1/notes.md`
  - Validation: fails if the guardrail statement is paraphrased rather than stated
    verbatim, or if any IC/backtest/Sharpe claim appears anywhere in this sprint's
    output despite being out of scope.
