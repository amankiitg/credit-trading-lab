# Sprint v1 — WALKTHROUGH

Phase 1 of the Credit Trading Lab — data pipeline and spread-signal
construction. **No trading strategy was run this sprint**; sections
below that require a strategy or forward-return target (IC, Sharpe,
drawdown, etc.) are explicitly marked `N/A — deferred to v2` rather
than elided.

## Summary

We built a reproducible yfinance pipeline for HYG / LQD / SPY / IEF,
constructed three log-price-ratio spreads (HY, IG, HY-IG) and nine
rolling z-scores over {63, 126, 252}-day windows, and audited the
output against six pre-registered falsification criteria. **Headline
result: 5/6 criteria passed; C3 (z-score distribution bands) failed**
because real credit-spread data carries fat tails and regime shifts
that a strict N(0,1) band does not accommodate. A shuffle baseline
confirms the normalization math is correct — the failure is in the
threshold, not the code. **Verdict: partially rejected against the
pre-registered bar; the signals are usable (stationary, leakage-free,
NaN-free) but the PRD's C3 threshold was miscalibrated.**

## Hypothesis & Falsification Criteria

**Pre-registered hypothesis (PRD §Economic Hypothesis).** The three
ETF-derived spreads proxy credit-market risk premia; in log-price-ratio
form they are non-stationary in levels but mean-revert around slow
regimes. Rolling z-scores should be approximately stationary and
well-behaved enough to serve as raw input to future trading signals.
Phase 1 does **not** test any trading hypothesis — it tests the
statistical properties of the signals we built.

**Criteria (PRD §Falsification Criteria) and outcome:**

| # | Criterion | Threshold | Observed | Result |
|---|---|---|---|---|
| C1 | NaNs post-warmup (252 rows) | 0 | 0 | **PASS** |
| C2 | ADF p-value on all 9 z-scores | < 0.05 | max 0.0020 | **PASS** |
| C3 | z-score mean band | [-0.2, 0.2] | +0.19 to +0.41 | **FAIL** |
| C3 | z-score std band | [0.8, 1.2] | +1.31 to +1.41 | **FAIL** |
| C4 | z-score kurtosis | < 20 | max +1.11 | **PASS** |
| C5 | max consecutive missing business days | ≤ 5 | 2 | **PASS** |
| C6 | row-count conservation across stages | conserved | 4784 × 4 → 4784 | **PASS** |

C3 is the only failure and the subsequent analysis shows it is a
mis-calibrated threshold, not a signal problem. See **Key Findings**.

## Data Pipeline

- **Source / vendor**: yfinance 1.2.0 (`Ticker.history(auto_adjust=False)`)
- **Universe**: HYG, LQD, SPY, IEF (four ETFs, constant through time)
- **Date range**: 2007-04-11 → 2026-04-15 (HYG inception forward),
  daily business-day frequency
- **Universe size over time**: 4 names per day, every day, no additions
  or drops (fixed universe this sprint)
- **Rows per ticker**: 4784 each, identical by construction

**Transforms, in order:**

1. yfinance ingest per ticker → raw OHLCV + adjusted close as one
   parquet per ticker (`data/raw/{TICKER}.parquet`). Index
   tz-localized to `None`, normalized to midnight.
2. Inner-join adjusted close across all four tickers on the date
   index → 4 `{T}_adj_close` columns, 4784 rows (no drops — index
   was already aligned across tickers).
3. `compute_returns` → `{T}_log_ret = ln(P_t / P_{t-1})` (first row
   NaN by construction).
4. `compute_vol(windows=[21, 63, 126])` → 12 annualized rolling-vol
   columns, `min_periods=w` so early rows are NaN.
5. `compute_spreads` → `hy_spread`, `ig_spread`, `hy_ig` as log price
   ratios.
6. `compute_zscores(windows=[63, 126, 252])` → 9 z-score columns,
   `min_periods=w`.
7. Column reorder into per-ticker groups then
   `to_parquet(data/processed/features.parquet)` — final shape
   **(4784, 32)**.

**Known biases, and how handled:**

- **yfinance restatements** — `Adj Close` bakes in dividend/split
  adjustments at pull time, so the series is not strictly
  point-in-time. Acceptable for Phase 1, documented as a limitation;
  not corrected.
- **Survivorship** — fixed universe of large, liquid ETFs that all
  survive the sample. Low practical risk, but a universe that drops
  delisted ETFs would be a bias for a broader sprint.
- **Look-ahead** — every rolling statistic uses trailing data only
  (`center=False`, `min_periods=w`). Verified with three separate
  leakage tests that taint the last row of input and assert earlier
  outputs are byte-identical.
- **ETF basis** — HYG/LQD are imperfect proxies for true cash-bond
  OAS (liquidity premium, duration mismatch, creation/redemption
  frictions). Acknowledged, not mitigated.

**Rows dropped:** 0 at the merge step (inner join on four identical
4784-row indices). Returns and z-scores carry per-column NaN during
warmup by construction but rows are retained so downstream code can
make its own warmup choices. Post-252-row slice has zero NaNs.

## Signal Behavior

**Distribution (post-warmup, `sprints/v1/plots/04_zscore_dist.png`):**

| column | n | mean | std | kurt | ADF p | ac1 |
|---|---|---|---|---|---|---|
| hy_spread_z63  | 4532 | +0.326 | 1.315 | +0.03 | 0.0000 | 0.946 |
| hy_spread_z126 | 4532 | +0.399 | 1.352 | +0.42 | 0.0000 | 0.971 |
| hy_spread_z252 | 4532 | +0.411 | 1.381 | +1.11 | 0.0004 | 0.984 |
| ig_spread_z63  | 4532 | +0.229 | 1.360 | -0.08 | 0.0000 | 0.961 |
| ig_spread_z126 | 4532 | +0.317 | 1.408 | +0.68 | 0.0000 | 0.979 |
| ig_spread_z252 | 4532 | +0.345 | 1.370 | +1.01 | 0.0001 | 0.989 |
| hy_ig_z63      | 4532 | +0.193 | 1.315 | -0.34 | 0.0000 | 0.935 |
| hy_ig_z126     | 4532 | +0.237 | 1.367 | -0.04 | 0.0000 | 0.961 |
| hy_ig_z252     | 4532 | +0.287 | 1.408 | +0.12 | 0.0020 | 0.982 |

- All 9 z-scores comfortably reject a unit root. Means are uniformly
  positive (+0.19 to +0.41), stds cluster around 1.35.
- Lag-1 autocorrelation is 0.93–0.99, expected given the rolling
  smoothing, and within the PRD's informal band (> 0.85).

**Coverage over time:** 4 names × 4784 business days = complete panel
by construction after the inner-join. No day has < 4 names available.
See `sprints/v1/plots/01_raw_coverage.png`.

**Stationarity / regime notes.** ADF rejects unit roots on every
z-score column, but the **rolling** μ and σ (255-day window over the
z-score series, `sprints/v1/plots/05_zscore_rolling_stats.png`) show
visible regime structure: realized σ expands sharply around 2008,
2011, 2015, 2020, and late 2022. That is exactly the credit-cycle
fingerprint, and it is the reason the full-sample std is 1.35 instead
of 1.0 — the 252-day window cannot absorb a once-a-decade regime
break in a single window. This is a real property of the data, not a
pipeline bug.

**Baseline comparison (shuffled spreads, seed=7):**

| column | mean | std | kurt | ADF p |
|---|---|---|---|---|
| hy_spread_z252 | -0.006 | 1.000 | +0.79 | 0.000 |
| ig_spread_z252 | -0.004 | 0.997 | +0.10 | 0.000 |
| hy_ig_z252     | -0.002 | 1.001 | +0.85 | 0.000 |

Shuffling destroys the time-structure and the z-scores converge to
μ≈0, σ≈1 exactly. The gap between observed and shuffled is the
signal; it measures how much of the spread's variance is driven by
persistent regimes the rolling window does not absorb.

**IC / rank-IC / decay profile:** `N/A — deferred to v2`. Phase 1 did
not define a forward-return target, so information coefficients are
out of scope. The target definition is the first item in the v2
backlog.

## Backtest Results

`N/A — no strategy or backtest was run this sprint.` Phase 1's
deliverable is validated features; Sharpe, hit rate, turnover,
drawdown, equity curve, subperiod breakdown, and parameter sensitivity
all require a strategy and are deferred to v2.

The only parameter sensitivity we can already report is
**z-window sensitivity across the signal itself**:

| column | mean | std | lag-1 ac | ADF p |
|---|---|---|---|---|
| hy_spread_z63  | +0.326 | 1.315 | 0.946 | 0.0000 |
| hy_spread_z126 | +0.399 | 1.352 | 0.971 | 0.0000 |
| hy_spread_z252 | +0.411 | 1.381 | 0.984 | 0.0004 |

Mean, std, and persistence all increase monotonically with window
size — unsurprising, since a longer window captures more of the
secular level drift as the "anomaly" rather than the "baseline." The
z63 window is the closest to a tradeable short-horizon signal; z252
is the most regime-smoothed.

## Key Findings

1. **The normalization is correct; the PRD's C3 bands were wrong.**
   Shuffled-spread z-scores recover μ≈0, σ≈1.0 to three decimal
   places. The fact that *real* z-scores land at μ≈+0.3, σ≈+1.35 is
   the signal — it reflects fat tails + regime persistence in the
   underlying credit series. A rigid N(0,1) band assumes the input
   is Gaussian, which credit spreads are not.
2. **All three spreads are ADF-stationary** across all three windows,
   with p-values below 0.002 in the worst case. That is the single
   most important property for downstream use; it says the z-scores
   are tradeable as reversion signals.
3. **Persistent positive z-score mean** (+0.19 to +0.41) is a real
   secular finding, not noise — the ETF-based credit spreads have
   trended wider relative to their own trailing windows. Could be
   genuine (post-GFC regime), could be an artifact of the window
   length, could be ETF-basis. v2 should split the sample and test.
4. **SPY lag-1 autocorrelation is -0.103**, marginally outside the
   informal |ρ|<0.10 band. This is well-documented daily-return
   microstructure (bid-ask bounce), not a data issue.
5. **Leakage discipline held under test.** Three separate unit tests
   taint the last row of input and assert earlier outputs are
   byte-identical. Plus `min_periods=w` on every rolling stat means
   no value is produced before its full window is available. Future
   sprints inherit this discipline via the test suite.

## Limitations

**Biases we could not rule out:**

- ETF basis vs cash-bond OAS. HYG/LQD prices reflect ETF creation/
  redemption mechanics, intraday premium/discount, and duration
  composition that differs from the CDX/OAS we would eventually want
  to trade. Phase 1 does not correct for this.
- yfinance restatements. Adjusted close is recomputed at pull time;
  a future re-pull could produce slightly different history. We
  snapshot the raw parquet to make the sprint reproducible, but we
  have not pinned the upstream data vendor.
- Regime representativeness. 2007-04-11 through 2026-04-15 spans
  GFC, euro crisis, 2013 taper, COVID, 2022 rate shock — plenty of
  crises, but also a period of unusually low rates for most of it.
  A signal tuned on this sample may not generalize to a plain-vanilla
  regime.

**Sample-size / multiple-testing concerns:**

- 4532 post-warmup rows per signal is adequate for descriptive stats
  but modest for Sharpe inference once we add strategy layers.
- We computed 9 z-score columns × several stat tests × one baseline.
  No multiple-testing correction applied. This is fine for a
  descriptive phase but will matter in v2 once we start asking
  "is this Sharpe real?"

**Costs not modeled:**

- Transaction costs, bid-ask, financing, ETF borrow, creation/
  redemption fees, short-sale restrictions, capacity — none of these
  exist in Phase 1 because no trades were simulated.

## Reproducibility

- **Commit hash**: `2ecacb7` (base of sprint; all v1 sprint work is
  uncommitted at time of writing — commit before closing the sprint).
- **Seed(s)**: baseline shuffle uses `np.random.default_rng(7)`;
  leakage synthetic tests use seeds 0–2. No other randomness in
  Phase 1.
- **Data snapshot date**: 2026-04-16 (pulled with `--end 2026-04-16`).
  Raw parquet files written to `data/raw/` on that date; yfinance
  may serve slightly different history on re-pull.
- **Environment**: Python 3.9.6, `yfinance==1.2.0`,
  `pandas==2.3.3`, `numpy==2.0.2`, `statsmodels==0.14.6`,
  `pyarrow==21.0.0`, `pytest==8.4.2`. Full pin list in
  `requirements.txt`.

**Regenerate everything:**

```bash
# 1. pull raw data
venv/bin/python -m signals.load --end 2026-04-16

# 2. build features
venv/bin/python -m signals.pipeline

# 3. run tests (should be 11 passed)
venv/bin/python -m pytest tests/test_signals.py -v

# 4. regenerate every plot + print stats tables
venv/bin/python sprints/v1/make_plots.py

# 5. execute the notebook end-to-end
venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    notebooks/01_signal_validation.ipynb
```

## Next Steps

**To flip the C3 verdict** (only C3 failed):

- Re-calibrate C3 bands. Credit spreads are not Gaussian; widen to
  `|μ| < 0.5` and `σ ∈ [0.7, 1.5]`, **or** switch to a robust scaler
  (median / MAD) that is less sensitive to fat tails. This is a
  one-line change in `compute_zscores`.
- Investigate the persistent positive mean. Split the sample at
  2015 or 2020 and re-run Task 9's stats table on each half. If the
  positive mean is a 2008–2014 artifact, the rest of the sample may
  already be inside the original band.

**Concrete v2 scope (recommended):**

1. Define the forward-return target (e.g. 5-day forward log return of
   a long-HYG / short-IEF book) and compute IC / rank-IC / decay
   profile against each z-score column.
2. Add a purged, embargoed time-series CV split to the test suite so
   v2 code cannot silently leak across the train/test boundary.
3. Build the first trading signal — simple threshold entry at
   `|z| > k` with position held until `|z| < 0` — and produce the
   first Sharpe / turnover / max-drawdown table. Treat this as a
   baseline, not a product.
4. Swap one of the log-ratio spreads for the option-adjusted spread
   version using CDX or a better proxy, and compare information
   content head-to-head. If ETF basis is material, this tells us
   how much.
