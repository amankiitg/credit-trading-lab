# Sprint v3 — Relative Value Signals

## Summary

Sprint v3 built three credit relative-value signals (HY/IG, Credit/Rates,
Cross-term) using three hedge-ratio methods (rolling OLS, 2-state Kalman,
DV01 from the C++ pricer) and ran a regime-conditional quality analysis.
The pre-registered central thesis — **RV1 mean-reverts faster on
`equity_first` days than on `neither` days** — **passes** across all three
hedge methods (Kalman 43% shorter, OLS 67%, DV01 84%). C19, C20, C21, C22,
C24 pass; C18 partially fails (regime non-degeneracy on 2 of 3 classifiers
is too strict for our 19-year sample); C23 fails on 4 of 9 (pair × method)
combos because OLS β crosses zero in regime shifts. **Verdict: thesis
confirmed; implementation reveals tradeoffs that reshape the Sprint 5
plan.**

## Hypothesis & Falsification Criteria

**Primary thesis (pre-registered):** Equity markets incorporate risk faster
than credit, creating predictable lag-driven dislocations. Specifically,
the half-life of RV1 (HY/IG residual) mean-reversion is **>20% shorter**
during `equity_first` regimes than during `neither` regimes.

| ID | Criterion | Threshold | Result |
|---|---|---|---|
| C18 | Regime label coverage + non-degeneracy | cov>95%, top<70% | partial — 1/3 (vol_regime ✓, equity_regime 74%, equity_credit_lag 85%) |
| C19 | RV residual stationarity (best method, ADF) | p<0.05 each | ✓ all 3 (Kalman p≈0) |
| C20 | Cointegration via at least one method | p<0.05 each pair | ✓ all 3 |
| C21 | OU half-life ∈ [1, 126] days | each best-method residual | ✓ all 3 (1.5–2.2d Kalman) |
| **C22** | **RV1 equity_first vs neither half-life** | **>20% shorter** | **✓ ratio=0.572 (43% shorter)** |
| C23 | Rolling 63d hedge CV < 1.0 | each (pair × method) | ✗ 4/9 fail (OLS all 3, Kalman x-term) |
| C24 | regime_signal_quality.parquet schema | ≥27 rows, valid schema | ✓ 63 rows |

C18 and C23 failures are pre-registered honest failures — see Limitations.

## Data Pipeline

**Sources** (no change from sprint-v1):
- ETF OHLCV: yfinance (HYG, LQD, SPY, IEF), 4784 trading days, 2007-04-11 → 2026-04-15
- Treasury yields + BAML OAS + synthetic CDS: FRED, 7639 days, 1996-12-31 → 2026-04-15
- C++ pricer: `pycredit` module from sprint-v2

**Sprint-3 transforms (in `signals/pipeline.py::build_with_rv()`):**

1. Sprint-1 base: returns + volatility + spreads + z-scores + flags + RV
   stubs → 50 columns.
2. Compute 3 regime classifiers (`signals.regimes`): `vol_regime`,
   `equity_regime`, `equity_credit_lag`. All trailing-only.
3. Compute 9 (3 pair × 3 method) candidate residuals
   (`signals.rv_signals.build_all_residuals`).
4. Per pair, select the method with lowest ADF p-value as "best."
5. Overwrite the 5 NaN stub columns with best-method residuals + hedge
   ratios; add 3 categorical regime cols + 3 z_rv cols → final 56 cols.
6. Regime-conditional quality table (3 signals × 3 methods × 7 unique
   labels = 63 rows) → `data/results/regime_signal_quality.parquet`.

**Known biases:**
- ETF proxy for DV01: pricing fixed-maturity 4y / 9y bullets rather than
  using ETF time-varying duration. Bias: hedge ratio drifts as ETF
  composition shifts.
- Kalman initialization: first 63 valid rows use OLS; not strictly online
  pre-day-64. Mitigated by 252-day global warmup.
- `vol_regime` uses an expanding median, which weakly leaks future
  information from earlier days into the threshold for later days.
  Acceptable for regime conditioning; would not be acceptable for trading.
- Multiple-testing: 27 (signal × method × classifier) cells; only C22 is
  pre-registered. The rest are exploratory.

**Row counts:** 4784 in, 4784 out. No drops. 252-day warmup applied
analytically (not by row deletion).

## Signal Behavior

**Residual distributions** (post-warmup, n=4532):

| pair | best method | residual var | half-life | ADF p |
|---|---|---|---|---|
| rv_hy_ig | kalman | 2.0e-5 | 1.50d | 1e-26 |
| rv_credit_rates | kalman | 4.4e-5 | 2.23d | 1e-23 |
| rv_xterm | kalman | 3.0e-5 | 1.87d | 1e-25 |

For comparison, OLS produces 11–47× larger residual variance and 18–26d
half-lives — a more economically meaningful but ADF-less-significant fit.

**Coverage:** 56-column features.parquet has 4721 / 4784 = 98.7% non-null
on the 5 RV residual columns post-warmup; the 63 NaN rows are warmup +
regime-classifier startup rows.

**Stationarity:** see C19/C21 above. All three best-method residuals pass
ADF and have finite half-lives, but Kalman residuals are practically too
small to trade — they pass the formal tests by being nearly zero.

**Regime distribution (post-warmup):**
- `vol_regime`: high 38%, low 62%
- `equity_regime`: bull 74%, bear 26%
- `equity_credit_lag`: equity_first 8%, credit_first 7%, neither 85%

## Headline Result — C22

RV1 (HY/IG) half-life by regime, best method (Kalman):

| regime | half-life | n_obs |
|---|---|---|
| equity_first | 0.85 days | 353 |
| neither | 1.48 days | 3855 |
| credit_first | 1.29 days | 324 |

Ratio equity_first / neither = **0.572 → equity_first is 42.8% shorter**.
Pre-registered threshold was >20% shorter (ratio < 0.80). Thesis confirmed.

The same direction holds across methods:
- OLS: 5.30 / 16.24 = 0.326 (67% shorter)
- DV01: 61.6 / 374.8 = 0.164 (84% shorter)

The economic interpretation: when equity moved first and credit lagged,
the credit catch-up is faster (more deterministic) than on days with no
clear leader. This is consistent with the equity-credit lag thesis being
real.

## Key Findings

1. **The equity-credit lag thesis is supported.** All three hedge methods
   independently confirm equity_first regimes have shorter RV1 half-lives
   than neither regimes by 43–84%. Cross-method robustness suggests the
   effect is not an artifact of the specific β estimator.

2. **Kalman wins ADF but loses tradeability.** A 2-state random-walk
   Kalman fits y so closely that the residual is near-zero noise. ADF
   p ≈ 0 trivially follows. Half-life sits at 1.5–2.2 days — too fast to
   trade on a daily cadence. PRD's "select by ADF" rule should be
   replaced or augmented with a half-life floor in Sprint 5.

3. **DV01 wins hedge stability cleanly.** OLS β crosses zero during
   regime shifts (max CV ~2000); DV01 ratios are bounded between two
   positive durations and never exceed 0.06 max CV. For a production
   hedge where stability matters more than statistical optimality, DV01
   is the right primitive — but its half-life (250–620 days) is too slow
   for daily trading. A blend (DV01 with statistical adjustment) is the
   logical Sprint 5 step.

4. **C18 is structurally hard on a 19-year US sample.** A 63-day cumulative
   SPY return is positive on 74% of days (bull-heavy market history).
   Cross-correlation max-over-11-lags rarely clears 0.15 with 21-day
   windows (correlation SE ≈ 0.22), so 85% of days end up `neither`. The
   "no label > 70%" rule is a calibration-of-thresholds problem, not a
   classifier-quality problem.

5. **OLS is the practical default** despite losing on ADF: 18–26d half-life
   sits squarely in the C21 tradeable band, residuals have meaningful
   variance, and on the C22 thesis test OLS shows the second-largest
   regime gap (67% shorter, behind DV01's 84% but well ahead of Kalman's
   43%). Sprint 5 will likely use OLS as the canonical method.

## Limitations

- **Multiple testing.** 27 (signal × method × classifier) cells; we only
  pre-registered C22. The other 26 are exploratory and should be treated
  as hypothesis-generating, not confirmatory.
- **Sample size on equity_first cell.** 353 days (8% of post-warmup) for
  the C22 numerator. Not tiny but not massive either; the half-life
  estimate has wide error bars we did not compute.
- **No costs modeled.** Sprint v3 produces signals only; financing,
  borrow, bid/ask, and market impact are not modeled. Sprint 5 will.
- **ETF-derived spreads.** Our `hy_spread`, `ig_spread`, `hy_ig` are
  log-price ratios, not par-spread differences. They include duration,
  liquidity, and composition effects that single-name CDS hedges would
  not. Limits the strict economic interpretation.
- **C23 metric brittleness.** CV = std / |mean| explodes near zero; the
  test as written flags genuine instability *and* pure metric artifacts
  in the same way. A more honest spec would be median-CV or CV at the 95th
  percentile.
- **Kalman Q-tuning not optimized.** Q=1e-5 is the PRD-fixed default; a
  larger Q might give residuals with usable half-lives. Sprint 5 should
  do a sensitivity sweep.

## Reproducibility

- **Commit hash**: see `git log --oneline -1` (this commit + tag
  `sprint-v3` mark the sprint close).
- **Data snapshot**: features.parquet last updated by
  `python3 -m signals.pipeline` (which calls `build_with_rv`).
- **No stochastic steps**: all OLS/Kalman/DV01 paths are deterministic.

**To regenerate from scratch:**

```bash
# 1. Activate venv
source venv/bin/activate

# 2. Build C++ pricer (sprint-v2 prerequisite)
cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release \
  -DPython_EXECUTABLE=$(pwd)/venv/bin/python3 -DPYBIND11_FINDPYTHON=ON
cmake --build build -j

# 3. Run the enriched pipeline → writes data/processed/features.parquet (4784×56)
PYTHONPATH=python/credit python3 -m signals.pipeline

# 4. Build the regime quality table → writes data/results/regime_signal_quality.parquet
PYTHONPATH=python/credit python3 -c "
import pandas as pd, pycredit
from signals.rv_signals import build_all_residuals, build_regime_quality_table
df = pd.read_parquet('data/processed/features.parquet')
cmd = pd.read_parquet('data/raw/credit_market_data.parquet')
results = build_all_residuals(df, cmd, pycredit)
build_regime_quality_table(df, results).to_parquet('data/results/regime_signal_quality.parquet', index=False)
"

# 5. Build + execute the validation notebook
PYTHONPATH=python/credit python3 scripts/build_notebook_v3.py
PYTHONPATH=python/credit jupyter nbconvert --to notebook --execute --inplace \
  notebooks/03_rv_signals.ipynb --ExecutePreprocessor.timeout=300

# 6. Run all tests
PYTHONPATH=python/credit python3 -m pytest tests/ -q
ctest --test-dir build --output-on-failure
```

## Next Steps

1. **Demote Kalman; promote OLS as canonical for Sprint 5.** Re-run V6
   with a half-life floor (e.g. >5 days) added to the best-method
   selection rule, or simply hard-code OLS as the primary signal and
   keep the others as diagnostics.

2. **Blend DV01 stability with statistical fit.** Try `β_blend = 0.5 ·
   β_OLS + 0.5 · β_DV01`. The OLS leg adds price-discovery information,
   the DV01 leg constrains the hedge from passing through zero in
   regime shifts. Compare ADF p, half-life, and max-CV vs each pure
   method.

3. **Recalibrate C23 metric.** Replace max-CV with median-CV or
   require CV<1.0 on >90% of windows. Document why the change isn't
   moving the goalposts.

4. **Explore the 27-cell exploratory matrix carefully.** Several `vol_regime
   = high` cells have shorter half-lives than `low`. Worth a follow-up
   with proper multiple-testing correction.

5. **Sprint 4 — costs + execution model.** Sprint 5 cannot run a backtest
   without financing assumptions for ETF shorts (borrow), expected
   slippage, and a holding-period model. PRD it before backtesting.

6. **Out-of-sample window.** All current analysis is in-sample (full
   2007–2026). Sprint 5 should split: train on 2007–2018, test on
   2019–2026, and re-confirm C22 on the held-out half.
