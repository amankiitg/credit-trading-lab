# Sprint v6.5 — Engine Accounting Rescue

## Overview

Sprint v6 factor attribution revealed that 57% of Strategy A's gross P&L comes from
"model drift" — the OLS rolling intercept and beta re-parameterising during the hold,
not from market prices actually moving in our favour. The current engine computes:

```
gross = side × (rv_exit − rv_entry) × notional
```

where `rv_exit = rv_hy_ig_residual[exit_fill_date]` uses the **rolling β and α at the
exit date**, not the entry date. In real trading you open a fixed position at entry
proportions and hold them — the correct P&L is:

```
gross_fixed = side × (Δhy − β_entry × Δig) × notional
            = hy_leg_pnl + ig_hedge_pnl   (from v6 T2 decomposition)
```

This sprint answers one question before any further Tier 2 work: **does Strategy A's
edge survive fixed-entry accounting?** If yes, the engine is corrected and v5/v5.6
numbers are re-registered. If no, v5 Strategy A was largely an accounting artifact and
the entire Tier 1 conclusion must be revisited before proceeding.

---

## Economic Hypothesis

The drift arises because `rv_hy_ig_residual` at any date t equals `hy_t − αt − βt × ig_t`
where αt and βt are the trailing 252-day OLS estimates. As the window rolls forward,
αt and βt absorb recent price history — the residual can appear to revert simply because
the model re-centres toward current prices, regardless of whether the underlying
spread relationship actually changed. Real paper trading does not benefit from this:
you hold fixed dollar quantities of HYG and LQD opened at entry ratios. The model
drift is an accounting artifact, not tradeable P&L.

**What the rescue test determines:**
- If corrected Sharpe ≥ 0.40 and hit rate ≥ 65%: the z-score entry signal is genuinely
  predictive. The engine over-counted P&L but the signal survives. Fix the engine and
  re-register all numbers.
- If corrected Sharpe < 0.40 or hit rate collapses: Strategy A's edge was largely
  model-drift, not a real signal. The Tier 1 conclusion is invalid. Do not proceed to v7.

---

## Falsification Criteria

Pre-registered (must be established before engine fix is committed):

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|---------------|----------------|
| R1 | Corrected net Sharpe for RV1_A under fixed-entry accounting | ≥ 0.40 | Do NOT proceed to v7; revisit Tier 1 |
| R2 | Corrected hit rate for RV1_A | ≥ 60% (lower bar than v5 given accounting change) | Same |
| R3 | RV2_A and RV3_A corrected Sharpe both ≥ 0.40 | ≥ 0.40 each | Signals not admitted to Tier 2 |
| R4 | Relative ranking preserved: RV3_A ≥ RV2_A ≥ RV1_A in corrected Sharpe | order holds | Admission decisions from v5.6 may need revision |

---

## Corrected P&L Formula

**Fixed-entry gross P&L per trade (no engine change required for rescue test):**

```python
gross_fixed = side × (delta_hy − hedge_ratio_entry × delta_ig) × notional
net_fixed   = gross_fixed − cost      # cost unchanged (same trade, same dates)
```

where `delta_hy` and `delta_ig` are the changes in `hy_spread` and `ig_spread`
(log price ratios from features.parquet) from `entry_fill_date` to `exit_fill_date`.
These are already computed in `sprints/v6/attribution_table.csv`.

**Note:** this formula does NOT include the OLS intercept α. This is correct:
```
rv_exit_fixed − rv_entry
= [hy_exit − α_entry − β_entry × ig_exit] − [hy_entry − α_entry − β_entry × ig_entry]
= Δhy − β_entry × Δig
```
α_entry cancels out exactly. The fixed-entry P&L depends only on β_entry and the
raw spread moves.

**Engine fix (separate from rescue test, done only if R1/R2 pass):**
Modify `backtest/engine.py` to accept optional `y_series` and `x_series` (the raw
spread series), and at exit compute:
```python
rv_exit_fixed = rv_entry + (y[exit_fill] - y[entry_fill]) - hr[entry_fill] * (x[exit_fill] - x[entry_fill])
gross = sign * (rv_exit_fixed - rv_entry) * notional
      = sign * ((y[exit_fill] - y[entry_fill]) - hr[entry_fill] * (x[exit_fill] - x[entry_fill])) * notional
```
If y/x not provided, engine falls back to current behaviour (backward compatible).

---

## Data

- `sprints/v6/attribution_table.csv` — already contains `delta_hy`, `delta_ig`,
  `hedge_ratio_entry`, `gross_pnl` (old), `cost`, `net_pnl` (old) for all 94 trades.
  No new data loads needed for the rescue test.
- `data/processed/features.parquet` — `hy_spread`, `ig_spread` for re-running all
  three signals after the engine fix.

---

## Out of Scope

- Re-optimising entry/exit/stop parameters under new accounting (would be overfitting)
- Re-testing the equity_first gate (rejected in v5; accounting change doesn't revive it)
- Kalman or DV01 methods (disqualified in v5.5; still disqualified)
- Any v7 (scenario risk) or v8 (paper trading) work until R1/R2 are confirmed
- Intraday or tick-level P&L reconciliation

---

## Dependencies

- `sprints/v6/attribution_table.csv` — primary input for rescue test
- `sprints/v6/notes.md` — T2 decomposition numbers (model_drift = $485,641 total)
- `sprints/v5.6/signal_selection.md` — registered v5.6 performance numbers (to be
  updated if R1/R2 pass and engine is fixed)
- `backtest/engine.py` — modified in T4 if R1/R2 pass
- `backtest/ab_test.py` — re-run all three signals in T5
