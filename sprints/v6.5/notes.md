# Sprint v6.5 — Notes
**Signal: RV1_A (rv_hy_ig, OLS, no gate) | 94 trades 2007–2026 | $1M notional**

---

## T1 — Rescue Test: Fixed-Entry P&L for RV1_A (2026-06-13)

### Methodology

The engine computes daily mark-to-market P&L as:

```
daily[d] += sign × (rv[d] - rv[d-1]) × notional       for d in (entry_fill, exit_fill]
daily[d] -= borrow_per_day
daily[entry_fill] -= spread_slippage
```

where `rv[d] = hy_spread[d] - α[d] - β[d] × ig_spread[d]` uses the **rolling OLS β and α at
date d** (the trailing 252-day window that includes day d). This means as the window rolls
forward, both α and β change — the residual re-centres toward current prices independent of
whether market prices actually moved favourably.

The corrected formula replaces the rolling residual difference with a fixed-β difference:

```
daily_fixed[d] = sign × ((hy[d] - hy[d-1]) - β_entry × (ig[d] - ig[d-1])) × notional - borrow_per_day
daily_fixed[entry_fill] -= spread_slippage
```

where `β_entry = hedge_ratio_entry` (the OLS β at entry_signal_date, used to size the position
at inception). α cancels exactly in the difference, so the daily fixed MTM depends only on β_entry
and the raw daily moves of hy_spread and ig_spread. Same entries, exits, cost — only the
MTM computation changes.

**Reconciliation check (exact):**
- Sum of fixed daily MTM across all positions: $274,731
- Sum of trade-level formula `Σ sign × (Δhy − β_entry × Δig) × notional − cost`: $274,731
- Difference: $0.00 ✓

### Results

| metric | v5 registered (engine MTM) | corrected (fixed-β daily MTM) | delta |
|--------|---------------------------|-------------------------------|-------|
| net Sharpe (annualised) | 0.591 | **0.202** | −0.389 |
| hit rate | 80.9% | 68.1% | −12.8pp |
| n_trades | 94 | 94 | 0 |
| total net P&L | $760,372 | $274,731 | −$485,641 |
| max drawdown | −$152,025 | −$234,178 | −$82,153 |

The $485,641 gap is exactly the model_drift component found in v6 T2 — it was 57% of v5
gross P&L ($485,641 / $849,632 = 57.2%). Stripping that artifact drops total net by the
same amount.

### R1/R2 Verdicts

| ID | Criterion | Value | Threshold | Verdict |
|----|-----------|-------|-----------|---------|
| R1 | Corrected Sharpe ≥ 0.40 | **0.202** | 0.40 | **FAIL** |
| R2 | Corrected hit rate ≥ 60% | 68.1% | 60% | PASS |

**R1 FAILS. Gate closed.**

### Interpretation

The hit rate remaining at 68.1% confirms the entry signal retains directional predictability:
the strategy correctly identifies the direction of the next mean-reversion move on 68 of 100
trades (vs 81% under biased accounting). The signal is real in the sense that entry timing
beats a coin flip by a meaningful margin.

The Sharpe collapse from 0.591 → 0.202 is driven entirely by magnitude: trades that
appeared profitable because the OLS window re-centred were typically small winners with
barely positive fixed-entry P&L, while true dislocations that reverted cleanly remain
winners under fixed-entry accounting. The volatility of fixed daily MTM is also higher
than rolling-residual MTM (more raw spread noise, no model smoothing), which compounds
the Sharpe reduction.

At Sharpe 0.202 with $275k net over 19 years ($14k/yr on $1M), the strategy does not
clear the minimum viability threshold for Tier 2 continuation under the current engine.

### Gate Outcome

**GATE CLOSED. Do not proceed to T2–T7.**

Per PRD.md falsification criteria: "If corrected Sharpe < 0.40 or hit rate collapses:
Strategy A's edge was largely model-drift, not a real signal. The Tier 1 conclusion is
invalid. Do not proceed to v7."

The Tier 1 conclusion requires revision. The z-score entry signal retains some directional
validity (68% hit rate), but the P&L magnitude is insufficient to support Tier 2 expansion
under honest accounting.

---

## Option C — RV2_A and RV3_A fixed-entry rescue (2026-06-13)

User chose Option C: check RV2_A and RV3_A under the same fixed-entry accounting before
committing to a path. Results are decisive.

### Methodology

Same daily MTM reconstruction as T1, applied to each signal's native y/x series:
- RV2_A: y = hy_spread, x = dgs10 (decimal, from credit_market_data.parquet, forward-filled)
- RV3_A: y = hy_ig (= hy_spread − ig_spread), x = slope (= dgs10 − dgs2)

Both reconciliations pass ($0.00 diff between daily MTM sum and trade-level formula).

### Results

| signal | registered Sharpe | corrected Sharpe | delta | n_trades | R1 (≥0.40)? |
|--------|------------------|-----------------|-------|----------|-------------|
| RV1_A  | 0.591            | **0.202**        | −0.389 | 94      | **FAIL** |
| RV2_A  | 0.693            | **−0.108**       | −0.801 | 103     | **FAIL** |
| RV3_A  | 0.856            | **−0.187**       | −1.043 | 101     | **FAIL** |

| signal | old hit rate | fixed hit rate | old net P&L | fixed net P&L |
|--------|-------------|---------------|-------------|---------------|
| RV1_A  | 80.9%       | 68.1%         | $760,372    | $274,731      |
| RV2_A  | 75.7%       | 49.5%         | $994,573    | −$13,067,119  |
| RV3_A  | 82.2%       | 54.5%         | $990,258    | −$10,828,427  |

### Interpretation

**RV1_A** is the least damaged. Hit rate holds at 68% (directional content retained).
The model_drift was $486k on a $995k registered gross — about half. The signal has
real directional content but insufficient magnitude to clear R1.

**RV2_A and RV3_A collapse catastrophically.** Corrected net P&Ls are −$13M and −$11M
against registered values of ~$1M each. This is not a small accounting adjustment — it
is a fundamental inversion.

The root cause is the same as RV1_A but amplified by the x-series being a rate or
slope (dgs10, dgs10−dgs2) rather than a credit spread. Rates have secular trends
spanning the entire 2007–2026 sample (5% → 0.5% → 5%). With large β_entry values
(RV2_A mean 7.5, range up to 33.7), the daily rate leg contribution in fixed-entry
MTM is enormous. The rolling OLS intercept α was continuously absorbing this secular
trend — what looked like P&L from mean reversion was almost entirely the model
re-centring as the rate cycle evolved.

**RV3_A** is the same issue: hy_ig vs yield-curve slope. Slope trends across regimes
(steep in 2010-2012, flat/inverted in 2018-2019 and 2022-2023). The OLS rolling
window was tracking this — fixed-entry accounting strips it out completely.

### Architecture Conclusion

All three Tier 1 signals are disqualified under honest fixed-entry accounting. The
entire Tier 1 result set was dominated by OLS model re-centring, not market mean
reversion. This is not a parameter problem; it is an architecture problem with using
a rolling OLS intercept in the residual construction.

The one partial exception is RV1_A: the hit rate holds at 68% (vs 50% random), suggesting
the z-score entry signal has genuine directional information. But the magnitude of
tradeable P&L is insufficient for Tier 2.

Plots saved:
- `sprints/v6.5/plots/option_c_all_signals.png` — equity curves, all 3 signals, old vs corrected
- `sprints/v6.5/plots/option_c_sharpe_comparison.png` — bar chart, all 6 Sharpes vs R1 threshold

---

### What the User Must Decide Before Any Further Work

Option C has now been executed (see section above). Both RV2_A and RV3_A fail catastrophically,
eliminating the portfolio diversification path.

**Remaining paths:**

Option A — **RV1_A signal salvage with corrected accounting**: RV1_A is the only signal
with a positive corrected Sharpe (0.202) and retained directional content (68% hit rate).
Explore whether a tighter entry threshold (|z|>2.5 or |z|>3) or maximum hold-period cap
(≤20d) improves corrected Sharpe above 0.40. This uses the same architecture but with
fixed-entry accounting from the start. New Tier 1 sprint with honest engine.

Option B — **Architecture change**: Drop the rolling OLS residual for a signal that doesn't
embed a rolling intercept. The most natural alternative is hy_ig = hy_spread − ig_spread
(the pure log-spread ratio) z-scored over a trailing window, with no hedge ratio. This is
the underlying spread between HY and IG ETFs and should be more stationary (both are credit;
long-run relationship should be tighter). No β, no α, no model drift. Would need a new Tier 1
sprint to validate under the corrected engine from the start.

**Do not commit to any option without discussing with the user.**

### Plot

`sprints/v6.5/plots/rescue_pnl_comparison.png` — old vs corrected cumulative P&L (left panel)
and waterfall bar showing model drift removal (right panel).
