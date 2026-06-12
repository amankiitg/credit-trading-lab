# Sprint v5.6 — Notes

## S1 · Scaffold + baseline reconfirmation
**Date: 2026-06-12**

### features.parquet confirmed post-v5.5
- Shape: 4784 × 56 ✓ (matches v5.5 deliverable)
- Date range: 2007-04-11 → 2026-04-15
- rv_hy_ig OLS half-life: 17.6d ✓ (in [5,63] band, v5.5 E2 PASS)
- Random baseline p95: 1.70 (from data/benchmarks/random_baseline.parquet, reused for M6)

### RV1_A benchmark — fixed from v5, NOT re-tested
| metric | value | source |
|--------|-------|--------|
| net Sharpe | 0.591 | sprints/v5/WALKTHROUGH.md |
| hit rate | 80.9% | sprints/v5/WALKTHROUGH.md |
| n_trades | 94 | sprints/v5/WALKTHROUGH.md |
| total net P&L | $760,372 | sprints/v5/WALKTHROUGH.md |
| avg hold | 15.7d | sprints/v5/WALKTHROUGH.md |
| max drawdown | −$152,025 | sprints/v5/WALKTHROUGH.md |

### Unregistered numbers — DO NOT USE as validated results
The following were computed informally on 2026-06-12 during a session exploring
ungated multi-signal performance. They are recorded here ONLY to motivate the
sprint. Every criterion below (M1–M9) must be evaluated independently:

- RV2_A (rv_credit_rates, OLS, no gate): **unregistered** Sharpe ~0.693, ~103 trades
- RV3_A (rv_xterm, OLS, no gate): **unregistered** Sharpe ~0.856, ~101 trades

These numbers are treated as zero-information priors. S2 will reproduce them
from scratch under the pre-registered framework.

---

## S2 · Standalone backtest — RV2_A and RV3_A
**Registered results (reproduced from scratch, not from informal run)**

| metric | RV1_A (v5 benchmark) | RV2_A | RV3_A |
|--------|---------------------|-------|-------|
| net Sharpe | 0.591 | **0.6928** | **0.8560** |
| hit rate | 80.9% | 75.7% | 82.2% |
| n_trades | 94 | 103 | 101 |
| avg hold | 15.7d | 14.6d | 13.8d |
| max drawdown | −$152,025 | −$175,795 | −$100,634 |
| total net P&L | $760,372 | $994,573 | $990,258 |
| M1 (Sharpe>0.40) | PASS | **PASS** | **PASS** |
| M2 (hit>65%) | PASS | **PASS** | **PASS** |

Parameters confirmed: entry=2.0, exit=0.5, stop=4.0, fill_lag=1, notional=$1M.
RV1_A reference line on equity plot matches v5 benchmark exactly.

---

## S3 · Single-trade dominance — M3

| signal | max single |net_pnl| | total net P&L | max share | M3 |
|--------|------------------|---------------|-----------|-----|
| RV2_A | $149,127 (2020-03-20 entry) | $994,573 | 15.0% | **PASS** |
| RV3_A | $83,251  (2020-03-20 entry) | $990,258 | 8.4% | **PASS** |

Post-mortem on largest trades: both entered 2020-03-20 (COVID crash trough).
RV2_A entered LONG on rv_credit_rates (HY cheap vs rates, z < −2 during spike);
RV3_A entered LONG on rv_xterm (hy_ig differential dislocated vs curve slope).
Both are genuine crisis dislocations — not stop-outs or data errors. RV2_A's
15% share is high but well within the 25% M3 threshold.

---

## S4 · Parameter grid robustness — M4

| signal | cells with Sharpe>0 | fraction | M4 |
|--------|---------------------|----------|----|
| RV2_A | 27/27 | 1.00 | **PASS** |
| RV3_A | 27/27 | 1.00 | **PASS** |

Both signals have positive standalone Sharpe in every single cell of the
entry × exit × stop grid. This is stronger than required (≥60%).

---

## S5 · Subperiod stability — M5
Split at 2016-09-01 (consistent with v5).

| signal | first half (→2016-09) | second half (2016-09→) | M5 |
|--------|----------------------|----------------------|----|
| RV1_A | 0.523 | 0.674 | PASS |
| RV2_A | 0.706 | 0.735 | **PASS** |
| RV3_A | 0.834 | 0.879 | **PASS** |

RV2_A and RV3_A are stable across both halves, with second half slightly
stronger than first — no degradation pattern.

---

## S6 · Random baseline — M6
Random p95 = 1.70 (1000 simulations, 64 trades each, hy_spread — reused from v1).

| signal | n_trades | per-trade Sharpe | excess vs p95 | sqrt(n) inflation vs 64 | M6 |
|--------|----------|-----------------|---------------|------------------------|----|
| RV2_A | 103 | 4.290 | +2.590 | 1.27× | **PASS** |
| RV3_A | 101 | 5.586 | +3.887 | 1.26× | **PASS** |

Methodological caveat (logged per PRD): RV2/RV3 have ~103/101 trades vs 64 in
the baseline. The sqrt(n) scaling in per-trade Sharpe gives them a ~1.26–1.27×
advantage over the baseline. Even adjusting: RV2 deflated = 4.29/1.27 = 3.38
(still +1.68 excess); RV3 deflated = 5.59/1.26 = 4.44 (still +2.74 excess).
Both pass M6 even with conservative sqrt-adjustment.

---

## S7 · Cross-signal correlation + portfolio — M7, M8

**M7 — pairwise daily P&L correlation:**
|       | RV1_A | RV2_A | RV3_A |
|-------|-------|-------|-------|
| RV1_A | 1.000 | 0.181 | 0.506 |
| RV2_A | 0.181 | 1.000 | 0.351 |
| RV3_A | 0.506 | 0.351 | 1.000 |

No pair exceeds ρ=0.70. RV1/RV2 correlation is very low (0.181) despite both
using hy_spread as a leg — different x-leg (ig_spread vs 10y rate) creates
genuine independence. RV1/RV3 is the highest (0.506) — both involve hy_spread
in some form. M7: **OK — no excessive correlation**.

**M8 — equal-weight portfolio (all 3 signals, $1M each):**
| metric | value |
|--------|-------|
| Portfolio Sharpe | **0.9473** |
| Best individual (RV3_A) | 0.8560 |
| Ratio | 1.107 (> 0.85 threshold) |
| M8 | **PASS** |
| Portfolio total net P&L | $2,745,203 |
| Portfolio max drawdown | −$256,142 |
| Portfolio active days | 2866 / 4784 (59.9%) |

Portfolio Sharpe 0.947 exceeds the best individual (0.856) — low pairwise
correlations (especially RV1/RV2 = 0.181) provide genuine diversification.
The portfolio is in a trade 60% of days vs 33% for any individual signal —
higher utilisation partially explains the Sharpe improvement.

---
## S8 · Notebook `56_multisignal_validation.ipynb`
**Completed 2026-06-12**

Generator script: `scripts/build_notebook_v5_6.py`
Output: `notebooks/56_multisignal_validation.ipynb` (11 cells, 4 sections)

Executed clean via `jupyter nbconvert --execute`. All cells produced output.
Key outputs verified:
- Cell 3 scorecard: all M1–M6 PASS for RV1/RV2/RV3, numbers match S2–S6 exactly
- Cell 7 subperiod: RV1 0.523/0.674, RV2 0.706/0.735, RV3 0.834/0.879 — all PASS
- Cell 9 portfolio: ρ_max=0.506, portfolio Sharpe=0.9473, ratio=1.107 — M8 PASS

Plots refreshed: equity_rv2_rv3.png, grid_rv2_rv3.png (combined), subperiod.png,
portfolio_equity.png — all in sprints/v5.6/plots/.

---

## S9 · Signal selection + sprint close
**Completed 2026-06-12**

`sprints/v5.6/signal_selection.md` written. Contains:
- M1–M8 scorecard table with all stored numbers
- Admission list: RV1_A, RV2_A, RV3_A — all three admitted
- Exclusion list: all gated variants (B), all Kalman variants, DV01
- Portfolio recommendation: $1M each, equal weight, Sharpe 0.947 expected
- Multiple-testing note: Bonferroni doesn't change the decision
- M9 economic intuition for all three signals

**Sprint v5.6 closed. All S1–S9 marked [x].**

Decision binding for v6: three signals admitted — RV1_A, RV2_A, RV3_A.
