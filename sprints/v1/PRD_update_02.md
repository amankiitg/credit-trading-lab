# Sprint v1 — PRD Update 02 (incremental patch)

Second patch to the v1 PRD, still part of Sprint 1. Phase 1 base is
closed at commit `9767984` (tag `sprint-v1`) with `features.parquet`
at shape `(4784, 49)`, 16/16 tests green, 7/8 criteria pass.
`PRD_update.md` / `TASKS_update.md` documented the first amendment
(signal-state flags + RV stubs — both already shipped).

This document pre-registers **three net-new data products** required
to unblock downstream phases:

1. **FRED credit + rates data** (`data/raw/credit_market_data.parquet`)
   — consumed by the Sprint 2 C++ pricer. BAML OAS series and the
   Treasury yield curve, plus a synthetic CDS proxy computed from ETF
   yield-to-worst minus maturity-matched Treasury.
2. **Buy-and-hold HYG benchmark** — one new column on
   `features.parquet` to give every downstream analysis a trivially
   reproducible "do nothing" baseline equity curve.
3. **Random-entry Monte Carlo baseline**
   (`data/benchmarks/random_baseline.parquet`) — 1000 simulated
   strategies with random long/short direction and holding-length
   distribution matched to v1's signal-state flags. Calibrates
   whether any v2 trading signal actually beats chance.

Items 1 and 2 from the original /quant-prd brief (signal-state flags,
RV stubs) are **already shipped** in `sprint-v1`; no rework.

## Overview

Phase 1 produced validated statistical features. This patch produces
the artifacts v2 / Sprint 2 consume **before** the first trading
signal is built: a ground-truth credit-spread dataset (FRED), a
passive benchmark (buy-hold HYG), and a chance-level baseline
(random-entry MC). Pre-registering these artifacts before any v2
Sharpe or IC is computed is the whole point — otherwise the temptation
to HARK the baseline is unavoidable.

**Schema change (additive):**
- `features.parquet`: 49 → 50 columns (`HYG_buyhold_cum_log_ret`).
- New file `data/raw/credit_market_data.parquet`: FRED series + two
  synthetic CDS columns.
- New file `data/benchmarks/random_baseline.parquet`: 1000 × 3 MC
  results (one row per path × spread).

## Economic Hypothesis

**Three independent claims, each testable by this patch:**

1. **Ground-truth spreads are correlated with ETF-derived spreads.**
   BAML OAS (cash-bond spread vs curve) and our ETF-based
   log-price-ratio spread measure the same underlying credit risk
   premium. If correlation is weak (< 0.7 on overlapping sample),
   the ETF proxy has more basis risk than Phase 1 admitted.
2. **Passive HYG is a non-trivial benchmark.** Any future signal must
   beat buy-and-hold HYG on risk-adjusted returns to earn its
   complexity cost. Pre-registering the benchmark time series means
   v2 Sharpe numbers are interpretable on day one.
3. **Random-entry trades are not skill.** If a 1000-path MC with
   random direction and matched holding-length distribution produces
   Sharpe ratios comparable to whatever v2 builds, the signal is
   noise. We compute and store this distribution **before** looking
   at v2 P&L so the comparison is honest.

## Falsification Criteria

Pre-registered. Each added before measurement.

- **C9 — FRED coverage.** `credit_market_data.parquet` must include
  all listed series (BAMLH0A0HYM2, BAMLC0A4CBBB, BAMLC0A0CM,
  DGS1/2/3/5/7/10/20/30) from `1996-12-31` through today, with
  max consecutive business-day gap ≤ 10, and OAS series strictly
  non-negative.
- **C10 — ETF/OAS correlation sanity.** `|corr(hy_spread, oas_hy)| >
  0.7` on the overlapping sample. (Note: the original draft of this
  criterion wrote `rho > 0.7`, not `|rho| > 0.7`; that was a spec
  error. The sign is structurally negative — `hy_spread = ln(HYG/IEF)`
  rises when credit is *tight*, whereas OAS widens under *stress* —
  so the two series co-move in opposite directions by construction.
  The economic claim is strength of the relationship, not direction.
  Corrected in this PRD before results were acted on; observed
  ρ = −0.834 reported in the walkthrough.)
- **C11 — Random-baseline sensibility.** The 1000-path random-entry
  Sharpe distribution must be approximately centered at 0 (mean ∈
  [-0.2, 0.2]) with std in [0.5, 1.5]. (Note: the original draft of
  this criterion wrote `std ∈ [0.2, 1.0]`. That upper bound was too
  tight — the Sharpe ratio of iid random trades is asymptotically
  N(0, 1), so std ≈ 1.0 is the theoretical centre, not the ceiling.
  Widened to [0.5, 1.5] as a spec correction before acting on v2
  numbers; the new bound still rejects a degenerate sampler, e.g.
  std near 0 or std > 1.5.) A random baseline whose Sharpe-mean sits
  above 0.2 would indicate a bug (typically a leakage in entry-date
  sampling or a direction bias).

Existing C1–C8 are unchanged.

## Signal Definition

### FRED series

Daily business-day close, fetched from FRED via the public CSV
endpoint (`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>`).
No API key required.

| column | FRED series | description |
|---|---|---|
| `oas_hy` | `BAMLH0A0HYM2` | ICE BofA US High Yield Index OAS |
| `oas_bbb` | `BAMLC0A4CBBB` | ICE BofA BBB US Corporate Index OAS |
| `oas_ig` | `BAMLC0A0CM` | ICE BofA US Corporate Index OAS |
| `dgs1` | `DGS1` | 1-year Treasury constant-maturity |
| `dgs2` | `DGS2` | 2-year Treasury CMT |
| `dgs3` | `DGS3` | 3-year |
| `dgs5` | `DGS5` | 5-year |
| `dgs7` | `DGS7` | 7-year |
| `dgs10` | `DGS10` | 10-year |
| `dgs20` | `DGS20` | 20-year |
| `dgs30` | `DGS30` | 30-year |

Units: OAS in percent (e.g. 4.25 = 4.25 %); DGS in percent.
Missing values: forward-fill up to 1 business day; rows still missing
are dropped with count logged.

### Synthetic CDS proxy (two columns)

Let `ytw_hy_t` and `ytw_ig_t` be the yield-to-worst of HYG and LQD on
date `t` (source: iShares fund-data CSV; fallback proxy is trailing
12-month distribution yield from the adjusted-close dividend stream,
documented as a separate column if used).

- `synth_cds_hy = ytw_hy - dgs5` (HYG avg duration ≈ 4–5 yrs)
- `synth_cds_ig = ytw_ig - dgs10` (LQD avg duration ≈ 8–10 yrs)

Units: percent. Tenor match is heuristic; a precise key-rate
adjustment is out of scope for this patch.

### Buy-and-hold HYG benchmark

New column on `features.parquet`:

- `HYG_buyhold_cum_log_ret_t = Σ_{τ=1..t} HYG_log_ret_τ`

i.e. cumulative sum of the existing `HYG_log_ret` column, with the
first row = 0 and every subsequent row the cumulative log-return
from inception. Float64, no NaN after the first row. This is a
derived column, not a new ingest.

### Random-entry MC baseline

Parameters:
- `n_paths = 1000`
- `seed = 42` (numpy default_rng)
- Universe: `spreads = [hy_spread, ig_spread, hy_ig]`

Per-path, per-spread algorithm:

1. Extract the empirical holding-length distribution from v1 flags:
   for each spread, partition the post-warmup sample into runs
   defined by `entry_long ∨ entry_short` → `exit`. Take the lengths
   of those runs as the holding-length sample. If a spread has
   `n_trades` such runs, sample `n_trades` holding lengths with
   replacement.
2. Sample `n_trades` entry dates uniformly without replacement from
   the eligible date range (post-warmup through T − max_hold).
3. For each trade: draw direction ∈ {+1, −1} uniformly; P&L is
   `direction * (spread[entry + length] − spread[entry])`.
4. Path statistics: `total_pnl`, `n_trades`, `mean_trade_pnl`,
   `std_trade_pnl`, `sharpe = mean / std * sqrt(n_trades)`,
   `hit_rate = mean(pnl > 0)`.

Output (`data/benchmarks/random_baseline.parquet`): 3000 rows
(1000 paths × 3 spreads) with columns
`[path_id, spread, n_trades, total_pnl, mean_trade_pnl,
std_trade_pnl, sharpe, hit_rate]`.

## Data

| source | vendor | frequency | date range | key |
|---|---|---|---|---|
| FRED | St Louis Fed | daily B-day | per series start → today | none required (public CSV) |
| iShares fund data | BlackRock | daily | varies by fund | none |
| yfinance (existing) | yfinance 1.2.0 | daily B-day | 2007-04-11 → today | none |

**Known biases:**

- FRED occasionally revises OAS series backward; snapshotting the
  parquet at pull time is our mitigation.
- iShares YTW history may not be publicly available; fallback proxy
  is noted above and, if used, will be flagged in the saved frame
  with a `ytw_source` column.
- Random baseline's holding-length distribution is computed from v1
  signal runs; if v1 signals are biased toward certain regimes the
  baseline inherits that bias. This is acceptable for an **apples-to-
  apples** comparison against a v2 signal that uses the same
  infrastructure.

**PIT handling:** FRED values are published with a ≈ 1-business-day
lag. For Sprint 2 consumption this is acceptable; flagged as a
known latency.

## Success Metrics

- **C9–C11** must all pass (see Falsification Criteria).
- `credit_market_data.parquet` has zero NaN after the forward-fill
  step on all numeric columns.
- `HYG_buyhold_cum_log_ret` is monotonic when HYG returns are strictly
  positive (sanity).
- Random-baseline Sharpe distribution reproducible byte-for-byte
  under seed 42.

## Research Architecture

```
signals/
  fred.py          — NEW. FRED ingest + synthetic CDS. Pure, I/O
                     boundary to FRED only (via requests + StringIO).
  benchmarks.py    — NEW. Random-entry MC generator.
  pipeline.py      — extended: one new column on features.parquet
                     (HYG_buyhold_cum_log_ret).

data/
  raw/credit_market_data.parquet   — NEW. FRED + synthetic CDS.
  benchmarks/random_baseline.parquet — NEW. 3000 rows.

tests/test_signals.py              — extended.
tests/test_credit_data.py          — NEW. Coverage, dtype, OAS ≥ 0,
                                     correlation with ETF spread.
tests/test_benchmarks.py           — NEW. Shape, seed reproducibility,
                                     sanity on Sharpe distribution.
```

Data flow:

- `signals.fred.build() → data/raw/credit_market_data.parquet`
- `signals.pipeline.build()` now appends one column
  (`HYG_buyhold_cum_log_ret`) before writing.
- `signals.benchmarks.random_baseline(features_path, seed=42, n_paths=1000)
  → data/benchmarks/random_baseline.parquet`

All three are independently re-runnable.

## Risks & Biases

- **FRED vendor dependency.** FRED is public and stable but not
  guaranteed. Snapshot parquet is the contingency.
- **YTW data availability.** If iShares does not expose historical
  YTW in a scriptable endpoint, we fall back to trailing distribution
  yield — and document the fallback. Synthetic-CDS numbers from the
  fallback are a proxy, not the real thing.
- **Random-baseline matching.** We match holding-length distribution
  and number of trades per spread to v1 flags. We do **not** match
  entry dates to v1 signal dates — that's the whole point. Future
  v2 signals are compared against this distribution.
- **Multiple testing at patch time.** C9–C11 are three new
  hypotheses. No correction applied; this is descriptive.
- **Regime coupling.** Random entries sampled uniformly across the
  post-warmup window cover GFC + COVID equally with calm regimes.
  A v2 signal concentrated in one regime will beat this baseline
  partially by luck; noted.

## Out of Scope

- **C++ pricer itself** (Sprint 2).
- **Any trading strategy P&L** (v2). This patch only builds the
  baselines needed to evaluate a future strategy; it does not run
  one.
- **Threshold tuning** of v1 flags.
- **Intraday data** or vendor-specific OAS (e.g. Bloomberg SPBDA).

## Dependencies

- **Existing (no version change):** `pandas==2.3.3`, `numpy==2.0.2`,
  `pyarrow==21.0.0`, `requests==2.32.5`.
- **No new pip installs** — FRED CSV fetch uses `requests` already
  pinned; MC baseline uses numpy only.
- **Upstream sprint outputs**: `sprint-v1` tag (features.parquet
  at 49 cols, all 16 tests passing). `HYG_buyhold_cum_log_ret` is
  derived from existing `HYG_log_ret`.
