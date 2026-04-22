# Sprint v3 — Relative Value Signals

Weeks 6-7. Sprint 1 (`sprint-v1`) delivered statistical features,
signal-state flags, FRED credit/rates data, and a random-entry MC
baseline. Sprint 2 (`sprint-v2`) delivered a C++ pricing engine with
DV01, CS01, and batch throughput > 10k/sec. Sprint 3 builds three
families of relative-value signals and tests the central thesis that
equity markets lead credit.

## Overview

Build three RV signal families — HY vs IG, Credit vs Rates, and
Cross-term structure — using three hedge-ratio estimation methods
(OLS rolling, Kalman filter, C++ DV01/CS01). For each signal, compute
regime-conditional quality metrics (half-life, z-score magnitude,
frequency) across three regime classifiers (vol regime, equity regime,
equity-credit lag). Store results in a structured parquet for Sprint 5
A/B testing.

**This sprint produces signals and regime analytics, not P&L.** No
backtest is run. The falsification criteria test signal quality, regime
conditioning, and the central hypothesis about equity-credit lag.

## Economic Hypothesis

**Primary thesis:** Equity markets incorporate risk faster than credit
due to higher liquidity and trading frequency. This creates predictable
lag-driven dislocations in HY vs IG spreads.

**Testable prediction:** The half-life of RV1 (HY vs IG residual)
mean-reversion is **> 20% shorter** during `equity_first` regimes
(equity moved first, credit lagged) compared to `neither` regimes (no
clear leader). Faster mean-reversion = more tradeable dislocation =
evidence that equity-credit lag creates exploitable RV.

**Secondary claims:**

1. All three RV residuals (HY/IG, credit/rates, cross-term) are
   stationary (ADF p < 0.05) after hedge-ratio adjustment.
2. DV01-based hedge ratios from the C++ pricer produce residuals with
   lower variance than OLS estimates, because they remove duration
   mismatch mechanistically rather than statistically.
3. Kalman-filter hedge ratios adapt faster to regime shifts than
   rolling OLS, producing shorter half-lives in volatile regimes.

## Falsification Criteria

Pre-registered. All must pass before Sprint 3 is closed.

- **C18 — Regime labels coverage.** Each of the three regime
  classifiers (`vol_regime`, `equity_regime`, `equity_credit_lag`) must
  cover > 95% of post-warmup trading days with a non-null label, and
  no single regime must exceed 70% of observations (labels are
  informative, not degenerate).
- **C19 — RV residual stationarity.** ADF p-value < 0.05 on all three
  RV residuals (`rv_hy_ig_residual`, `rv_credit_rates_residual`,
  `rv_xterm_residual`) using the best hedge method for each.
- **C20 — Cointegration.** Engle-Granger cointegration test p < 0.05
  for each of the three spread pairs under at least one hedge method.
- **C21 — Half-life finite.** Ornstein-Uhlenbeck half-life estimate is
  finite and in [1, 126] trading days for all three RV residuals.
  Half-life > 126 days means too slow to trade; < 1 day means the fit
  is degenerate.
- **C22 — Equity-credit lag thesis.** RV1 (`rv_hy_ig_residual`)
  half-life during `equity_first` regime is **> 20% shorter** than
  during `neither` regime. This is the central pre-registered test.
  Failure rejects the thesis; the signal may still exist but the
  equity-lag explanation is wrong.
- **C23 — Hedge stability.** Rolling 63-day hedge-ratio coefficient of
  variation (CV = std/|mean|) is < 1.0 for each RV pair under each
  hedge method. CV > 1.0 means the hedge ratio flips sign or is
  unstable — the residual is meaningless.
- **C24 — Regime-signal quality stored.** File
  `data/results/regime_signal_quality.parquet` exists with the required
  schema (see below) and contains per-regime half-life, z-magnitude,
  and frequency for all 3 signals x 3 regimes x 3 hedge methods =
  27 rows minimum.

## Signal Definition

### Regime classifiers

Three binary/ternary classifiers, computed from existing Sprint 1
features. Each assigns a label to every post-warmup trading day.

**1. Volatility regime (`vol_regime`)**

Rolling 63-day realized vol of SPY vs its expanding median:

    vol_regime_t = "high" if SPY_vol_63_t > median(SPY_vol_63_{1:t})
                   "low"  otherwise

Labels: `{"high", "low"}`.

**2. Equity regime (`equity_regime`)**

Rolling 63-day cumulative SPY return:

    equity_regime_t = "bull" if Σ_{τ=t-62}^{t} SPY_log_ret_τ > 0
                      "bear" otherwise

Labels: `{"bull", "bear"}`.

**3. Equity-credit lag (`equity_credit_lag`)**

Detects whether equity moved first and credit followed, or vice versa.
Uses cross-correlation of daily SPY returns vs daily `hy_spread`
changes over a trailing 21-day window:

    xcorr_k = corr(SPY_log_ret_{t-20:t}, Δhy_spread_{t-20+k:t+k})

for lags k in {-5, ..., +5}. The lag `k*` with the highest |xcorr|
determines the label:

    equity_credit_lag_t = "equity_first"  if k* > 0  (equity leads)
                          "credit_first"  if k* < 0
                          "neither"       if k* = 0 or max|xcorr| < 0.15

Labels: `{"equity_first", "credit_first", "neither"}`.

Threshold 0.15 is a noise floor — below this the cross-correlation is
not meaningfully different from zero. This threshold was chosen as
approximately 2/sqrt(21) (the standard error of a sample correlation
with n=21).

### RV Signal Family 1: HY vs IG (`rv_hy_ig_residual`)

Spread pair: `hy_spread` (= ln(HYG/IEF)) vs `ig_spread` (= ln(LQD/IEF)).

Three hedge-ratio methods:

**a) OLS rolling (126-day window):**

    hy_spread_t = α + β · ig_spread_t + ε_t
    rv_hy_ig_ols_t = ε_t  (residual)
    hedge_ratio_hy_ig_ols_t = β_t

**b) Kalman filter (random-walk state):**

State equation: `β_t = β_{t-1} + η_t`, `η ~ N(0, Q)`.
Observation: `hy_spread_t = α_t + β_t · ig_spread_t + ε_t`, `ε ~ N(0, R)`.

Kalman recursion estimates `β_t` online with no lookback window. Initial
state: `β_0 = OLS(first 63 days)`. Noise ratio `Q/R` controls
adaptivity; we set `Q = 1e-5`, `R` estimated from OLS residual
variance. These are defaults; sensitivity tested in C23.

    rv_hy_ig_kalman_t = hy_spread_t - α_t - β_t · ig_spread_t
    hedge_ratio_hy_ig_kalman_t = β_t

**c) DV01-based (C++ pricer):**

Use the Sprint 2 pricer to compute DV01 for HYG-proxy and LQD-proxy
bonds on each date (using the DGS term structure from
`credit_market_data.parquet`). Hedge ratio is the DV01 ratio:

    hedge_ratio_hy_ig_dv01_t = DV01_HYG_t / DV01_LQD_t

    rv_hy_ig_dv01_t = hy_spread_t - hedge_ratio_hy_ig_dv01_t · ig_spread_t

DV01 proxy: price a 5% semi-annual bullet at HYG's approximate
duration (4y maturity) and LQD's (9y maturity) off the day's DGS curve.
This is a proxy because ETF duration shifts over time — acceptable for
Sprint 3, noted as a limitation.

**Best hedge selection:** the method whose residual has the lowest ADF
p-value is stored as the canonical `rv_hy_ig_residual` and
`hedge_ratio_hy_ig` in `features.parquet`.

### RV Signal Family 2: Credit vs Rates (`rv_credit_rates_residual`)

Spread pair: `hy_spread` vs a rates factor.

Rates factor: first principal component of `{dgs2, dgs5, dgs10, dgs30}`
from `credit_market_data.parquet`, or simply `dgs10` as a single-factor
proxy (simpler, more interpretable, and avoids PCA stationarity issues).

We use `dgs10` as the rates factor.

Three hedge methods: OLS rolling 126d, Kalman, and CS01/DV01 ratio
from the C++ pricer (CS01 of a 5y CDS / DV01 of a 10y Treasury proxy).

    rv_credit_rates_t = hy_spread_t - β_t · dgs10_t / 100

Best method stored as `rv_credit_rates_residual` and `hedge_ratio_cr`.

### RV Signal Family 3: Cross-term structure (`rv_xterm_residual`)

Spread pair: `hy_ig` (= ln(HYG/LQD)) vs the Treasury term slope.

Term slope: `slope_t = (dgs10_t - dgs2_t) / 100` (in decimal).

    rv_xterm_t = hy_ig_t - β_t · slope_t

Three hedge methods as above. The DV01 variant uses the C++ pricer to
compute duration of long-HYG short-LQD portfolio vs a 2s10s Treasury
flattener.

Best method stored as `rv_xterm_residual`.

### Z-scoring

Each RV residual is z-scored on a 63-day trailing window:

    z_rv_t = (rv_t - mean(rv, 63d)) / std(rv, 63d)

The z-scored residuals are the trading signals for Sprint 5.

### Half-life estimation

For each RV residual, fit an Ornstein-Uhlenbeck process:

    Δrv_t = θ · (μ - rv_{t-1}) · Δt + σ · √Δt · ε_t

The discrete-time regression is:

    Δrv_t = a + b · rv_{t-1} + ε_t

Half-life = `-ln(2) / b` (in trading days). `b` must be negative
(mean-reverting); if b ≥ 0, half-life is infinite (no mean-reversion).

### Regime-conditional quality metrics

For each combination of (signal, regime_classifier, regime_label,
hedge_method), compute:

| metric | definition |
|---|---|
| `half_life` | OU half-life in trading days (on the subsample) |
| `z_magnitude` | mean(|z_rv|) on the subsample |
| `signal_freq` | fraction of days where |z_rv| > 1.5 (tradeable signal) |
| `n_obs` | number of observations in the subsample |
| `adf_pvalue` | ADF p-value on the subsample residual |

Stored in `data/results/regime_signal_quality.parquet`.

### Parameters (explicit)

- `ols_window = 126` (trading days)
- `kalman_Q = 1e-5`, `kalman_R = auto` (from OLS residual variance)
- `kalman_init_window = 63`
- `z_window = 63`
- `xcorr_window = 21`, `xcorr_max_lag = 5`, `xcorr_noise_floor = 0.15`
- `vol_regime_window = 63`
- `equity_regime_window = 63`
- `half_life_bounds = [1, 126]`
- `signal_threshold = 1.5` (z-score for "tradeable")
- `hyg_proxy_maturity = 4` (years)
- `lqd_proxy_maturity = 9` (years)
- `proxy_coupon = 0.05` (5% semi-annual)
- `warmup = 252` (trading days, inherited from Sprint 1)

## Data

**Existing inputs (from prior sprints):**

| artifact | source | purpose |
|---|---|---|
| `data/processed/features.parquet` | Sprint 1 | 50 cols: spreads, z-scores, flags, RV stubs (NaN) |
| `data/raw/credit_market_data.parquet` | Sprint 1 | DGS term structure + BAML OAS (7639 x 15) |
| `data/benchmarks/random_baseline.parquet` | Sprint 1 | MC baseline for Sprint 5 comparison |
| `pycredit` module | Sprint 2 | C++ pricer: `bootstrap_discount`, `price_bonds`, DV01/CS01 |

**New outputs:**

| path | rows (approx) | purpose |
|---|---|---|
| `data/processed/features.parquet` | 4784 | RV stubs populated (was NaN), + regime cols, + z_rv cols |
| `data/results/regime_signal_quality.parquet` | ≥ 27 | Per-regime quality for all signal x regime x method combos |

**Schema additions to `features.parquet`:**

| column | type | description |
|---|---|---|
| `rv_hy_ig_residual` | float64 | Best-method HY/IG residual (was NaN stub) |
| `rv_credit_rates_residual` | float64 | Best-method credit/rates residual (was NaN stub) |
| `rv_xterm_residual` | float64 | Best-method cross-term residual (was NaN stub) |
| `hedge_ratio_hy_ig` | float64 | Best-method hedge ratio (was NaN stub) |
| `hedge_ratio_cr` | float64 | Best-method credit/rates hedge ratio (was NaN stub) |
| `vol_regime` | category | `{"high", "low"}` |
| `equity_regime` | category | `{"bull", "bear"}` |
| `equity_credit_lag` | category | `{"equity_first", "credit_first", "neither"}` |
| `z_rv_hy_ig` | float64 | 63d z-score of rv_hy_ig_residual |
| `z_rv_credit_rates` | float64 | 63d z-score of rv_credit_rates_residual |
| `z_rv_xterm` | float64 | 63d z-score of rv_xterm_residual |

**Total columns after Sprint 3:** 50 (existing) + 6 (regimes + z_rv) = 56.
(The 5 RV stubs are overwritten, not added.)

**`data/results/regime_signal_quality.parquet` schema:**

| column | type |
|---|---|
| `signal` | str — `{rv_hy_ig, rv_credit_rates, rv_xterm}` |
| `hedge_method` | str — `{ols, kalman, dv01}` |
| `regime_classifier` | str — `{vol_regime, equity_regime, equity_credit_lag}` |
| `regime_label` | str — the specific label |
| `half_life` | float64 |
| `z_magnitude` | float64 |
| `signal_freq` | float64 |
| `n_obs` | int64 |
| `adf_pvalue` | float64 |

**Known biases:**

- **ETF proxy for DV01.** We price synthetic bullets at fixed
  maturities (4y, 9y) rather than using actual ETF portfolio duration.
  The DV01 ratio drifts as rates move and as the ETF composition
  changes. Acceptable for Sprint 3; a time-varying maturity lookup is
  a Sprint 5 enhancement.
- **Look-ahead in Kalman initialization.** The first 63 days of OLS
  for Kalman warm-up mean the Kalman residual is not purely online
  until day 64. Mitigated by the 252-day global warmup exclusion.
- **Regime labels are not point-in-time in a strict sense** — they use
  trailing windows and are computed from data available at `t`, but the
  expanding median for vol_regime is updated with all history up to `t`.
  This is a weak form of look-ahead (the median is influenced by the
  full pre-`t` sample, not a fixed window). Acceptable for regime
  conditioning; would not be acceptable for trading signals.
- **Cross-correlation noise floor (0.15).** This threshold determines
  how many days are classified as `neither`. If too low, `neither`
  shrinks and C22 becomes easier; if too high, `equity_first` shrinks
  and C22 becomes harder. We pre-register 0.15 before looking at
  results.

## Success Metrics

Passing C18–C24 is sufficient. No P&L metrics this sprint.

**Summary table** (printed by the final notebook cell):

| metric | target | source |
|---|---|---|
| Regime label coverage | > 95% non-null per classifier | C18 |
| Max regime dominance | < 70% | C18 |
| RV residual ADF p-value | < 0.05 all three | C19 |
| Cointegration p-value | < 0.05 all three pairs | C20 |
| OU half-life | ∈ [1, 126] days | C21 |
| RV1 equity_first vs neither half-life | > 20% shorter | C22 |
| Hedge ratio CV | < 1.0 | C23 |
| regime_signal_quality.parquet | ≥ 27 rows, schema valid | C24 |

## Research Architecture

```
signals/
  regimes.py         -- NEW. Regime classifiers (vol, equity, xcorr lag)
  rv_signals.py      -- NEW. OLS/Kalman/DV01 hedge + residuals for 3 RV families
  halflife.py        -- NEW. OU half-life estimation
  pipeline.py        -- EXTENDED. Adds regime + RV cols to features.parquet

data/
  results/regime_signal_quality.parquet  -- NEW.

tests/
  test_regimes.py       -- NEW. C18 coverage + non-degeneracy
  test_rv_signals.py    -- NEW. C19-C21 stationarity + cointegration + half-life
  test_regime_quality.py -- NEW. C22 thesis test + C23 hedge stability + C24 schema

notebooks/
  03_rv_signals.ipynb   -- NEW. Signal construction walkthrough + regime analysis

sprints/v3/
  PRD.md              -- this file
  TASKS.md            -- 10 tasks
  notes.md            -- populated during sprint
  WALKTHROUGH.md      -- written at sprint close
  plots/              -- all Sprint 3 plots
```

**Data flow:**

1. `signals.regimes.compute_regimes(features_df)` → adds 3 regime
   columns to the features frame.
2. `signals.rv_signals.build_rv_signals(features_df, credit_data_df,
   pycredit)` → computes all 3 RV families x 3 methods, selects best,
   populates the 5 RV stub columns + 3 z_rv columns.
3. `signals.halflife.ou_halflife(residual_series)` → returns half-life.
4. `signals.rv_signals.regime_quality_table(...)` → builds the
   regime_signal_quality.parquet from all combinations.
5. `signals.pipeline.build()` → orchestrates 1-4, writes updated
   features.parquet and regime_signal_quality.parquet.

**Dependency on Sprint 2 C++ pricer:** The DV01 hedge method calls
`pycredit.bootstrap_discount()` and `pycredit.price_bonds()` for each
trading day's DGS curve snapshot. At 42k bonds/sec this adds ~0.1s per
date for 2 bonds — the full 4784-day sweep takes ~5 seconds.

## Risks & Biases

- **Multiple testing.** Three RV families x three hedge methods x three
  regime classifiers = 27 combinations. The C22 thesis test is on one
  specific cell (RV1 x equity_credit_lag x equity_first vs neither).
  The other 26 are exploratory. We do not apply Bonferroni to C22 since
  it was pre-registered, but acknowledge the broader table is a fishing
  expedition.
- **Kalman filter tuning.** The noise ratio Q/R controls how fast the
  Kalman tracks. A poorly chosen Q makes the filter either too sluggish
  (Q too small — acts like OLS) or too noisy (Q too large — residual
  is white noise). We fix Q = 1e-5 before looking at results; the
  sensitivity table will show robustness.
- **ETF ≠ single-name credit.** The RV signals are computed on ETF
  log-price ratios, not individual credit names. The spread includes
  duration, liquidity, and composition effects that a single-name CDS
  hedge would not. This limits the economic interpretation.
- **Regime hindsight.** The expanding-median vol regime uses all
  pre-`t` history. In live trading, the regime label at `t` is known,
  but the decision to condition on it is informed by this analysis.
  The honest test is whether the regime classification adds value on
  out-of-sample data (Sprint 5).
- **Sample size per regime cell.** If `equity_first` covers only 15%
  of days (~717 obs), the half-life estimate may be noisy. We report
  `n_obs` per cell to flag underpowered comparisons.

## Out of Scope

- P&L, Sharpe, or any backtest. Sprint 5 trades these signals.
- Portfolio optimization or position sizing.
- Real-time signal computation or streaming.
- PCA-based rates factor (we use dgs10 as a single-factor proxy).
- Time-varying ETF maturity lookup for DV01 proxy.
- CDS-based hedge ratios (single-name CDS data not available).
- Regime-switching models (Markov, HMM). We use simple threshold rules.

## Dependencies

**Existing (no version change):** `pandas`, `numpy`, `statsmodels`
(ADF, OLS), `pyarrow`, `matplotlib`, `pycredit` (Sprint 2).

**New:** `filterpy` (Kalman filter) or hand-rolled univariate Kalman
(~20 lines). If `filterpy` adds install friction, implement inline —
the 1D Kalman is trivial.

**Prior sprint outputs:**
- `sprint-v1` tag: `features.parquet` (50 cols), `credit_market_data.parquet`
- `sprint-v2` tag: `pycredit` module (built, importable from venv)
