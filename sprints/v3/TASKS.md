# Sprint v3 — Tasks

Ten atomic tasks, each 10–20 minutes of focused work. Every signal
task is paired with a test. Status starts at `[ ]`; mark `[x]` as
each lands. All tests cumulative: the final task requires C18–C24
green.

Pre-req: `sprint-v2` tag checked out (C++ pricer importable via
`pycredit`, features.parquet 50 cols, credit_market_data 15 cols).

See `PRD.md` §Falsification Criteria for C18–C24 definitions.

---

- [x] **Task V1: Regime classifiers — vol, equity, equity-credit lag (C18)**
  - Acceptance: `signals/regimes.py` exposes three pure functions:
    `vol_regime(df, window=63)`, `equity_regime(df, window=63)`,
    `equity_credit_lag(df, xcorr_window=21, max_lag=5, noise_floor=0.15)`.
    Each returns a `pd.Series` of categorical labels. `vol_regime`
    splits on expanding median of `SPY_vol_63`. `equity_regime` uses
    rolling 63d cumulative SPY return sign. `equity_credit_lag` uses
    cross-correlation of SPY returns vs `Δhy_spread` at lags -5..+5,
    assigns `equity_first`/`credit_first`/`neither`. All three are
    trailing-only (no look-ahead).
  - Files: `signals/regimes.py`.
  - Validation: fails if any function uses `center=True` or future
    data; fails if labels include NaN inside the post-warmup window;
    fails if the function is not pure (no disk I/O, no side effects).

- [x] **Task V2: Regime tests — coverage + non-degeneracy (C18)**
  - Acceptance: `tests/test_regimes.py` loads `features.parquet`,
    computes all three regime classifiers, and asserts: (a) > 95%
    non-null coverage post-warmup for each classifier; (b) no single
    label exceeds 70% of observations; (c) all expected labels are
    present (`{"high","low"}`, `{"bull","bear"}`,
    `{"equity_first","credit_first","neither"}`); (d) `equity_credit_lag`
    at `noise_floor=0.15` produces `neither` on at least 5% of days
    (noise floor is working). All 4 assertions map to C18.
  - Files: `tests/test_regimes.py`.
  - Validation: fails C18 if any assertion fails.

- [x] **Task V3: OLS rolling hedge — 3 RV families**
  - Acceptance: `signals/rv_signals.py` exposes `ols_hedge(y, x,
    window=126)` returning `(residual, hedge_ratio)` as two Series.
    Applied to: (1) `hy_spread ~ ig_spread` → `rv_hy_ig_ols`;
    (2) `hy_spread ~ dgs10/100` → `rv_credit_rates_ols`;
    (3) `hy_ig ~ (dgs10-dgs2)/100` → `rv_xterm_ols`. Hedge ratios are
    rolling OLS β from a 126-day trailing window. Residuals are
    NaN during warmup (first 252 + 126 = 378 days).
  - Files: `signals/rv_signals.py`.
  - Validation: fails if residual has NaN post-warmup (day 379+); fails
    if hedge ratio is constant (OLS is actually running); fails if
    residual variance > spread variance (hedge is amplifying, not
    reducing).

- [x] **Task V4: Kalman filter hedge — 3 RV families**
  - Acceptance: `signals/rv_signals.py` exposes `kalman_hedge(y, x,
    Q=1e-5, init_window=63)` returning `(residual, hedge_ratio)`.
    Implements a univariate linear Kalman filter: state `β_t` follows
    a random walk, observation is `y_t = α_t + β_t · x_t + ε_t`.
    Initial β from OLS on the first `init_window` rows. R estimated
    from OLS residual variance. Applied to the same 3 pairs as V3.
    No external dependency — implement the 1D Kalman inline (~25 lines).
  - Files: `signals/rv_signals.py` (extended).
  - Validation: fails if Kalman hedge ratio is identical to OLS (filter
    is not adapting); fails if residual variance is > 2x OLS residual
    variance (filter is diverging); fails if β_t contains NaN or inf
    post-initialization.

- [x] **Task V5: DV01-based hedge — 3 RV families (C++ pricer)**
  - Acceptance: `signals/rv_signals.py` exposes `dv01_hedge(features_df,
    credit_data_df, pycredit_module)` returning `(residual, hedge_ratio)`
    for each pair. For each trading day with a valid DGS curve,
    bootstraps a discount curve via `pycredit.bootstrap_discount()`,
    prices two proxy bonds (4y and 9y maturity, 5% coupon, semi-annual),
    extracts DV01 from `pycredit.price_bonds()`. Hedge ratio =
    `DV01_short / DV01_long`. For credit/rates (pair 2), uses CS01 of
    a 5y CDS proxy from `pycredit.price_cds()` vs DV01 of a 10y bond.
    For cross-term (pair 3), uses the DV01 ratio of 4y vs 9y bonds
    as a duration adjustment to the slope factor. Falls back to NaN
    on dates where DGS data is missing (forward-filled in credit_data).
  - Files: `signals/rv_signals.py` (extended).
  - Validation: fails if DV01 hedge ratio is negative for pair 1 (HYG
    and LQD durations are both positive); fails if more than 5% of
    post-warmup dates produce NaN (DGS coverage is > 95%); fails if
    the DV01 sweep takes > 60 seconds total (should be ~5s at 42k/s).

- [x] **Task V6: Best-method selection + features.parquet update**
  - Acceptance: For each RV family, compare ADF p-values of the three
    hedge methods; select the one with the lowest p-value as "best."
    Populate the 5 existing RV stub columns in `features.parquet`
    (`rv_hy_ig_residual`, `rv_credit_rates_residual`,
    `rv_xterm_residual`, `hedge_ratio_hy_ig`, `hedge_ratio_cr`) with
    the best-method values. Add 6 new columns: `vol_regime`,
    `equity_regime`, `equity_credit_lag` (categorical), and `z_rv_hy_ig`,
    `z_rv_credit_rates`, `z_rv_xterm` (float64, 63-day trailing
    z-score of the best residual). Update `signals/pipeline.py` to call
    the new modules. Final schema: 56 columns.
  - Files: `signals/pipeline.py` (extended), `signals/rv_signals.py`
    (extended).
  - Validation: fails if any of the 5 formerly-NaN stub columns is
    still all-NaN; fails if schema != 56 columns; fails if z_rv columns
    have NaN post-warmup+63 (day 315+); fails if Sprint 1 tests
    regress (25/25 still green).

- [x] **Task V7: Stationarity, cointegration, half-life tests (C19–C21)**
  - Acceptance: `tests/test_rv_signals.py` loads the updated
    `features.parquet` and asserts: (a) ADF p < 0.05 on all three
    best-method RV residuals (C19); (b) Engle-Granger cointegration
    p < 0.05 on each spread pair under at least one hedge method (C20);
    (c) OU half-life ∈ [1, 126] trading days for all three residuals
    (C21). `signals/halflife.py` implements `ou_halflife(series)` via
    the AR(1) regression `Δrv_t = a + b · rv_{t-1}`, returning
    `-ln(2)/b`. Also prints a comparison table: half-life per signal
    per method (OLS vs Kalman vs DV01).
  - Files: `signals/halflife.py`, `tests/test_rv_signals.py`.
  - Validation: fails C19 if any ADF p ≥ 0.05; fails C20 if any pair
    has no method with cointegration p < 0.05; fails C21 if any
    half-life is outside [1, 126] or infinite (b ≥ 0).

- [x] **Task V8: Regime-conditional quality table + thesis test (C22–C24)**
  - Acceptance: `signals/rv_signals.py` exposes
    `build_regime_quality_table(features_df, all_residuals_dict,
    regime_cols)` that computes, for each (signal, method, regime_classifier,
    regime_label) combination: half-life, z_magnitude = mean(|z_rv|),
    signal_freq = fraction(|z_rv| > 1.5), n_obs, adf_pvalue. Saves to
    `data/results/regime_signal_quality.parquet`. `tests/test_regime_quality.py`
    asserts: (a) C22 — RV1 half-life in `equity_first` is > 20% shorter
    than in `neither` (the thesis test); (b) C23 — rolling 63-day hedge
    ratio CV < 1.0 for all pairs x methods; (c) C24 — parquet exists
    with ≥ 27 rows and correct schema.
  - Files: `signals/rv_signals.py` (extended),
    `tests/test_regime_quality.py`, `data/results/regime_signal_quality.parquet`.
  - Validation: fails C22 if thesis test fails (half-life ratio < 20%);
    fails C23 if any hedge ratio CV ≥ 1.0; fails C24 if schema or row
    count wrong.

- [x] **Task V9: Sprint validation notebook — signal walkthrough**
  - Acceptance: `notebooks/03_rv_signals.ipynb` runs top-to-bottom and
    demonstrates: (a) Regime classification — time-series plot of all 3
    regime labels with shaded backgrounds; (b) OLS vs Kalman vs DV01
    hedge ratio evolution — 3 overlaid time-series per RV family;
    (c) RV residual time-series + z-score for each family, highlighting
    where |z| > 1.5; (d) Half-life comparison bar chart (3 signals x
    3 methods); (e) Regime-conditional quality heatmap — half-life and
    signal_freq by (signal x regime_label); (f) C22 thesis result —
    equity_first vs neither half-life for RV1, with annotation;
    (g) Cointegration + ADF summary table; (h) C18–C24 falsification
    checklist (PASS/FAIL). All plots saved to `sprints/v3/plots/`.
  - Files: `notebooks/03_rv_signals.ipynb`,
    `sprints/v3/plots/01_regime_labels.png`,
    `sprints/v3/plots/02_hedge_ratios.png`,
    `sprints/v3/plots/03_rv_residuals.png`,
    `sprints/v3/plots/04_halflife_comparison.png`,
    `sprints/v3/plots/05_regime_quality_heatmap.png`.
  - Validation: fails if notebook errors on re-run; fails if any plot
    is missing; fails if C18–C24 checklist cell omits any criterion.

- [x] **Task V10: Sprint close — notes + walkthrough + all tests green**
  - Acceptance: `sprints/v3/notes.md` records per-task findings,
    observed half-lives, hedge method rankings, regime distribution,
    and the C22 thesis result with exact numbers. Full test suite
    green: Sprint 1 (25/25), Sprint 2 Catch2 (38/38) + pytest (4/4),
    Sprint 3 new (≥ 10 tests). `sprints/v3/WALKTHROUGH.md` written
    per the `/quant-walkthrough` skill. Commit, tag `sprint-v3`, push.
  - Files: `sprints/v3/notes.md`, `sprints/v3/WALKTHROUGH.md`.
  - Validation: fails sprint if any of C18–C24 fail, if any prior
    sprint test regresses, or if the walkthrough omits any required
    section. Promote to tag `sprint-v3` only when all green.
