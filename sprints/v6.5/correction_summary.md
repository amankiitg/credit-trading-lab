# Sprint v6.5 — Correction Summary
**Date: 2026-06-13 | Scope: RV1_A, RV2_A, RV3_A | All Tier 1 signals**

---

## (a) Root Cause: Rolling OLS Intercept Is Not Tradeable P&L

Every Tier 1 signal uses an OLS residual of the form:

```
rv[t] = y[t] − α[t] − β[t] × x[t]
```

where `α[t] = mean_y[t] − β[t] × mean_x[t]` is re-estimated on a trailing 252-day
window at each date. As the window rolls forward, both `α` and `β` change to fit
current price history. When a trade is entered at `rv[entry]` (large dislocation),
the residual appears to revert over the hold because the rolling window re-centres
`α` toward current prices — not because market prices actually reverted. The engine
books this re-centring as profit ("model drift").

The effect is largest for signals where the x-series has a strong secular trend:
- **RV2_A** (y = hy_spread, x = dgs10): US 10-year yield went 5% → 0.5% → 5% from
  2007 to 2026. With `β_entry` values as large as 33.7, one rate move of 100bp
  during a 30-day hold contributes `33.7 × 0.01 × $1M = $337,000` from the rate
  leg alone — the rolling OLS absorbed this secular trend as "reversion."
- **RV3_A** (y = hy_ig, x = slope): same mechanism via yield curve slope.
- **RV1_A** (y = hy_spread, x = ig_spread): both legs are credit ETF ratios and
  co-move tightly; model drift is smaller (~$486k total) but still fails R1.

---

## (b) Disqualification Table — Corrected vs Registered Sharpe

Daily MTM fixed-entry reconstruction: each holding day d contributes
`sign × ((y[d]-y[d-1]) - β_entry × (x[d]-x[d-1])) × notional - borrow_per_day`.
α cancels in the daily difference. Reconciliation diff = $0.00 for all three signals.

| signal | registered Sharpe | corrected Sharpe | delta | corrected hit rate | corrected net P&L | R1 (≥0.40)? |
|--------|------------------|--------------------|-------|-------------------|--------------------|-------------|
| RV1_A  | 0.591            | **0.202**          | −0.389 | 68.1%            | $274,731           | **FAIL**    |
| RV2_A  | 0.693            | **−0.108**         | −0.801 | 49.5%            | −$13,067,119       | **FAIL**    |
| RV3_A  | 0.856            | **−0.187**         | −1.043 | 54.5%            | −$10,828,427       | **FAIL**    |

Source: computed in T1 (RV1_A) and T2/Option C (RV2_A, RV3_A).

---

## (c) Withdrawal Statement

**All v5/v5.6 Tier 1 admission decisions are withdrawn as of 2026-06-13.**

No Tier 2 work (v6 factor attribution, v7 scenario risk, v8 paper trading) will
proceed under the OLS residual signal architecture. The backtest numbers in
`sprints/v5/WALKTHROUGH.md`, `sprints/v5.6/signal_selection.md`, and
`sprints/v6/attribution_summary.md` are superseded by the corrected numbers in
this document. They are retained for reference but are no longer the working basis
for any forward sprint.

---

## (d) Option B Signal Specification

Replace the rolling OLS residual with the raw spread difference, z-scored:

| parameter | value |
|-----------|-------|
| signal name | `hy_ig` (pure log-spread ratio) |
| formula | `hy_ig = hy_spread − ig_spread = ln(HYG/IEF) − ln(LQD/IEF)` |
| z-score | `hy_ig_z252` — trailing 252-day rolling z-score of hy_ig |
| column | `hy_ig_z252` in `data/processed/features.parquet` ✓ (already computed) |
| entry LONG | z < −2 (HY cheap relative to IG on a 252-day basis) |
| entry SHORT | z > +2 |
| exit | |z| < 0.5 |
| stop | unchanged from v5 (configurable) |
| P&L formula | `side × Δhy_ig × notional` — no hedge ratio, no OLS, no model drift |
| engine change | none required — `hy_ig` is a raw spread, not a residual |

**Why no model drift is possible:** `hy_ig[t] = hy_spread[t] − ig_spread[t]` has no
free parameters. The z-score is used only as an entry signal, not for P&L sizing.
The daily MTM is `sign × (hy_ig[d] - hy_ig[d-1]) × notional` — a direct price
difference with nothing to re-parameterise.

**Economic intuition:** HYG and LQD are both investment-grade/high-yield ETFs vs
Treasuries. The ratio `hy_ig` measures how much HY is cheap or rich relative to IG.
When HY widens sharply relative to IG (z < −2), credit stress is amplified in the HY
segment — this is the dislocation. Mean reversion here means the credit quality spread
compresses back toward its historical norm, which is a more stable relationship than
either spread vs rates.

---

## (e) Next Sprint

**v7 (new Tier 1): Option B hy_ig z-score signal**

This is not a continuation of Tier 2; it is a fresh Tier 1 validation of the hy_ig
signal under honest fixed-entry accounting. The same M1–M8 gates from sprint v5 apply.
The engine is unchanged — `hy_ig` is already a raw spread; the engine's daily MTM loop
will produce honest P&L because `rv = hy_ig` has no rolling intercept to re-centre.

Relevant data:
- `hy_ig` and `hy_ig_z252` are in `data/processed/features.parquet`
- Entry/exit thresholds use the existing z-score framework
- No new data ingestion needed

The factor attribution framework (v6) and scenario risk framework (v7 in the old
numbering) are deferred until Option B passes Tier 1 under corrected accounting.
