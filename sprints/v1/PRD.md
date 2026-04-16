# Sprint v1 — PRD

## Overview

Phase 1 of the Credit Trading Lab. Build a clean, reproducible data pipeline
and validated spread signals from ETF adjusted prices (HYG, LQD, SPY, IEF).
Outputs are parquet files with a fixed schema, consumable without modification
by downstream backtests and the Streamlit dashboard. No trading logic this
sprint — the deliverable is trustworthy features.

## Economic Hypothesis

The three ETF-derived spreads proxy credit-market risk premia:

- **HYG / IEF** — high-yield credit vs duration-matched Treasuries → HY credit
  risk premium (with duration/liquidity noise).
- **LQD / IEF** — investment-grade credit vs Treasuries → IG credit risk
  premium.
- **HYG / LQD** — HY vs IG → compensation for moving down the credit-quality
  ladder.

In log-price-ratio form these series are non-stationary in levels but
mean-revert around slow-moving regimes; rolling z-scores should be
approximately stationary and useful as the raw input to later trading
signals. Phase 1 does not test any trading hypothesis — it tests that the
signals we build have the statistical properties required to be used.

## Falsification Criteria

Pre-registered. Any one of these fails the sprint:

1. `data/processed/features.parquet` contains NaNs in any row after the
   warmup window (first 252 business days).
2. ADF test on any of the 9 z-score columns fails to reject a unit root at
   p > 0.05.
3. Any z-score column has rolling mean outside [-0.2, 0.2] or rolling std
   outside [0.8, 1.2] on the post-warmup sample.
4. Any z-score column has sample kurtosis > 20 (suggests broken
   normalization or untreated outliers).
5. Any raw ticker file has a business-day gap > 5 days inside its date
   range.
6. Row counts are not conserved across pipeline stages (logged in/out
   counts disagree beyond the documented drop reasons).

## Signal Definition

Let `P_t(T)` be the adjusted close for ticker `T` on business day `t`.

### Returns
- `log_ret_t(T) = ln(P_t(T) / P_{t-1}(T))`
- Indexing: `log_ret` at date `t` uses only information available at the
  close of `t`.

### Rolling annualized volatility
- `vol_w_t(T) = std(log_ret over last w obs) * sqrt(252)`
- Windows: `w ∈ {21, 63, 126}`

### Spread series (log price ratios)
- `hy_spread_t   = ln(P_t(HYG) / P_t(IEF))`
- `ig_spread_t   = ln(P_t(LQD) / P_t(IEF))`
- `hy_ig_t       = ln(P_t(HYG) / P_t(LQD))`

### Rolling z-scores
- `z_w_t(X) = (X_t - mean(X over last w obs)) / std(X over last w obs)`
- Windows: `w ∈ {63, 126, 252}`
- Applied to `X ∈ {hy_spread, ig_spread, hy_ig}` → 9 z-score columns.

### Parameters (explicit)
- `return_lag = 1`
- `vol_windows = [21, 63, 126]`
- `z_windows   = [63, 126, 252]`
- `spread_series = ["hy_spread", "ig_spread", "hy_ig"]`

## Data

- **Source**: yfinance `Ticker.history(auto_adjust=False)`, `Adj Close` used.
- **Tickers**: HYG, LQD, SPY, IEF.
- **Frequency**: daily, business days only.
- **Date range**: 2007-04-11 (HYG inception) through run date; `--start` and
  `--end` CLI args for reproducibility.
- **Known biases**:
  - yfinance occasionally has missing bars; audit required.
  - Adjusted close bakes in dividend/split restatements → not strictly
    point-in-time. Acceptable for Phase 1; documented limitation.
  - HYG/LQD are ETF proxies, not cash-bond spreads (basis, duration, and
    liquidity mismatch vs OAS/CDX). Acknowledged, not corrected.
- **PIT handling**: for Phase 1, adjusted close is treated as observable at
  `t`'s close. No look-ahead is allowed beyond that.
- **Missing data**: forward-fill up to 1 business day; rows still missing
  are dropped with count logged.
- **Corporate actions**: handled upstream by yfinance adjustment.

## Success Metrics

### Data hygiene
- Per ticker: zero missing business days inside the range after fill.
- Max business-day gap ≤ 5.
- Row counts logged at every stage (ingest → features → z-scores).

### Signal quality (post-warmup, i.e. dropping first 252 obs)
- Zero NaNs in `features.parquet`.
- ADF p-value < 0.05 on each of the 9 z-score columns.
- For each z-score column: sample mean ∈ [-0.2, 0.2], std ∈ [0.8, 1.2],
  kurtosis < 20.
- Log-return lag-1 autocorrelation: |ρ| < 0.10 per ticker.
- Z-score lag-1 autocorrelation: ρ > 0.85 (expected, given rolling
  smoothing; flag if violated).

### Reproducibility
- Ingest with a fixed `--end` date produces byte-identical parquet files on
  re-run (yfinance determinism permitting).
- Notebook `01_signal_validation.ipynb` runs top-to-bottom without manual
  intervention.

## Research Architecture

```
data/raw/{ticker}.parquet        ← yfinance ingest (one file per ticker)
data/processed/features.parquet   ← merged features + spreads + z-scores
notebooks/01_signal_validation.ipynb
signals/
  load.py          — yfinance ingest, parquet write
  features.py      — returns, rolling vol, spread construction
  zscore.py        — rolling z-score computation
  pipeline.py      — orchestrator that builds features.parquet end-to-end
tests/
  test_signals.py  — schema, NaN, and invariant assertions
```

Data flow: `yfinance → data/raw/*.parquet → signals.pipeline.build()
→ data/processed/features.parquet → notebook/dashboard`.

Each module is pure: takes dataframes in, returns dataframes out. `load.py`
is the only I/O boundary to yfinance; `pipeline.py` is the only I/O
boundary to disk for processed outputs.

## Parquet Schemas

### `data/raw/{ticker}.parquet` (one per ticker)
- **Index**: `date`, `DatetimeIndex`, tz-naive, business-day.
- **Columns** (float64 unless noted):
  - `open`, `high`, `low`, `close`, `adj_close`, `volume` (int64).

### `data/processed/features.parquet`
- **Index**: `date`, `DatetimeIndex`, tz-naive, business-day, monotonic
  increasing, unique.
- **Columns** (all float64):
  - Per ticker `T ∈ {HYG, LQD, SPY, IEF}`:
    `{T}_adj_close`, `{T}_log_ret`, `{T}_vol_21`, `{T}_vol_63`,
    `{T}_vol_126`
  - Spreads: `hy_spread`, `ig_spread`, `hy_ig`
  - Z-scores (9 columns): `hy_spread_z63`, `hy_spread_z126`,
    `hy_spread_z252`, `ig_spread_z63`, `ig_spread_z126`,
    `ig_spread_z252`, `hy_ig_z63`, `hy_ig_z126`, `hy_ig_z252`
- **Total columns**: 4 × 5 + 3 + 9 = 32.

## Risks & Biases

- **yfinance reliability** — bars can change across pulls; mitigated by
  snapshotting raw parquet and keeping the pull date in a sidecar file.
- **ETF basis risk** — HYG/LQD track indices with lag, premium/discount, and
  liquidity noise. Not a true OAS. Phase 1 limitation, not mitigated.
- **Look-ahead** — rolling stats must be trailing only. Explicit shift-test
  in validation.
- **Regime dependence** — 2007–present spans GFC, 2013 taper, 2020 COVID,
  2022 rate shock. Z-score windows up to 252 days will bleed regime info;
  flagged but not corrected.
- **Multiple testing** — 9 z-score columns × multiple stat tests. Phase 1
  is descriptive, so no correction; noted for downstream sprints.

## Out of Scope

- Cash-bond / OAS / CDX spreads.
- Intraday data.
- Portfolio construction, backtesting, transaction-cost models.
- ML features or regime models.
- Streamlit dashboard implementation (this sprint produces its input).
- Execution, risk, and model layers.

## Dependencies

- Existing: `yfinance==1.2.0`, `pandas==2.3.3`, `numpy==2.0.2`,
  `matplotlib==3.9.4`.
- New (to add to requirements.txt): `pyarrow` (parquet engine),
  `statsmodels` (ADF), `jupyter` (notebook).
- No prior-sprint outputs (v1).
