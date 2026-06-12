# Sprint v5.6 — Multi-Signal Validation Gate

## Overview

Sprint v5 validated Strategy A (RV1/rv_hy_ig, OLS, no regime gate) as Tier 1's
deliverable: net Sharpe 0.591, 81% hit rate, 94 trades over 2007–2026. The
equity_first gate was pre-registered and rejected. Sprint v5 only tested RV2 and
RV3 with the gate — their ungated performance was never examined. An informal
run today returned RV2_A Sharpe 0.693 (103 trades) and RV3_A Sharpe 0.856 (101
trades), but these are **unregistered numbers**. Before either signal enters Tier
2 (v6 factor attribution, v7 scenario risk, v8 paper trading), each must pass the
same falsification framework v5 applied to RV1. This sprint pre-registers that
framework, runs the tests, measures cross-signal correlation, and produces a
binding signal_selection.md document that gates v6.

## Economic Hypothesis

Each RV pair exploits a different dislocation in the credit market:

- **RV1 (rv_hy_ig):** HY spread vs IG spread — when the HY/IG ratio is stretched
  relative to its recent history (OLS residual extended), the two spreads tend to
  revert to their rolling cointegrating relationship. The counterparty is a
  directional credit investor who has over-extended one leg.

- **RV2 (rv_credit_rates):** HY spread vs the 10y Treasury rate — when HY
  spread is cheap/rich relative to rates (after removing the OLS beta), it
  tends to revert. The counterparty is a macro/rates investor causing a
  temporary dislocation between credit and the risk-free curve.

- **RV3 (rv_xterm):** hy_ig differential vs the 2s10s Treasury slope — when the
  HY/IG differential is out of line with the curve shape, it tends to revert.
  The counterparty is a duration trader whose curve positioning temporarily
  disconnects credit spreads from the yield curve's term premium signal.

All three pairs have OU half-lives in [5, 63] days (v5.5 verified), so mean
reversion is plausibly fast enough to trade and slow enough to survive a T+1
fill. The null hypothesis for each signal is: standalone net Sharpe ≤ 0.40
after costs on the full 2007–2026 sample. Rejection of the null (Sharpe > 0.40)
is required for inclusion in Tier 2.

The portfolio hypothesis is: combining qualifying signals in equal weight produces
a Sharpe ≥ 85% of the best individual signal. If cross-signal P&L correlation is
high (ρ > 0.70), the three signals are effectively one bet dressed differently and
the portfolio gain will be minimal. If correlation is low, diversification is
genuine and the combined Sharpe should materially exceed any single signal.

## Falsification Criteria

Pre-registered. A signal passes iff ALL of M1–M6 hold for it. The portfolio
passes iff M7 and M8 hold. RV1 is the benchmark and is not re-tested; its
numbers are fixed at the v5 values.

| ID | Criterion | Pass threshold |
|----|-----------|---------------|
| M1 | Standalone net Sharpe, full sample 2007–2026 | > 0.40 |
| M2 | Trade hit rate (net_pnl > 0) | > 65% |
| M3 | Max single-trade share of total P&L | ≤ 25% |
| M4 | Parameter grid: fraction of 27 cells with Sharpe > 0 | ≥ 60% |
| M5 | Subperiod: standalone Sharpe > 0 in BOTH halves | both > 0 |
| M6 | Per-trade Sharpe > random-entry p95 (same baseline as v5) | excess > 0 |
| M7 | Pairwise daily P&L correlation between qualifying signals | reported; flag if ρ > 0.70 |
| M8 | Equal-weight portfolio Sharpe vs best individual | ≥ best × 0.85 |
| M9 | Economic intuition — for each admitted signal, signal_selection.md must answer: (1) what dislocation are you exploiting, (2) who is on the other side, (3) why is this distinct from RV1 and not just the same HY-cheapness bet in disguise | qualitative, must be written clearly enough to defend in an interview |

A signal that fails any of M1–M6 is **excluded** from Tier 2. The decision is
binding: v6 PRD explicitly references `sprints/v5.6/signal_selection.md`.

## Signal Definition

All signals use the canonical OLS residual from v5.5 (canonical_residuals()).
No new computation — signals already exist in features.parquet and residuals dict.

**Strategy construction (identical to v5 Strategy A for each pair):**
- Residual: canonical OLS residual for the pair (HL ∈ [5, 63], ADF p < 0.05)
- Z-score: trailing 63-day rolling z-score of the residual
- Entry: `|z| > 2.0` (long if z < −2, short if z > +2)
- Exit: `|z| < 0.5`
- Stop: `|z| > 4.0`
- Fill lag: 1 trading day (no same-bar fills)
- Notional: $1,000,000 per signal
- Gate: none (ungated — same as Strategy A)

**Portfolio:**
- Equal-weight: $1,000,000 notional per signal, positions independent
- Daily P&L: sum of individual daily P&Ls
- Sharpe computed on combined daily P&L series

**Parameters (identical to v5, NOT tuned for RV2/RV3):**
- entry = 2.0, exit = 0.5, stop = 4.0
- warmup = 252 days
- cost model: unchanged from v5 (1.5bp half-spread, 0.5bp slippage, 0.40%/yr borrow)

## Data

- features.parquet: 4784 × 56 (post-v5.5), contains rv_credit_rates_residual,
  rv_xterm_residual, z_rv_credit_rates, z_rv_xterm, hedge_ratio_cr — all
  regenerated from canonical_residuals() in v5.5
- data/raw/credit_market_data.parquet: FRED data, 2006–2026
- data/benchmarks/random_baseline.parquet: Sprint v1 random-entry baseline
  (1000 simulations, 64 trades each on hy_spread) — reused as-is for M6

**Known biases:**
- All three signals share the HY spread leg in some form — they are not
  constructed from independent data sources. Cross-signal correlation (M7) is
  the quantification of this dependency.
- The random baseline (v1) used hy_spread. Using the same baseline for RV2/RV3
  is a slight methodological simplification — documented honestly in notes.md.
- No survivorship bias: HYG/LQD/IEF are liquid ETFs with continuous history.
- OLS beta estimated on trailing window (no look-ahead): verified by v5.5 R9.

## Success Metrics

**Standalone (per signal):**
- Net Sharpe > 0.40 (M1)
- Hit rate > 65% (M2)
- Max single-trade P&L share ≤ 25% (M3)
- Trade count ≥ 30 (enough for statistics)
- Avg hold 10–40 days (consistent with [5,63] HL band)

**Robustness:**
- Parameter grid: ≥ 60% of 27 cells Sharpe > 0 (M4)
- Both subperiod halves Sharpe > 0 (M5)
- Per-trade Sharpe > random p95 (M6)

**Portfolio:**
- Pairwise ρ reported for all qualifying pairs (M7)
- Portfolio Sharpe ≥ best × 0.85 (M8)
- Portfolio max drawdown and trade count recorded

## Research Architecture

```
backtest.ab_test.build_strategy(features, residuals, StrategySpec(pair, 'ols', gated=False))
    → BacktestResult per signal

backtest.metrics.summary(daily_pnl, trades)
    → Sharpe, hit_rate, n_trades, max_drawdown per signal

backtest.ab_test.parameter_grid / subperiod_split
    → M4, M5 per signal

backtest.benchmarks.vs_random / trade_sharpe
    → M6 per signal (reuse v5 random_baseline.parquet)

portfolio: sum daily_pnl across qualifying signals → combined metrics (M7, M8)
```

No new modules. All computation uses frozen v5/v5.5 infrastructure.

## Risks & Biases

- **Multiple testing:** testing 3 signals raises the bar for "significance."
  The M1 threshold (0.40) is set below RV1's actual 0.591 to allow for
  multiple-testing shrinkage while still requiring a meaningful positive result.
  If all three pass, the Bonferroni-corrected bar would be tighter — noted in
  signal_selection.md.
- **Shared legs:** rv_hy_ig and rv_credit_rates both use hy_spread as y-leg.
  rv_xterm uses hy_ig (= hy_spread − ig_spread) as y-leg. Cross-correlation
  will reflect this shared exposure. M7 quantifies it.
- **Same parameter set as v5:** entry/exit/stop are fixed from v5 RV1 tuning.
  Not re-optimising for RV2/RV3 (that would be overfitting). The parameter
  grid (M4) tests sensitivity, not optimality.
- **Random baseline mismatch:** the baseline was built on hy_spread trades
  (~64 trades). RV2 (103 trades) and RV3 (101 trades) have different n_trades,
  so the sqrt(n) scaling in per-trade Sharpe differs. Documented, not corrected.

## Out of Scope

- Re-testing the equity_first gate on RV2/RV3 — already rejected in v5
- Kalman or DV01 hedge methods — disqualified in v5.5, not revisited
- Portfolio optimisation (position sizing, Kelly weights) — flat $1M per signal
- Any new data sources or features — only existing features.parquet
- Changes to backtest engine, state machine, or cost model
- Dashboard or pipeline changes
- Tier 2 sprint execution — this sprint only produces signal_selection.md

## Dependencies

- sprints/v5/WALKTHROUGH.md — v5 falsification framework (C25–C31)
- sprints/v5.5/WALKTHROUGH.md — canonical residuals, confirmed OLS for all pairs
- data/processed/features.parquet (post-v5.5)
- data/benchmarks/random_baseline.parquet
- backtest/{engine,ab_test,metrics,benchmarks}.py — all frozen, no changes
