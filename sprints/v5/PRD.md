# Sprint v5 — Backtest + Thesis Test + Portfolio

Weeks 10-12. **Final sprint of Tier 1.** Sprint 1 built the data
pipeline and spread signals; Sprint 2 the C++ pricer; Sprint 3 the
RV residuals, regime classifiers, and the C22 half-life finding;
Sprint 4 the visualizer. Sprint 5 is where the central thesis is
finally put on trial as a **traded strategy with costs** — not a
half-life statistic, but a net-of-cost equity curve with a
pre-registered, bootstrapped pass/fail.

## Overview

Build a backtest engine and run the equity-credit-lag thesis as an
A/B test: **Strategy A** trades the RV1 (HY/IG) residual with no
regime filter (control); **Strategy B** trades the identical
residual but only when `equity_credit_lag == "equity_first"`. Same
engine, same thresholds, same sizing, same costs. The thesis is
**confirmed** only if the incremental Sharpe (B − A) is positive
*and* its bootstrap 95% confidence interval excludes zero — and
survives an out-of-sample walk-forward. Sprint 3 showed equity_first
days mean-revert faster (C22, half-life 43% shorter); Sprint 4
showed that faster reversion does *not* mean more-frequent extreme
z. Sprint 5 answers the only question that matters for a trader:
**after costs, does the regime filter add risk-adjusted return?**

## Economic Hypothesis

**Causal story.** Equity markets are more liquid and trade at higher
frequency than credit. New risk information is priced into equities
first; credit (here, HY vs IG ETF spreads) lags. On days the
cross-correlation detector labels `equity_first`, equity has already
moved and the HY/IG spread is mid-catch-up — so a dislocation in the
hedged residual is more likely to be a genuine lag artifact that
will mean-revert, rather than noise or a regime change. Filtering RV1
entries to `equity_first` days should therefore raise the *quality*
of trades: shorter holding period, higher hit rate, better Sharpe.

**Who is on the other side.** Liquidity providers and credit market
makers who are slower to update quotes; index-tracking flows that
move HYG/LQD mechanically; investors treating the spread move as
permanent when it is transitory. We are paid the lag-convergence
premium for warehousing the position over the catch-up window.

**Why it could be wrong.** (1) The lag may be real but smaller than
costs — ETF half-spreads + borrow eat a 1-2 week RV trade. (2)
`equity_first` may be a coincident label, not a leading one — the
21-day cross-correlation could be picking up contemporaneous
co-movement, in which case the filter adds nothing (Sprint 4's
shuffled-regime baseline already hinted HIGH-frequency is
baseline-like). (3) The edge may be entirely a 2008/2020 crisis
artifact and absent in calm regimes. (4) The residual itself may not
be tradeable under any hedge method once costs are in.

## Falsification Criteria

Pre-registered. Written before any backtest is run. Criteria
continue Sprint 3's numbering (C24 → C25). The **headline test is
C27**; the others gate engine correctness, out-of-sample honesty,
and robustness.

- **C25 — Engine correctness & no leakage.** A synthetic
  deterministic residual (a known sinusoid) run through the engine
  produces a trade ledger whose total P&L matches a hand-computed
  value to < 1e-6. The position held at date `t` is a function only
  of signal values at dates ≤ `t` (explicit shift; verified by a
  leakage test that perturbs a future bar and confirms no past
  position changes).

- **C26 — Control strategy is real.** Strategy A (RV1, OLS hedge, no
  regime filter) executes ≥ 30 round-trip trades on the full sample
  and produces a finite, non-degenerate net-of-cost Sharpe. This is
  not a thesis test — it confirms the engine yielded a usable
  baseline to compare against.

- **C27 — THESIS (headline).** Incremental Sharpe `ΔS = Sharpe(B) −
  Sharpe(A)`, both net of costs, OLS hedge, full in-sample period,
  is **> 0** AND the **95% bootstrap confidence interval
  (1000 resamples, seed 20260516) for ΔS has a lower bound > 0**.
  If the CI contains 0, the `equity_first` filter adds no
  risk-adjusted value and the thesis is **rejected at the trading
  level** — the C22 half-life effect, while real, is not monetizable.

- **C28 — Out-of-sample survival.** Thresholds (entry/exit/stop) are
  grid-calibrated on 2007-01-01 → 2018-12-31, **locked**, then
  applied unchanged to 2019-01-01 → 2026-04-15. The incremental
  Sharpe ΔS on the OOS test window must remain **> 0**. A sign flip
  OOS means the in-sample ΔS was overfit.

- **C29 — Beats the random benchmark.** Strategy B's net Sharpe
  exceeds the **95th percentile** of the Sprint-1 random-entry
  baseline (`data/benchmarks/random_baseline.parquet`, `hy_spread`
  paths). Excess Sharpe = `Sharpe(B) − Sharpe(random_p95)` > 0.

- **C30 — Robustness.** ΔS remains **> 0 on ≥ 75% of the
  parameter-sensitivity grid** (entry ∈ {1.5, 2.0, 2.5}, exit ∈
  {0.25, 0.5, 1.0}, stop ∈ {3.0, 4.0, 5.0} = 27 cells) **AND** ΔS > 0
  in **both halves** of the sample split at 2016-09. A pass that
  exists only in one parameter corner or one crisis is not a pass.

- **C31 — Not one-trade-driven.** For both A and B, no single trade
  contributes **> 25%** of that strategy's total P&L.
  `data/results/failure_analysis.parquet` exists with the worst 5
  trades per strategy and a one-line post-mortem each.

## Signal Definition

### Traded signal

The RV1 residual `rv` and its 63-day trailing z-score `z_rv`, for
each of the three hedge methods (OLS / Kalman / DV01), recomputed
via `signals.rv_signals.build_all_residuals`. The **canonical**
method for the pre-registered criteria is **OLS rolling 126d**
(Sprint 4 walkthrough showed Kalman is over-fit — half-life ~1.5d;
DV01 is too slow — ~450d). Kalman and DV01 are run alongside and
reported as a robustness panel, not used for C27/C28.

### Position state machine

A time-series state machine consuming `z_rv` and the three
thresholds. State ∈ {flat, long, short}. Transitions, evaluated at
each date `t` on `z_rv` known at `t`:

- flat → **short** when `z_rv > +entry` (residual rich → bet it falls)
- flat → **long** when `z_rv < −entry` (residual cheap → bet it rises)
- long/short → **flat (exit)** when `|z_rv| < exit` (reverted — take profit)
- long/short → **flat (stop)** when `|z_rv| > stop` (blew through — stop loss)
- An open position is held across days; only one position at a time.
- **Strategy B gate:** a flat → long/short *entry* is permitted only
  if `equity_credit_lag == "equity_first"` on date `t`. Exits and
  stops are never gated (you always close). Strategy A has no gate.

Entry/exit fills are at the **next day's** values (`t+1`) to avoid
same-bar look-ahead.

### Sizing — fixed notional, DV01-hedged

- HY leg: constant `notional = $1,000,000`.
- Hedge leg: `notional × hedge_ratio_t` (the leg-2 weight from the
  active hedge method at entry), making the pair duration-neutral.
- One unit of position per trade; no pyramiding, no vol-scaling.

### P&L convention

The residual `rv` is a log-ratio (dimensionless). Per round-trip
trade:

    gross_pnl = side · (rv_entry − rv_exit) · notional

where `side = +1` for a short-residual trade (entered at
`z_rv > +entry`), `side = −1` for a long-residual trade. A 0.01
residual move on $1M notional = $10,000.

### Costs (pre-registered)

| component | value | applied |
|---|---|---|
| half-spread | 1.5 bp per leg per side | entry + exit, both legs |
| slippage | 0.5 bp of notional | per trade (entry + exit) |
| short borrow | 0.40 % annual | on the short leg, per day held |

    cost = 2 legs · 2 sides · 1.5bp · notional
         + 2 · 0.5bp · notional
         + 0.40% · (holding_days / 252) · notional
    net_pnl = gross_pnl − cost

These are deliberately modest but non-zero — HYG/LQD are among the
most liquid credit ETFs; borrow is cheap. They are pre-registered so
the net Sharpe is not retro-fitted.

### Metrics

Per strategy: annualized Sharpe (daily P&L series, √252), hit rate
(% trades with net_pnl > 0), turnover (trades / year), max drawdown
(on the cumulative net equity curve), average holding days, total
net P&L, trade count, capacity note.

### A/B & bootstrap

`ΔS = Sharpe(B) − Sharpe(A)`. CI via stationary block bootstrap of
the **daily net P&L series** (block length ≈ 21d to preserve
autocorrelation), 1000 resamples, seed 20260516. Report ΔS point
estimate, 2.5/97.5 percentiles, and the fraction of resamples with
ΔS > 0.

### Parameters (explicit)

- `entry = 2.0`, `exit = 0.5`, `stop = 4.0` (defaults; swept in C30)
- `z_window = 63`, `ols_window = 126`
- `notional = 1_000_000`
- `half_spread_bp = 1.5`, `slippage_bp = 0.5`, `borrow_annual = 0.004`
- `fill_lag = 1` (trading day)
- `bootstrap_n = 1000`, `bootstrap_block = 21`, `seed = 20260516`
- `oos_split = 2018-12-31`, `subperiod_split = 2016-09-15`
- `warmup = 378` (252 global + 126 OLS, from Sprint 3)

## Data

| artifact | source | role |
|---|---|---|
| `data/processed/features.parquet` | Sprint 3 | 4784 × 56 — residuals, z_rv, regimes, hedge ratios |
| `data/raw/credit_market_data.parquet` | Sprint 1 | rates legs for RV2/RV3 portfolio |
| `data/benchmarks/random_baseline.parquet` | Sprint 1 | 3000 random-entry paths — C29 benchmark |
| `pycredit` | Sprint 2 | DV01 hedge-method recompute |

- **Frequency / range:** daily, 2007-04-11 → 2026-04-15, 4 ETFs.
- **Point-in-time:** all signals trailing-only (Sprint 3 verified);
  fills lagged one day; thresholds for C28 calibrated only on the
  train window.
- **Known biases:** ETF-proxy residuals (not single-name credit);
  no intraday data so fills are next-day close; borrow rate is a
  flat assumption, not a historical borrow curve; survivorship N/A
  (fixed 4-ETF universe, all alive the whole sample).
- **Missing data / corporate actions:** handled upstream in Sprint 1
  (adjusted close); no new ingestion this sprint.

## Success Metrics

Passing C25–C31 is the bar. Headline summary table (printed by the
final notebook):

| metric | target | criterion |
|---|---|---|
| Engine P&L vs hand calc | < 1e-6 error | C25 |
| Strategy A trade count | ≥ 30 | C26 |
| Incremental Sharpe ΔS (in-sample) | > 0, CI lower > 0 | C27 |
| Incremental Sharpe ΔS (OOS) | > 0 | C28 |
| Strategy B excess Sharpe vs random p95 | > 0 | C29 |
| ΔS positive across grid | ≥ 75% of 27 cells | C30 |
| ΔS positive in both halves | both > 0 | C30 |
| Max single-trade P&L share | < 25% | C31 |

Reported but not pass/fail: per-strategy Sharpe / hit rate /
turnover / max drawdown / avg holding days; the same A/B under
Kalman and DV01 hedges; the multi-signal portfolio Sharpe.

## Research Architecture

```
execution/
  costs.py        -- NEW. Cost model: half-spread + slippage + borrow
  position.py     -- NEW. Position state machine (z_rv + thresholds → positions)
backtest/
  engine.py       -- NEW. Run one strategy → trade ledger + daily P&L + equity curve
  metrics.py      -- NEW. Sharpe, hit rate, turnover, max DD, capacity
  ab_test.py      -- NEW. A/B incremental Sharpe + block-bootstrap CI
  benchmarks.py   -- NEW. Buy-hold HYG + random-baseline-p95 comparison
risk/
  portfolio.py    -- NEW. Combine RV1/RV2/RV3 strategies into one book
data/results/
  backtest_trades.parquet      -- NEW. Full trade ledger, all strategies
  failure_analysis.parquet     -- NEW. Worst-5 trades per strategy + post-mortem
notebooks/
  05_backtest.ipynb            -- NEW. A/B, failure slide, benchmarks, robustness
tests/
  test_costs.py, test_position.py, test_engine.py,
  test_metrics.py, test_ab_test.py   -- NEW.
sprints/v5/
  PRD.md, TASKS.md, notes.md, WALKTHROUGH.md, plots/
```

**Data flow:**

1. `signals.rv_signals.build_all_residuals` → residuals + hedge
   ratios for the 3 methods.
2. `execution.position.run_state_machine(z_rv, thresholds, regime_gate)`
   → position series.
3. `backtest.engine.run(residual, positions, hedge_ratio, costs)`
   → trade ledger + daily net P&L + equity curve.
4. `backtest.metrics.summary(daily_pnl, trades)` → Sharpe etc.
5. `backtest.ab_test.compare(A, B)` → ΔS + bootstrap CI.
6. `backtest.benchmarks.vs_random(strategy, random_baseline)` →
   excess Sharpe.
7. `risk.portfolio.combine([rv1, rv2, rv3])` → portfolio equity.

The signal / portfolio-construction / backtest split: signals come
frozen from Sprint 3; `execution/` turns signals into positions;
`backtest/` turns positions into P&L and statistics; `risk/`
aggregates strategies. No module reaches backward.

## Risks & Biases

- **Multiple testing.** Three hedge methods × A/B × 27-cell grid is
  a large search. C27 is pre-registered on OLS only; everything else
  is explicitly robustness/exploratory. We do not cherry-pick the
  best hedge method as "the result."
- **Look-ahead via thresholds.** Using full-sample default
  thresholds is mild in-sample tuning. C28 (walk-forward) is the
  honest control — thresholds locked on train, no peeking.
- **Cost assumption risk.** Flat 0.40% borrow and 1.5bp half-spread
  are estimates. If the true cost is 2-3× higher the net Sharpe
  could flip. A cost-sensitivity row is included in the notebook.
- **Regime label leakage.** `equity_credit_lag` is trailing but
  `vol_regime`'s expanding median weakly leaks (Sprint 3 caveat).
  The B gate uses `equity_credit_lag`, which is the cleaner label.
- **Crisis dependence.** The edge may live entirely in 2008/2020.
  C30's subperiod split is the guard; the failure slide will expose
  whether the best trades all cluster in crises.
- **Small trade count.** Strategy B only trades on `equity_first`
  days (~8% of the sample). Trade count could be low enough that ΔS
  has wide error bars — exactly what the bootstrap CI is there to
  surface honestly.
- **Survivorship / capacity.** Universe is 4 ETFs, all alive the
  whole sample — no survivorship. ETF ADV (HYG ≈ $1-2bn/day) dwarfs
  the $1M notional, so capacity is not binding; noted, not modeled
  in depth.

## Out of Scope

- Live or paper trading against a broker. (Tier 2.)
- Intraday execution, limit-order modeling, market-impact curves.
- Single-name CDS strategies — ETF proxies only.
- Vol-targeted or Kelly sizing — fixed notional only.
- Optimizing the signal itself (hedge windows, z-window) — signals
  are frozen from Sprint 3.
- Tax, margin, and financing beyond the flat borrow assumption.
- Regime-switching position sizing — the regime is an entry gate,
  not a sizing input.

## Dependencies

**Existing (no version change):** `pandas`, `numpy`, `scipy`,
`statsmodels`, `pyarrow`, `matplotlib`, `pycredit` (Sprint 2).

**New:** none — block bootstrap is hand-rolled (~15 lines).

**Prior sprint outputs:**
- `sprint-v3` tag: `features.parquet` (56 cols), `regime_signal_quality.parquet`.
- `sprint-v1` tag: `random_baseline.parquet`.
- `sprint-v2` tag: `pycredit` module (for the DV01 hedge recompute).
