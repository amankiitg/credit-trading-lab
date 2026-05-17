# Sprint v5 — Backtest + Thesis Test + Portfolio

## Summary

Sprint v5, the final sprint of Tier 1, put the equity-credit-lag
thesis on trial as a traded strategy: Strategy A trades the RV1
(HY/IG) residual unconditionally; Strategy B trades it only on
`equity_credit_lag == equity_first` days. The pre-registered headline
test (C27) asks whether the incremental Sharpe ΔS = Sharpe(B) −
Sharpe(A) is positive with a bootstrap CI excluding zero. **It is
not — ΔS = −0.41, 95% CI [−0.82, −0.01].** The rejection holds
out-of-sample, across all 27 parameter cells, in both subperiods, and
against the random benchmark. **Verdict: the thesis is rejected at
the trading level.** The equity-credit lag is a real statistical
effect (Sprint v3's C22: ~43% faster mean-reversion on `equity_first`
days) but not a monetizable regime filter. The engine-correctness
gates (C25, C26) pass, so this is a correctly-measured rejection, not
a bug — which per the PRD is a successful sprint.

## Hypothesis & Falsification Criteria

**Pre-registered hypothesis.** Equity markets incorporate risk faster
than credit; on `equity_first` days the HY/IG spread is mid-catch-up,
so a dislocation in the hedged residual is more likely a genuine lag
artifact that will revert. Filtering RV1 entries to `equity_first`
should raise risk-adjusted return.

| ID | Criterion | Threshold | Result |
|---|---|---|---|
| C25 | Engine correct, no leakage | hand-calc < 1e-6; perturb test | **PASS** |
| C26 | Control strategy real | A ≥ 30 trades, finite Sharpe | **PASS** (94 trades, Sharpe 0.59) |
| C27 | **Thesis** — ΔS > 0, bootstrap CI excludes 0 | CI lower > 0 | **FAIL** (ΔS −0.41, CI [−0.82, −0.01]) |
| C28 | OOS walk-forward ΔS > 0 | locked thresholds | **FAIL** (OOS ΔS −0.61) |
| C29 | Strategy B beats random p95 | excess Sharpe > 0 | **FAIL** (B 1.42 vs p95 1.70) |
| C30 | Robust across grid + subperiods | ≥ 75% cells, both halves | **FAIL** (0/27 cells, both halves < 0) |
| C31 | Not one-trade-driven | no trade > 25% of P&L | **FAIL** (B: one trade = 49%) |

Five of seven criteria fail; the two that pass are engine-correctness
gates, not thesis tests. The hypothesis is comprehensively rejected.

## Data Pipeline

**Source:** `data/processed/features.parquet` (4784 × 56) from the
`sprint-v3` tag — no new ingestion. Daily, 2007-04-11 → 2026-04-15,
4-ETF universe (HYG, LQD, SPY, IEF), all alive the whole sample (no
survivorship). `data/raw/credit_market_data.parquet` supplies the
rates legs for RV2/RV3; `data/benchmarks/random_baseline.parquet`
(Sprint 1, 3000 random-entry paths) is the C29 benchmark.

**Transforms (V5), in order:**
1. `signals.rv_signals.build_all_residuals` — 3 RV pairs × 3 hedge
   methods (OLS/Kalman/DV01); all trailing (Sprint 3 verified).
2. `signals.rv_signals.trailing_zscore` — 63-day z of each residual.
3. `execution.position.run_state_machine` — z + thresholds →
   {−1,0,+1}; Strategy B passes an `equity_first` entry gate.
4. `backtest.engine.run` — positions → trade ledger + daily
   mark-to-market net P&L; fills lagged 1 trading day.
5. `backtest.metrics` / `ab_test` / `benchmarks` / `failure` /
   `regime_table` / `risk.portfolio` — analysis layer.

**Known biases:** ETF-proxy residuals (not single-name credit);
next-day-close fills (no intraday); flat 0.40% borrow assumption
(not a historical borrow curve); the regime label `equity_credit_lag`
is trailing but `vol_regime`'s expanding median weakly leaks
(inherited Sprint 3 caveat — not used as the B gate). One position
open across the 2018/2019 OOS boundary contributes post-split
mark-to-market to the OOS slice; at most one trade, immaterial.

**Rows dropped:** none. 4784 bars in, 4784 out; the 378-day warmup is
applied analytically (positions forced flat, not rows deleted).

## Signal Behavior

The traded signal is the RV1 OLS residual and its 63-day z-score.
Distribution and stationarity were characterised in Sprint v3
(C19–C21 all passed: ADF p < 0.05, half-life ≈ 18–26 days for OLS).
Sprint v5 adds the *trading* behaviour:

**Trade counts and holding.** Strategy A takes 94 round-trips over
19 years (turnover ≈ 5/yr, avg hold 15.7 days). Strategy B, gated to
`equity_first` (~8% of days), takes only 14 (turnover ≈ 0.7/yr, avg
hold 21.2 days). The gate discards ~85% of trades.

**Per-strategy metrics (in-sample, OLS hedge, default thresholds):**

| metric | A (no filter) | B (equity_first) |
|---|---|---|
| annualised Sharpe | 0.591 | 0.179 |
| Sortino | 0.946 | 0.269 |
| hit rate | 80.9% | 64.3% |
| trades | 94 | 14 |
| total net P&L | $760,372 | $50,650 |
| max drawdown | −$152,025 | −$30,245 |
| avg holding days | 15.7 | 21.2 |

**IC / decay.** Not re-computed in v5 — the signal is frozen from
Sprint v3. The relevant v5 statistic is the *strategy* Sharpe, above.
A baseline comparison (the random-entry baseline) is in Backtest
Results below.

**Regime behaviour.** The regime performance table
(`data/results/regime_performance.parquet`, 28 rows) shows the
sharpest evidence against the thesis: Strategy B's daily P&L
restricted to `equity_first` days has Sharpe **−0.18** — even inside
its own gating regime the filtered strategy loses risk-adjusted
return. On the same `equity_first` days, the unfiltered Strategy A
scores **+0.64**. Strategy A's best regime cell is
`equity_credit_lag = credit_first` (Sharpe 1.26) — the opposite of
the thesis regime.

## Backtest Results

Pre-registered metrics, all net of the modelled costs (1.5bp
half-spread × 2 legs × 2 sides + 0.5bp slippage + 0.40%/yr borrow;
fixed $1M notional, DV01-hedged).

### Incremental Sharpe — the headline (C27)

ΔS = Sharpe(B) − Sharpe(A) = 0.179 − 0.591 = **−0.412**. Stationary
block bootstrap (1000 resamples, block 21, seed 20260516):

- 95% CI = **[−0.815, −0.009]** — entirely below zero.
- fraction of resamples with ΔS > 0 = **2.1%**.

The `equity_first` filter does not add risk-adjusted return; it
removes it. See `sprints/v5/plots/01_ab_equity.png` — the running
B − A difference is below zero for essentially the whole sample.

### Out-of-sample (C28)

Thresholds grid-calibrated on 2007 → 2018-12-31 (best Strategy-B
train Sharpe → entry 1.5 / exit 1.0 / stop 3.0), locked, applied to
2019 → 2026. **OOS ΔS = −0.610** — more negative than in-sample. Not
an artifact of in-sample tuning.

### Benchmarks (C29)

Comparison on the per-trade Sharpe basis (`mean/std·√n`) the Sprint-1
random baseline was built on:

| | per-trade Sharpe | vs random p95 (1.70) |
|---|---|---|
| Strategy A | 5.00 | +3.30 — clears it |
| Strategy B | 1.42 | −0.28 — **fails** |
| buy-hold HYG (ann. daily) | 0.44 | context |

C29 is pre-registered on Strategy B, which does **not** beat a lucky
random-entry path. Strategy A clears the benchmark comfortably — the
*base* RV1 signal is a real edge; the filter is what sinks it.

### Subperiod & parameter sensitivity (C30)

- 27-cell entry × exit × stop grid: ΔS > 0 in **0** cells. Range
  [−0.69, −0.07], median −0.35.
- Sample halves (split 2016-09-15): first-half ΔS −0.35, second-half
  −0.49 — both negative.

The rejection is not crisis-specific and has no favourable parameter
corner. See `04_robustness_grid.png`, `05_subperiod.png`.

### Failure analysis (C31)

`data/results/failure_analysis.parquet` — worst 5 trades per
strategy. Strategy A's max single-trade P&L share is 10% (PASS);
Strategy B's is **49%** (FAIL) — B's result is one trade plus noise,
inevitable with only 14 trades. A side-finding from the slide:
several of B's losing trades exit by `take_profit` (the z reverted to
mid-band) yet still lose money, because the 63-day rolling z-mean
drifts toward the residual during the hold — a Sprint-3 signal-design
artifact (trading the z of a residual whose z-window mean moves).

### Hedge-method robustness panel

| hedge | Sharpe A | Sharpe B | ΔS | 95% CI |
|---|---|---|---|---|
| OLS | 0.59 | 0.18 | −0.41 | [−0.86, +0.03] |
| Kalman | 0.71 | 0.09 | −0.62 | [−1.18, −0.23] |
| DV01 | −0.08 | −0.01 | +0.07 | [−0.33, +0.47] |

DV01 shows a nominally positive ΔS, but both DV01 strategies have
~zero Sharpe (they barely trade profitably) and the CI straddles zero
widely — noise on two non-strategies, not a rescue. Under the two
hedges that produce a real strategy, ΔS is firmly negative.

### Multi-signal portfolio

RV1/RV2/RV3 Strategy-B books combined: equal-weight Sharpe 0.20,
inverse-vol 0.13 — neither beats the best single signal (RV2_B,
0.22). Combining three weak gated strategies averages the weakness.

## Key Findings

1. **The equity-credit lag is real but not tradeable as a filter.**
   Sprint v3 measured a genuine effect — `equity_first` days
   mean-revert ~43% faster (C22). Sprint v5 shows that gating trades
   on it *destroys* risk-adjusted return: ΔS = −0.41, CI below zero,
   and the same sign out-of-sample, across every parameter cell, in
   both subperiods. A statistical effect is not an edge.

2. **The mechanism is lost diversification, not a weak signal.** The
   filter cuts 94 trades to 14. Faster mean-reversion on the
   survivors cannot compensate for trading 7× less often. Sprint v4's
   shuffled-regime baseline already foreshadowed this — `equity_first`
   does not raise the *frequency* of tradeable dislocations.

3. **The unfiltered base signal is the better strategy.** Strategy A
   — RV1 with no regime filter — has a net Sharpe of 0.59, an 81% hit
   rate, and a per-trade Sharpe of 5.0 that clears the random
   benchmark by +3.3. The Tier-1 work produced a tradeable credit RV
   signal; the regime overlay was the wrong thing to add to it.

4. **Strategy B loses even inside its own regime.** B's P&L
   restricted to `equity_first` days has Sharpe −0.18. The regime the
   thesis is built on is the regime where the gated strategy performs
   worst — the cleanest single refutation in the sprint.

5. **Pre-registration did its job.** The falsification criteria were
   written before any backtest ran. The result is a clean,
   defensible rejection rather than a fishing expedition — and the
   honest negative is more valuable than a curve-fit positive would
   have been.

## Limitations

- **Cost assumptions.** Flat 0.40% borrow and 1.5bp half-spread are
  estimates; a 2–3× higher cost would lower both strategies further
  but does not change the *sign* of ΔS (the gate removes value gross
  too). A historical borrow curve was not used.
- **ETF-proxy residuals.** Spreads are ETF log-price ratios, not
  single-name credit; duration/liquidity/composition effects are
  bundled in. Limits the economic interpretation.
- **Small-sample B.** 14 trades is too few for a precise Sharpe;
  this is *why* the bootstrap CI is wide — but the CI is still
  entirely below zero, so the rejection survives the imprecision.
- **Multiple testing.** 3 hedge methods × A/B × 27-cell grid is a
  large search; C27 is the only pre-registered cell, the rest are
  explicitly robustness/exploratory.
- **z-mean drift artifact.** Trading the z-score of a residual whose
  63-day z-window mean drifts means a "z reverted" exit is not a
  "residual reverted" exit. This dilutes both strategies equally, so
  it does not bias ΔS, but it caps absolute performance.
- **No paper/live trading.** This is a historical simulation; fills
  are next-day-close, market impact is unmodelled. (Tier 2.)

## Reproducibility

- **Seeds:** bootstrap seed 20260516; the leakage/portfolio tests use
  fixed `np.random.default_rng` seeds. No other stochastic step.
- **Data snapshot:** `features.parquet` from the `sprint-v3` tag
  (4784 × 56); `random_baseline.parquet` from `sprint-v1`.
- **Code:** `sprint-v5` tag. Foundation committed at `a9306a1`.
- **Dependencies:** no new packages — the block bootstrap is
  hand-rolled.

**To regenerate every result, plot, and table:**

```bash
source venv/bin/activate
# 1. features.parquet prerequisite (skip if present)
PYTHONPATH=python/credit python3 -m signals.pipeline
# 2. A/B + trade ledger
PYTHONPATH=python/credit python3 -m backtest.ab_test
# 3. notebook — runs A/B, failure slide, benchmarks, robustness,
#    regime table, portfolio, and the C25–C31 checklist; writes
#    failure_analysis.parquet, regime_performance.parquet, 6 plots
python3 scripts/build_notebook_v5.py
PYTHONPATH=python/credit jupyter nbconvert --to notebook --execute \
  --inplace notebooks/05_backtest.ipynb --ExecutePreprocessor.timeout=600
# 4. test suite
PYTHONPATH=python/credit python3 -m pytest tests/ -q
```

## Next Steps

These would flip or sharpen the result rather than re-litigate it:

1. **Trade the base signal, drop the regime overlay.** Strategy A
   (net Sharpe 0.59, per-trade Sharpe 5.0) is the actual Tier-1
   deliverable. A Tier-2 sprint should harden A — proper sizing, a
   borrow-cost feed, capacity analysis — and leave `equity_first`
   out of the entry logic.

2. **Test the lag as a *sizing* input, not a *gate*.** Instead of
   refusing non-`equity_first` trades, take every RV1 trade but
   scale notional up on `equity_first` days. This keeps the 94-trade
   diversification while still expressing the C22 half-life finding;
   it is the one formulation that could plausibly give ΔS > 0.

3. **Fix the z-mean-drift artifact.** Trade the residual against a
   fixed (expanding, or long-window) mean rather than a 63-day
   rolling mean, so a z-reversion is a P&L-reversion. Re-run A/B.

4. **Intraday lag detection.** The thesis is about equity leading
   credit; a daily `equity_credit_lag` label may be too coarse. An
   intraday lead-lag estimate could be a genuinely leading signal
   rather than a near-coincident one.

5. **Tier 2 — paper trading.** With Strategy A as the chosen
   strategy, build the live-data refresh loop and a simulated book
   so the Sprint-4 dashboard's Today View becomes real order tickets.
