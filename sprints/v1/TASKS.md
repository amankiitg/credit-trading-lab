# Sprint v1 — Tasks

Atomic, each 5–15 min of focused work. Every ingest/signal task is paired
with a validation task. Leakage check and baseline sanity are explicit tasks.

---

- [x] **Task 1: Scaffold modules and pin dependencies**
  - Acceptance: `signals/{load,features,zscore,pipeline}.py` exist as empty
    stubs with type-hinted function signatures; `pyarrow`, `statsmodels`,
    `jupyter` appended to `requirements.txt` and installed in the venv;
    `python -c "import signals.load, signals.features, signals.zscore,
    signals.pipeline, pyarrow, statsmodels"` exits 0.
  - Files: `signals/load.py`, `signals/features.py`, `signals/zscore.py`,
    `signals/pipeline.py`, `requirements.txt`.
  - Validation: fails if any import errors, or if stubs contain logic
    instead of signatures.

- [x] **Task 2: Implement yfinance ingest → `data/raw/{ticker}.parquet`**
  - Acceptance: `signals.load.fetch(tickers, start, end)` returns a dict of
    dataframes with the raw schema from the PRD; `signals.load.write_raw`
    writes one parquet per ticker to `data/raw/`; running the module as a
    script populates all four files; each parquet round-trips to the same
    shape/dtypes via `pd.read_parquet`.
  - Files: `signals/load.py`, `data/raw/{HYG,LQD,SPY,IEF}.parquet`.
  - Validation: fails if column names/dtypes diverge from the PRD schema,
    if index is not a tz-naive `DatetimeIndex` named `date`, or if any
    ticker pull returns an empty dataframe.

- [x] **Task 3: Raw-data audit (pairs with Task 2)**
  - Acceptance: `tests/test_signals.py::test_raw_integrity` passes; checks
    performed per ticker: monotonic unique index, no business-day gap > 5,
    no NaNs in `adj_close`, `volume` is int64 and non-negative; row counts
    printed per ticker; a coverage plot saved to
    `sprints/v1/plots/01_raw_coverage.png` showing availability over time.
  - Files: `tests/test_signals.py`, `sprints/v1/plots/01_raw_coverage.png`.
  - Validation: fails if any gap > 5 business days, or if the plot is
    missing axis labels / date range in title.

- [x] **Task 4: Implement returns and rolling volatility**
  - Acceptance: `signals.features.compute_returns(df)` adds `log_ret`
    column; `signals.features.compute_vol(df, windows=[21,63,126])` adds
    annualized vol columns; functions are pure (no disk I/O); shift-test
    passes — `compute_returns` on a df where the last row is replaced
    with NaN leaves all earlier values unchanged.
  - Files: `signals/features.py`.
  - Validation: fails if any rolling stat uses `center=True` or otherwise
    includes future data, or if vol is not annualized.

- [x] **Task 5: Returns validation (pairs with Task 4)**
  - Acceptance: printed table with per-ticker `log_ret` mean, std, skew,
    kurt, lag-1 autocorr; ACF plots saved to
    `sprints/v1/plots/02_returns_acf.png`; lag-1 |ρ| < 0.10 for every
    ticker; distribution histogram saved to
    `sprints/v1/plots/03_returns_dist.png`.
  - Files: `sprints/v1/plots/02_returns_acf.png`,
    `sprints/v1/plots/03_returns_dist.png`,
    `sprints/v1/notes.md` (append results table).
  - Validation: fails if any ticker has |lag-1 ρ| ≥ 0.10, or if plots omit
    axis labels / ticker legend.

- [x] **Task 6: Implement spread construction**
  - Acceptance: `signals.features.compute_spreads(df)` returns a df with
    exactly `hy_spread`, `ig_spread`, `hy_ig` columns; formulas match the
    PRD precisely (log of price ratios); a unit test verifies
    `hy_ig == hy_spread - ig_spread` to within 1e-12.
  - Files: `signals/features.py`, `tests/test_signals.py::test_spread_identity`.
  - Validation: fails if the algebraic identity test fails, or if spread
    columns contain NaNs where both underlying prices are present.

- [x] **Task 7: Implement rolling z-scores**
  - Acceptance: `signals.zscore.compute_zscores(df, cols, windows)`
    returns a df with 9 columns named `{col}_z{w}`; rolling mean/std use
    `min_periods=w`; a unit test on a synthetic series with known
    mean/std recovers z-scores to within 1e-10; leakage test — replacing
    the last row of input with NaN does not alter any earlier z-score.
  - Files: `signals/zscore.py`,
    `tests/test_signals.py::test_zscore_no_leakage`.
  - Validation: fails if z-scores are produced before `min_periods` is met
    (should be NaN), or if leakage test fails.

- [x] **Task 8: Assemble `features.parquet` via pipeline**
  - Acceptance: `signals.pipeline.build()` reads `data/raw/*.parquet`,
    runs the feature + spread + z-score steps, writes
    `data/processed/features.parquet`; `tests/test_signals.py::test_features_schema`
    asserts the full 32-column schema from the PRD (names, dtypes, index)
    and zero NaNs after the 252-row warmup; row counts at each stage are
    printed.
  - Files: `signals/pipeline.py`, `data/processed/features.parquet`,
    `tests/test_signals.py::test_features_schema`.
  - Validation: fails on any schema mismatch, any post-warmup NaN, or if
    row counts are not conserved (drops must be logged with reason).

- [x] **Task 9: Signal statistical validation + baseline comparison**
  - Acceptance: a script `signals/pipeline.py --validate` (or a cell in
    the Task 10 notebook) computes and logs, for each of the 9 z-score
    columns: mean, std, kurt, ADF p-value; compares against a baseline z-
    score computed on a shuffled copy of the same spread (expected mean≈0,
    std≈1, ADF p<0.05 from shuffling alone); results table appended to
    `sprints/v1/notes.md`; all falsification thresholds pass.
  - Files: `sprints/v1/notes.md`, `sprints/v1/plots/04_zscore_dist.png`,
    `sprints/v1/plots/05_zscore_rolling_stats.png`.
  - Validation: fails if any z-score column violates the mean/std/kurt
    bands or ADF threshold from the PRD, or if the baseline comparison is
    missing.

- [x] **Task 10: Build `notebooks/01_signal_validation.ipynb`**
  - Acceptance: notebook runs top-to-bottom from a fresh kernel without
    errors; sections: (1) raw coverage, (2) returns distribution + ACF,
    (3) spread time series, (4) z-score distribution & rolling stats,
    (5) ADF results table, (6) falsification-criteria checklist with
    pass/fail per item; final cell prints a single-line `PHASE1 STATUS:
    PASS` or `FAIL` based on the checklist; notebook executed and saved
    with outputs.
  - Files: `notebooks/01_signal_validation.ipynb`,
    `sprints/v1/plots/*` referenced inline.
  - Validation: fails if the notebook errors on re-run, if any plot is
    missing axes/title/legend, or if the pass/fail cell is missing.

---

## Amendment — added after initial v1 close

- [x] **Task 11: Signal-state flags (`signals/flags.py`)**
  - Acceptance: `compute_flags(df, spreads, window=63, thresholds)`
    returns a bool-dtype frame with 12 columns
    (`{spread}_{entry_long,entry_short,exit,stop}`) derived from the
    `{spread}_z63` series; default thresholds
    `entry=2.0, exit=0.5, stop=4.0`; NaN z-scores produce `False`,
    never NaN; invalid thresholds (`exit ≥ entry` or `stop ≤ entry`)
    raise `ValueError`.
  - Files: `signals/flags.py`.
  - Validation: `test_flag_threshold_semantics`,
    `test_flags_handle_nan_z_score`,
    `test_flag_thresholds_reject_bad_config` all pass.

- [x] **Task 12: RV signal stubs**
  - Acceptance: `rv_stubs(index)` returns a float64 frame with
    `rv_pair_score`, `rv_rank_score`, `rv_composite`, all NaN; these
    appear in `features.parquet` in the documented schema position;
    `test_rv_stubs_are_all_nan` passes.
  - Files: `signals/flags.py`, `signals/pipeline.py`.
  - Validation: fails if any stub column is missing, non-float64, or
    contains a non-NaN value in v1.

- [x] **Task 13: Re-validate (schema + NaN audit)**
  - Acceptance: `features.parquet` has shape `(N, 49)`; schema test
    covers numeric-vs-bool dtype discipline; NaN audit passes for all
    non-stub, non-flag columns post-warmup; flag columns are NaN-free
    across the entire frame including warmup; full test suite is
    **16/16 green**; flag-firing rates printed and within sane
    bounds (< 25% of rows per flag).
  - Files: `tests/test_signals.py`, `signals/pipeline.py`.
  - Validation: fails if any flag fires on > 25% of rows (threshold
    too loose) or on 0% of rows (threshold too tight / bug).
