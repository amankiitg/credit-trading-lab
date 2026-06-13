# Sprint v6 — Factor Attribution (Tier 2, Sprint 1)

## Scope note — Strategy A first

This sprint builds and validates the factor attribution framework on **Strategy A
(RV1_A / rv_hy_ig / OLS / no gate)** only. RV2_A (rv_credit_rates) and RV3_A
(rv_xterm) are admitted to Tier 2 per `sprints/v5.6/signal_selection.md` and will
have attribution run against the same framework in **sprint v6.5** once we have
confirmed the decomposition is correct and the tooling is stable on the signal we
understand best. v6.5 is a thin extension sprint — most of its work is running
T1–T7 below with pair=rv_credit_rates and pair=rv_xterm.

---

## Overview

Tier 1 established that Strategy A earns a net Sharpe of 0.591 with an 81% hit rate
over 94 trades (2007–2026). What it did not answer is *why* those trades were
profitable. This sprint decomposes each trade's gross P&L into two components:
(1) the **IG hedge component** — P&L from the IG spread leg moving during the hold,
and (2) the **HY leg component** — P&L from the HY spread leg. If the strategy is
genuinely harvesting mean reversion in the HY/IG residual, the HY leg should drive
most of the profit and the IG hedge should be a modest drag or wash. If the IG hedge
is consistently contributing large positive P&L, the strategy may be inadvertently
running a short-IG (credit-quality spread compression) bet that profits in risk-on
regimes — a different and less robust story. Beyond the decomposition, we slice P&L
by three available regime dimensions (realized-vol regime, equity regime,
equity_credit_lag) and by holding period, to identify concentration risks and
confirm the edge is not confined to a single market environment.

---

## Economic Hypothesis

The strategy enters when rv_hy_ig = hy_spread − β × ig_spread is more than 2σ from
its trailing mean. It bets that the residual will revert to zero within the OU
half-life (~18 days). **If this hypothesis is correct:**
- The gross P&L of a LONG trade should come primarily from hy_spread tightening
  (the HY leg contributing positively), with the IG hedge leg providing modest
  drag or mild positive contribution depending on whether IG widens or tightens.
- The mean contribution of the IG hedge across all trades should be near zero in
  expectation — the hedge is not the source of alpha, it removes systematic beta.
- P&L should be earned across both high-vol and low-vol periods, both bull and
  bear equity regimes — because the mean-reversion thesis depends on the residual
  level, not on the direction of the broader credit market.
- Short-hold and long-hold trades should both contribute: short holds capture fast
  snaps; long holds capture slow drifts back to equilibrium.

**If the hypothesis is wrong (what would falsify it):**
- The IG hedge contributes the majority of P&L → the strategy is running short-IG
  credit beta, not mean reversion.
- P&L is concentrated almost entirely in high-vol / bear regimes → the strategy is
  a distress harvester that runs long-credit risk at dislocations, not an RV signal.
- Long holds are almost all losers and short holds are almost all winners → the
  strategy profits from temporary dislocations that snap back in days, but the
  residual has more random-walk character than OU at longer horizons.

---

## Falsification Criteria

Pre-registered. All four must hold for the framework to support the mean-reversion
narrative and allow v6.5 / v7 to proceed.

| ID | Criterion | Pass threshold | What failure means |
|----|-----------|---------------|-------------------|
| FA1 | HY leg component share of gross P&L — both mean(hy_leg_pnl/gross_pnl) across trades AND sum(hy_leg_pnl)/sum(gross_pnl) in aggregate | ≥ 50% for both | Strategy is running IG hedge beta, not HY mean reversion |
| FA2 | IG hedge contribution: mean across all trades | Not significantly different from 0 (|mean| < 20% of mean gross_pnl per trade) | IG leg is a systematic alpha source, not a hedge — changes the story |
| FA3 | vol_regime=low AND vol_regime=high both have mean net_pnl > 0 per trade | both positive | Edge confined to stress episodes only |
| FA4 | holding_days ≤ 10 AND holding_days > 20 groups both have mean net_pnl > 0 | both positive | P&L structure inconsistent with OU half-life story |

FA1 and FA2 are complements: FA1 checks the HY leg drives the result; FA2 checks the
IG hedge is not a hidden source of profit. A strategy can pass FA1 and fail FA2 (if
both HY and IG are contributing positively via a shared credit-beta risk factor).

---

## Signal Definition

**No new signal.** This sprint analyses existing Strategy A trades using market data
at entry and exit dates.

**Per-trade P&L decomposition** (using entry_fill_date and exit_fill_date from the
trade ledger, merged with features.parquet for hy_spread and ig_spread):

```
Δhy = hy_spread[exit_fill_date] − hy_spread[entry_fill_date]
Δig = ig_spread[exit_fill_date] − ig_spread[entry_fill_date]

hy_leg_pnl   = side × Δhy × notional
ig_hedge_pnl = side × (−hedge_ratio_entry × Δig) × notional

gross_check = hy_leg_pnl + ig_hedge_pnl  # must match engine gross_pnl within $100
```

**Note on sign convention:** `hy_spread = ln(HYG/IEF)` and `ig_spread = ln(LQD/IEF)` —
these are log price ratios, not OAS-style spreads. The engine computes:

```
gross = side × (rv_exit − rv_entry) × notional
      = side × (Δhy − β × Δig) × notional
```

So the attribution splits directly as `hy_leg = side × Δhy` and
`ig_hedge = side × (−β × Δig)`.

For a LONG trade (side=+1) entered when rv is very negative (HY cheap):
- Profit comes from rv increasing: Δhy > 0 (HYG outperforms IEF) → hy_leg_pnl > 0
- The short-IG hedge earns when Δig < 0 (LQD underperforms IEF):
  ig_hedge_pnl = −β × Δig > 0 when Δig < 0

**Important:** do not use OAS/spread-tightening language here. The features use
log price ratios, so a rising `hy_spread` value means HYG is outperforming IEF
(a credit rally), which is the direction we want in a long trade.

**Regime enrichment** (join on entry_fill_date):
- `vol_regime` : "high" / "low" (from features.parquet, 21-day realized vol of HYG)
- `equity_regime` : "bull" / "bear"
- `equity_credit_lag` : "equity_first" / "credit_first" / "neither"
- `HYG_vol_21` : continuous realized-vol value at entry

**Holding-period buckets:**
- very_short : holding_days < 5
- short : 5 ≤ holding_days ≤ 10
- medium : 11 ≤ holding_days ≤ 20
- long : holding_days > 20

FA4 tests `≤10` (= very_short + short) and `>20` (= long).

---

## Data

- `data/processed/features.parquet` : 4784 × 56, 2007-04-11 → 2026-04-15.
  Columns used: `hy_spread`, `ig_spread`, `hedge_ratio_hy_ig`, `vol_regime`,
  `equity_regime`, `equity_credit_lag`, `HYG_vol_21`.
- Trade ledger: produced by `build_strategy(features, residuals, StrategySpec('rv_hy_ig','ols',gated=False))`.
  94 trades, columns: `entry_fill_date`, `exit_fill_date`, `side`,
  `hedge_ratio_entry`, `hedge_ratio_exit`, `holding_days`, `gross_pnl`,
  `cost`, `net_pnl`.

**Known biases:**
- hy_spread and ig_spread in features.parquet are derived from HYG/LQD/IEF
  ETF prices, not OAS directly. The FRED OAS series (oas_hy, oas_ig) is in
  credit_market_data.parquet but is used only for residual construction.
  The attribution uses the same spread series used by the engine — no mismatch.
- hedge_ratio_entry is the OLS β estimated on a trailing 252-day window ending
  at entry_signal_date (1 day before entry_fill_date). This is the β actually
  used by the engine — using it for attribution is consistent.
- No VIX in the dataset. `vol_regime` (based on HYG_vol_21) is used as the
  volatility dimension. This is already computed in features.parquet.

---

## Success Metrics

| Metric | Value to report | Notes |
|--------|----------------|-------|
| HY leg share (FA1) | mean(hy_leg_pnl / gross_pnl) across trades | Must be ≥ 50% |
| IG hedge mean (FA2) | mean(ig_hedge_pnl) | Must be < 20% of mean gross_pnl |
| Gross check | max(|gross_pnl − (hy_leg + ig_hedge)|) | Should be < $100 rounding |
| By vol_regime | mean net_pnl, hit rate, n_trades | Both regimes positive (FA3) |
| By equity_regime | mean net_pnl, hit rate, n_trades | Report; no hard threshold |
| By equity_credit_lag | mean net_pnl per regime | Report; flag if >80% in one bucket |
| By hold bucket | mean net_pnl, n_trades, hit rate | Both short and long positive (FA4) |
| Avg IG hedge drift | cumulative ig_hedge_pnl / cumulative gross_pnl | Directional beta audit |

---

## Research Architecture

```
[trade ledger]  ← build_strategy(StrategySpec('rv_hy_ig','ols',gated=False))
      ↓
[enrich_trades()]  — join features.parquet on entry/exit dates
      → adds Δhy, Δig, hy_leg_pnl, ig_hedge_pnl, gross_check
      → adds vol_regime, equity_regime, equity_credit_lag, HYG_vol_21 at entry
      ↓
[attribution_table]  — one row per trade, all components
      ├── per-trade breakdown (T2)
      ├── regime slices (T3)
      └── hold-period slices (T4)
      ↓
[attribution_summary.md]  — pre-formatted results table
[notebooks/06_factor_attribution.ipynb]  — full workbook
```

No new modules required. All computation in plain pandas in the notebook and a
helper script `scripts/build_attribution.py`.

---

## Risks & Biases

- **Sign convention error:** spread convention (tighter = positive P&L for long) is
  opposite to price convention. One sign flip anywhere will invert the attribution.
  T2 includes a gross_check — if max residual > $100, there is a sign error.
- **Beta at entry vs exit:** using hedge_ratio_entry for attribution is correct
  (that is what the engine used to size the hedge at inception). hedge_ratio_exit
  reflects a later window and is not the right basis for attribution of P&L earned
  during the hold. Do not average the two.
- **Regime imbalance:** vol_regime=high covers only 1,846 out of 4,784 days (~39%).
  If a large fraction of our 94 trades cluster in high-vol periods this is expected
  given the entry rule (|z|>2 more likely in high-vol). Report trade counts per
  regime honestly — a 70/30 split is not a failure if mean P&L is positive in both.
- **Holding-period bucket sparsity:** very_short (<5d) may have very few trades.
  Report n_trades per bucket. If a bucket has <5 trades, state "too few to conclude"
  rather than reporting mean P&L as if it were reliable.

---

## Out of Scope

- Attribution for RV2_A and RV3_A — deferred to sprint v6.5
- Portfolio-level factor attribution (combined three-signal attribution) — v7
- Risk factor model (Barra-style) — not needed for mean-reversion strategies
- Changing the strategy parameters, cost model, or engine
- Adding VIX data (not available in the dataset; HYG_vol_21 is the proxy)
- Intraday or tick-level analysis — daily data only

---

## Dependencies

- `sprints/v5/WALKTHROUGH.md` — confirmed RV1_A numbers
- `sprints/v5.6/signal_selection.md` — admission of RV2_A/RV3_A (context for the
  "Strategy A first" scope note; not consumed computationally)
- `data/processed/features.parquet` (post-v5.5, shape 4784×56)
- `backtest/ab_test.py`, `backtest/engine.py` — read-only, no changes
