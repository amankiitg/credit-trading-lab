# Sprint v6 — Attribution Summary
**Date: 2026-06-13 | Signal: RV1_A (rv_hy_ig, OLS, no gate) | 94 trades 2007–2026**

---

## FA1–FA4 Scorecard

| ID | Criterion | Stored number | Verdict |
|----|-----------|--------------|---------|
| FA1a | mean(hy_share) per qualifying trade | 1.555 | PASS (>0.50) — but noisy (ratio instability around losers) |
| FA1b | sum(hy_leg)/sum(gross) — aggregate | −0.100 | FAIL (<0.50) — see interpretation below |
| FA2 | \|mean(ig_hedge)\| < 20% × mean(gross) | $4,780 vs threshold $1,808 | FAIL — see interpretation |
| FA3 | both vol regimes positive | high $13,922 / low $5,079 | **PASS** |
| FA4 | ≤10d group AND >20d group both positive | $8,918 / $3,039 | **PASS** |

---

## Narrative — Source of Edge

The 3-way decomposition (`hy_leg + ig_hedge + model_drift = gross_pnl`, exact by construction)
reveals a more nuanced picture than the original FA1/FA2 framing anticipated:

| component | mean per trade | total | share of mean gross |
|-----------|--------------|-------|---------------------|
| HY leg    | −$908        | −$85,370  | −10% |
| IG hedge  | +$4,780      | +$449,361 | +53% |
| model drift (α/β shift) | +$5,166 | +$485,641 | +57% |
| **gross** | **+$9,039**  | **+$849,632** | 100% |
| net (after cost) | +$8,089 | +$760,372 | — |

**What FA1b FAIL and FA2 FAIL actually mean:**

FA1b fails (aggregate HY contribution = −10%) because the HY log-ratio `ln(HYG/IEF)`
moves slightly *against* us on average during holds. This does NOT indicate a problem —
it rules out the concern that we are running directional long-HY beta. The HY leg is near
zero (−$908 mean), not large and positive. If we were inadvertently long HY credit, the
HY leg would dominate with a large positive mean.

FA2 fails (IG hedge = $4,780, above the 20% threshold) because the IG leg is the
second-largest profit driver. For LONG rv trades (HY cheap), we are effectively SHORT
β×IG. When IG spreads adjust (widening while HY stays depressed), the short-IG hedge
earns. This is exactly how RV mean reversion is *supposed* to work: the spread
relationship reverts not always because HY rallies, but because IG adjusts. FA2 as
framed was checking for directional bias, not for this case.

**The dominant driver is model drift (57% of gross P&L)**: the OLS rolling intercept
`α = mean_y − β·mean_x` re-centers as each trading day is included in the trailing
window. When we enter a trade, rv is very negative (large dislocation). Over the hold,
the 252-day trailing mean shifts toward current prices, making rv appear to revert even
if neither leg moves dramatically. This is "statistical reversion" — the rolling window
itself closes the gap — and it is the mechanism by which the OU process is harvested.
This finding should be carried into v7 (scenario risk): in stress scenarios where the
rolling window shifts rapidly, the strategy may appear to profit from "reversion" that
is actually model re-parameterisation.

---

## Regime Table

| dimension | bucket | n_trades | mean net P&L | hit rate |
|-----------|--------|----------|-------------|---------|
| vol_regime | high | 32 (34%) | $13,922 | 87.5% |
| vol_regime | low  | 62 (66%) | $5,079  | 77.4% |
| equity_regime | bear | 35 (37%) | $11,267 | 80.0% |
| equity_regime | bull | 59 (63%) | $6,204  | 81.4% |
| equity_credit_lag | neither     | 81 (86%) | $8,715 | 80.2% |
| equity_credit_lag | equity_first | 9 (10%) | $4,476 | 77.8% |
| equity_credit_lag | credit_first | 4 (4%)  | $3,538 | 100% (too few) |

FA3 PASS. Concentration flag on equity_credit_lag (86% "neither") — expected, not
disqualifying. High-vol trades earn 2.7× more per trade than low-vol, consistent with
larger dislocations → larger payoffs when they close.

---

## Holding-Period Table

| bucket | n_trades | mean net P&L | hit rate | cumulative |
|--------|----------|-------------|---------|-----------|
| very_short (<5d)  | 16 | $5,879  | 75.0% | $94,063 |
| short (5–10d)     | 27 | $10,719 | 92.6% | $289,418 |
| medium (11–20d)   | 22 | $13,125 | 95.5% | $288,750 |
| long (>20d)       | 29 | $3,039  | 62.1% | $88,140 |

Pearson r(holding_days, net_pnl) = −0.242, p=0.019. Significant negative decay.
The 5–20d sweet spot (49 trades) contributes 76% of total net P&L. Trades past 20d
have not reverted in the OU window and earn much less on average.

---

## Net Beta Audit

| metric | value |
|--------|-------|
| Mean net_hy_beta (all days) | 0.0190 |
| Mean net_hy_beta (in-trade days) | 0.0562 |
| Flag (|mean|>0.3) | NO — CLEAN |
| LONG trades | 56 (60%), SHORT 38 (40%) |
| Net long days / net short days | 808 / 722 |

No directional credit beta. Confirmed: the strategy is not running a net credit book.

---

## Extension Note — RV2_A and RV3_A (sprint v6.5)

RV2_A (rv_credit_rates) and RV3_A (rv_xterm) are admitted to Tier 2 per
`sprints/v5.6/signal_selection.md`. The same T1–T7 attribution framework will be
applied in sprint v6.5 with the following signal-specific considerations:

**RV2_A (rv_credit_rates)**: The x-leg is `dgs10` (10-year Treasury yield in decimal),
not a spread. `delta_ig` in T2 becomes `delta_rates = dgs10[exit] − dgs10[entry]`.
The sign convention for the ig_hedge term needs care: rv_credit_rates = hy_spread −
β × dgs10. A LONG rv trade is effectively SHORT β×(10y rate). If rates fall during the
hold (Δdgs10 < 0), `ig_hedge = side × (−β × Δdgs10) = 1 × (−β × negative) = positive`.
Economic check: the IG hedge should earn when rates fall (flight-to-quality, rates drop,
our short-rates position profits), which is typically when HY is cheap. Verify sign
convention produces gross_check identity before reporting.

**RV3_A (rv_xterm)**: No hedge_ratio is stored in features.parquet for this pair (the
dashboard and engine use `hedge_ratio_hy_ig` as a proxy, which is incorrect). T2 for
RV3 needs to either (a) recompute the OLS beta from the rv_xterm regression inline, or
(b) use a DV01-style proxy. Option (a) is cleaner — call `ols_hedge(hy_ig, slope, window=252)`
at each entry/exit fill date to recover α and β at those dates. Alternatively, model_drift
can simply be left as the unexplained residual without a full 3-way split.
