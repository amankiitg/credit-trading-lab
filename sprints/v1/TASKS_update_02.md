# Sprint v1 — Tasks Update 02 (incremental patch)

Six atomic tasks, each 5–15 min. Every ingest task is paired with a
validation task. Randomness pinned via seeds. Status starts at `[ ]`;
mark `[x]` as each lands.

Pre-req: `sprint-v1` tag checked out (features.parquet at shape
(4784, 49); flags + stubs already in schema).

See `PRD_update_02.md` for falsification criteria C9–C11 and the
pre-registered algorithms.

---

- [ ] **Task U4: Implement `signals/fred.py` — FRED ingest**
  - Acceptance: `signals.fred.fetch(series_ids, start, end)` returns
    a wide dataframe with one column per series ID, business-day
    index, tz-naive. `signals.fred.build()` pulls the 11 series
    listed in PRD §Signal Definition via the public CSV endpoint
    (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>`),
    forward-fills up to 1 business day, drops remaining NaN with
    count logged, and writes `data/raw/credit_market_data.parquet`.
    No API key required. Import + module-run exits 0.
  - Files: `signals/fred.py`,
    `data/raw/credit_market_data.parquet`.
  - Validation: fails if any listed series is missing from the
    frame, if column dtype is not float64, if index is not
    monotonic+unique DatetimeIndex, or if any OAS column contains
    negative values.

- [ ] **Task U5: FRED coverage audit (pairs with U4)**
  - Acceptance: `tests/test_credit_data.py::test_fred_coverage`
    passes — checks performed: index is tz-naive business-day,
    start date ≤ 1996-12-31 (BAML earliest), max consecutive
    business-day gap ≤ 10, all OAS series ≥ 0, all DGS series
    finite and non-negative. Coverage plot saved to
    `sprints/v1/plots/06_fred_coverage.png` with axis labels +
    series legend + date range in title.
  - Files: `tests/test_credit_data.py`,
    `sprints/v1/plots/06_fred_coverage.png`.
  - Validation: fails C9 if any gap > 10 business days, any OAS
    value < 0, or any listed series missing.

- [ ] **Task U6: Synthetic CDS + ETF↔OAS correlation check**
  - Acceptance: `signals.fred.build_synth_cds(credit_df, hyg_ytw,
    lqd_ytw)` adds `synth_cds_hy` and `synth_cds_ig` columns to
    `credit_market_data.parquet` per the formulas in PRD §Synthetic
    CDS. If iShares YTW is not scriptably available, fall back to
    trailing-12m distribution yield from
    `data/raw/{HYG,LQD}.parquet` dividends, and set a sidecar
    `ytw_source` column per ticker (`ishares` or `proxy`).
    `tests/test_credit_data.py::test_etf_oas_correlation` computes
    Pearson correlation between `hy_spread` (from features.parquet)
    and `oas_hy` (from credit_market_data.parquet) on the
    overlapping index; correlation > 0.7 for C10.
  - Files: `signals/fred.py`, `tests/test_credit_data.py`,
    `data/raw/credit_market_data.parquet`.
  - Validation: fails C10 if correlation ≤ 0.7. If YTW proxy is
    used, test must assert `ytw_source` column exists and
    surfaces the choice.

- [ ] **Task U7: Buy-and-hold HYG column + schema bump**
  - Acceptance: `signals.pipeline.build()` appends
    `HYG_buyhold_cum_log_ret = HYG_log_ret.cumsum().fillna(0.0)` as
    the final numeric column before the flags block. Schema order:
    per-ticker (20) → spreads (3) → z-scores (9) → buyhold (1) →
    flags (12) → stubs (5) = **50 columns**. `test_features_schema`
    updated: `assert len(df.columns) == 50`. Zero NaN in the new
    column for all rows (first row = 0.0 by construction).
  - Files: `signals/pipeline.py`, `tests/test_signals.py`.
  - Validation: fails if schema order drifts, if the new column
    has any NaN, or if cumsum ≠ running total of log returns
    (identity test: `diff(HYG_buyhold_cum_log_ret) ==
    HYG_log_ret[1:]` within 1e-15).

- [ ] **Task U8: Implement `signals/benchmarks.py` — random-entry MC**
  - Acceptance: `signals.benchmarks.random_baseline(features_path,
    n_paths=1000, seed=42)` returns a dataframe with exactly 3000
    rows (1000 paths × 3 spreads) and columns `[path_id, spread,
    n_trades, total_pnl, mean_trade_pnl, std_trade_pnl, sharpe,
    hit_rate]`. Algorithm exactly per PRD §Random-entry MC: extract
    holding-length distribution from v1 flags, sample with
    replacement, uniform random entry dates, uniform random
    direction. Writes `data/benchmarks/random_baseline.parquet`.
    Running twice with the same seed produces byte-identical
    output.
  - Files: `signals/benchmarks.py`,
    `data/benchmarks/random_baseline.parquet`.
  - Validation: fails if shape ≠ (3000, 8), if seeded runs diverge,
    or if any path has n_trades = 0.

- [ ] **Task U9: Baseline sensibility + sprint re-validate**
  - Acceptance: `tests/test_benchmarks.py::test_random_baseline_is_noise`
    asserts per-spread Sharpe distribution mean ∈ [-0.2, 0.2] and
    std ∈ [0.2, 1.0] (C11). Summary table appended to
    `sprints/v1/notes.md`. Plot saved to
    `sprints/v1/plots/07_random_baseline_dist.png` showing per-
    spread Sharpe histograms with mean + 5th/95th percentile lines.
    Notebook `01_signal_validation.ipynb` gets a new cell "## 6c.
    Baselines (buy-hold + random MC)" showing the HYG buy-hold
    equity curve and the random-baseline Sharpe histogram; notebook
    re-executes top-to-bottom without errors. Full test suite
    green (target: 16 + 3 new = 19 passing). Final checklist in
    notebook prints C9, C10, C11 in addition to C1–C8.
  - Files: `tests/test_benchmarks.py`, `tests/test_credit_data.py`,
    `notebooks/01_signal_validation.ipynb`, `sprints/v1/notes.md`,
    `sprints/v1/plots/07_random_baseline_dist.png`.
  - Validation: fails C11 if the distribution is off-center or
    degenerate. Fails sprint if any of C9–C11 fail, if test suite
    regresses below 19/19, or if the notebook errors on re-run.
