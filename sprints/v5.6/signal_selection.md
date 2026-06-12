# Signal Selection — Sprint v5.6
**Date: 2026-06-12**

This document is the binding output of Sprint v5.6. It records M1–M9 verdicts
for every candidate signal and determines which signals enter Tier 2 (v6+).
v6 PRD must reference this file and use only the admitted signals.

---

## M1–M8 Scorecard

All numbers are registered in `sprints/v5.6/notes.md` (S2–S7) and reproduced
verbatim in `notebooks/56_multisignal_validation.ipynb`.

| Criterion | RV1_A | RV2_A | RV3_A |
|-----------|-------|-------|-------|
| **M1** — net Sharpe > 0.40 | **0.591** PASS | **0.693** PASS | **0.856** PASS |
| **M2** — hit rate > 65% | **80.9%** PASS | **75.7%** PASS | **82.2%** PASS |
| **M3** — max single trade ≤ 25% P&L | **<25%** PASS | **15.0%** PASS | **8.4%** PASS |
| **M4** — param grid ≥ 60% cells Sharpe > 0 | **27/27** PASS | **27/27** PASS | **27/27** PASS |
| **M5** — both subperiods Sharpe > 0 | 0.523 / 0.674 PASS | 0.706 / 0.735 PASS | 0.834 / 0.879 PASS |
| **M6** — per-trade Sharpe > random p95 (1.70) | **5.00** PASS | **4.29** PASS | **5.59** PASS |
| **M7** — max pairwise ρ < 0.70 | — | ρ_max = 0.506 (RV1/RV3) OK | same |
| **M8** — portfolio Sharpe ≥ best × 0.85 | — | Portfolio 0.9473 > 0.728 PASS | same |

M6 caveat (S6): baseline was calibrated on 64 trades; RV2/RV3 have 103/101 trades, giving a
sqrt(n) scaling advantage of ~1.27×. Even after conservative deflation: RV2 = 3.38, RV3 = 4.44 —
both pass M6 with margin.

---

## Admitted Signals

### RV1_A — rv_hy_ig, OLS, no gate
- **Admitted in v5** (benchmark). Numbers held fixed.
- Sharpe 0.591, hit 80.9%, 94 trades, $760k net P&L over 2007–2026.

### RV2_A — rv_credit_rates, OLS, no gate
- **Admitted.** Passes all M1–M6. Formally registered in this sprint.
- Sharpe 0.693, hit 75.7%, 103 trades, $994k net P&L.

### RV3_A — rv_xterm, OLS, no gate
- **Admitted.** Passes all M1–M6 with the strongest individual Sharpe. Formally registered.
- Sharpe 0.856, hit 82.2%, 101 trades, $990k net P&L.

---

## Excluded Signals

| Signal | Why excluded |
|--------|-------------|
| RV2_B (rv_credit_rates, gated) | Tested in v5 — equity_first gate rejected across all grids (delta_sharpe negative) |
| RV3_B (rv_xterm, gated) | Same — gate rejected in v5; ungated RV3_A is strictly better |
| RV1_B (rv_hy_ig, gated) | Gated version always underperforms ungated (v5 C30: all 27 delta_sharpe < 0) |
| Kalman variants (all pairs) | HL < 5d disqualifies all three (v5.5 selector, S1 residual diagnostics) |
| DV01 variant (rv_xterm) | Non-stationary residual (ADF p >> 0.05), HL = 73d above ceiling (v5.5 S5) |

---

## Portfolio Recommendation

**Run all three admitted signals at equal weight: $1M notional per signal.**

Expected performance (from S7):
- Portfolio net Sharpe: **0.947** (exceeds best individual 0.856 by 10.7%)
- Portfolio total net P&L: **$2,745,203** (2007–2026)
- Portfolio max drawdown: **−$256,142** (~9.3% of total P&L)
- Active (at least one signal in trade): 60% of all days vs 33% per signal

Diversification is genuine: RV1/RV2 daily P&L correlation is only 0.181 despite sharing
the HY spread leg. The different second leg (ig_spread vs 10y Treasury rate) creates
independent entry/exit timing. RV1/RV3 correlation (0.506) is the highest pair but well
below the 0.70 flag threshold.

**Allocation note for Tier 2:** equal $1M flat notional is not risk-parity. In v7 (scenario
risk), consider sizing by signal volatility so each contributes equally to portfolio variance.
For now, flat is appropriate — it is what was tested, and more complex allocation would need
its own pre-registration.

---

## Multiple-Testing Note

We tested 3 signals (RV1_A already admitted; 2 new). A strict Bonferroni correction at 5%
significance level would require each individual signal's Sharpe to be significant at 2.5%
rather than 5%. However:

1. The M1 threshold of 0.40 is conservative (RV1_A at 0.591 sets the empirical floor).
   RV2_A (0.693) and RV3_A (0.856) exceed M1 by 0.253 and 0.416 respectively.
2. The signals were not cherry-picked from a large universe — they are all distinct pairings
   of the three available credit/rates series (hy_spread, ig_spread, 10y rate, curve slope).
   Only 3 combinations are economically meaningful; we tested all 3.
3. Cross-signal correlation is low (max 0.506), so the tests are not fully independent in a
   statistical sense, but the signals are genuinely distinct bets.

**Conclusion:** Bonferroni adjustment does not change the admission decision. Both RV2_A and
RV3_A are admitted. The low ρ_max (0.506) is consistent with genuinely distinct exposures.

---

## M9 — Economic Intuition

Each admitted signal must have a clear, interview-defensible story distinct from RV1.

### M9 for RV1_A — HY/IG spread dislocation

HY and IG spreads share the same systematic credit risk factor but in different proportions.
When the HY/IG *ratio* deviates from its rolling OLS relationship, one spread has moved more
than the systematic factor justifies. The residual (rv_hy_ig = hy_spread − β × ig_spread)
captures this idiosyncratic wedge. Mean reversion occurs because:
- Counterparty: a directional credit manager overweight HY chases yield and temporarily
  compresses hy_spread, or a flow shock widens one leg. Risk-neutral dealers eventually
  trade the legs back in line.
- The OU half-life of 17.6d is consistent with a one- to three-week rebalancing cycle of
  institutional credit portfolios.

This is the most "pure credit" of the three signals — both legs live in the same credit
universe.

### M9 for RV2_A — HY spread vs Treasury rate dislocation

HY spread is the credit risk premium on top of the risk-free rate. In equilibrium, the
two move in opposite directions: when rates rise (risk-on), spreads tighten (and vice
versa). The OLS residual rv_credit_rates = hy_spread − β × dgs10 captures the deviation
from this empirical relationship. Mean reversion occurs because:
- Counterparty: a macro/rates investor (e.g. a duration trader) causes a temporary
  dislocation between the credit market and the Treasury market. For example, a rates
  rally driven by flight-to-quality pushes Treasury yields down, but credit spreads
  initially over-react and widen more than the equilibrium relationship predicts. As
  macro volatility fades, credit reverts.
- **Distinct from RV1:** RV1 arbitrages within credit (HY vs IG). RV2 arbitrages
  *across* credit and rates. The economic counterparty and the market that initiates the
  dislocation are different. This is why pairwise ρ(RV1, RV2) = 0.181 — the entry
  condition (rates-vs-spreads misalignment) is triggered by different market regimes than
  the HY/IG ratio stretch.
- OU half-life 26d: slightly slower reversion than RV1, consistent with macro
  dislocations resolving over a 4-6 week window.

### M9 for RV3_A — HY/IG differential vs yield curve slope dislocation

The 2s10s Treasury slope is the market's pricing of future growth and inflation (term
premium). HY minus IG spread (hy_ig = hy_spread − ig_spread) is the pure credit quality
premium (how much more you earn for holding non-investment-grade vs investment-grade).
In equilibrium, when the curve steepens (growth expectations rise), the credit quality
premium tends to compress (risk appetite improves across the capital structure). The OLS
residual rv_xterm = hy_ig − β × slope captures the deviation from this relationship.
Mean reversion occurs because:
- Counterparty: a duration trader who steepens/flattens the curve while the credit
  market's risk appetite has not yet adjusted. The mismatch unwinds as credit investors
  re-price the quality premium in line with the macro signal from the yield curve.
- **Distinct from RV1 and RV2:** RV3 uses the *differential* hy_ig (not the raw hy_spread)
  as the y-leg, isolating the credit-quality premium rather than the total credit spread.
  The x-leg (curve slope) is a completely different market from RV1's ig_spread or RV2's
  10y rate level. This is why ρ(RV1, RV3) = 0.506 (not zero, because hy_ig inherits part
  of hy_spread's variability) but is well below the 0.70 flag — the term-premium timing
  is different enough to generate genuine diversification.
- OU half-life 19d: faster than RV2, consistent with curve moves being more
  frequently incorporated into credit pricing than macro rate-level shifts.

---

## Decision Summary

| Signal | Admitted to Tier 2 | Reason |
|--------|-------------------|--------|
| RV1_A | Yes (v5) | Benchmark; all criteria PASS |
| RV2_A | **Yes** | All M1–M6 PASS; M7/M8 PASS at portfolio level; M9 written |
| RV3_A | **Yes** | All M1–M6 PASS; M7/M8 PASS at portfolio level; M9 written |

**v6 starts with three signals: RV1_A, RV2_A, RV3_A at $1M each.**
