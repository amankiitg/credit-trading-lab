# Sprint v5.5 — Foundation Repair (Walkthrough)

## Summary

A **corrective** sprint run as a gate before Tier 2 — no new research, no
new strategy, no engine changes. An audit of the signal-generation layer
found that **three different residual series were travelling under the
single name `rv_hy_ig`**, and the one published to `features.parquet` and
the dashboard was a *whitened* (overfit) residual that the v5 backtest
itself refused to trade. This sprint collapses them to one honest,
consistently-consumed residual, re-points the falsification gates that were
being evaluated on the wrong series, and re-validates the deliverable.

**The deliverable did not change. Strategy A: net Sharpe 0.591, 81% hit,
94 trades — bit-identical to v5.** The repair removed dishonesty in the
layer *around* the strategy (selection, publication, the other pairs'
presentation), not the strategy itself. All six pre-registered criteria
(E1–E6) pass.

## The defect

`signals.pipeline.enrich_with_rv` selected the published residual via
`select_best_method`, which ranks hedge methods by **lowest ADF p-value**.
ADF tests stationarity — and a *whitened* residual is trivially stationary
(p ~ 1e-21). So the selector systematically picked the **Kalman posterior**
residual for all three pairs and wrote it to `features.parquet`:

- **Posterior residual** = `yₜ − Hₜ·sₜ|ₜ`, measured *after* the filter
  absorbed `yₜ` into the state. It is a shrunk version of the innovation
  (`eₜ·R/S`), so it whitens to near-noise: std 0.005, half-life ~1.5 days.
- A 1.5-day half-life on daily bars *is* white noise — nothing persistent
  left to trade. Yet it won every ADF comparison and was the signal shown
  in the dashboard's Today View, conviction tiers, and `z_rv`.

Meanwhile the v5 backtest had already diagnosed this (v5 PRD: "Kalman is
over-fit — half-life ~1.5d") and traded the **OLS** residual. So the
pipeline and the backtest disagreed about what `rv_hy_ig` *was*. Two
further defects rode along: `rv_xterm`'s DV01 hedge ratio was a verbatim
copy of pair-1's 4y/9y bond ratio, and the C22 "43–67% shorter half-life"
thesis headline was computed on the whitened residual.

## What was built

**1 — Kalman residual → one-step-ahead innovation (E3).** `kalman_hedge`
now stores `eₜ = yₜ − Hₜ·sₜ|ₜ₋₁` (prediction error from the *prior* state,
past-data-only — the tradeable deviation) instead of the posterior
leftover. One-line change; β untouched. Std rises 0.0046 → 0.0077 (the old
"20× smaller variance than OLS" was mostly the posterior artifact).

**2 — Tradeability selector (E2).** `select_tradeable_method` replaces
`select_best_method`. A method qualifies only if its residual is
**stationary (ADF p < 0.05) AND has a tradeable OU half-life ∈ [5, 63]
days**; tiebreak = most stable hedge ratio (lowest rolling-63d CV). The
half-life band is the discriminator ADF lacks: it rejects whitened
residuals (< 5d) *and* too-slow non-reverters (> 63d). Returns `None` if no
method qualifies — a pair is reported *not tradeable*, never force-filled.

**3 — Single source of truth + `rv_xterm` DV01 removed (E4).**
`canonical_residuals()` is the one place a method is chosen and a residual
computed; pipeline, dashboard, and backtest all consume it. `rv_xterm`'s
DV01 (the copy-paste) is removed — there is no clean bond-DV01 hedge for a
credit-differential vs a curve slope, so it selects among {OLS, Kalman} only.

**4 — Consumers rewired (E1).** `enrich_with_rv` regenerates
`features.parquet` from `canonical_residuals`; `backtest.ab_test` resolves
its method from the selector (`method=None`) instead of a hardcoded `"ols"`.
A test asserts the residual stored on disk, read by the dashboard loader,
and fed to the engine are **bit-identical**.

**5 — Gates re-pointed + claims restated (R6/E5).** C19/C21 now test the
canonical residual and the `[5, 63]` band (was `[1, 126]`, which
rubber-stamped the whitened residual). C22 is restated on OLS only.

## Falsification results

| | criterion | result |
|---|---|---|
| E1 | features / dashboard / backtest residual bit-identical | ✓ PASS (`check_exact`) |
| E2 | no stored residual half-life < 5d | ✓ PASS (18 / 26 / 19 d) |
| E3 | Kalman residual is the innovation, std > 0.00463 | ✓ PASS (0.0077) |
| E4 | `rv_xterm` DV01 removed / not a copy of pair-1 | ✓ PASS |
| E5 | C22 restated on canonical residual, degenerate legs excluded | ✓ PASS |
| E6 | Strategy A re-validated, ∣ΔSharpe∣ ≤ 0.1 vs v5 | ✓ PASS (Δ +0.001) |

## The selector, on real data

| pair | OLS (chosen) | Kalman | DV01 |
|---|---|---|---|
| `rv_hy_ig` | adf 3.6e-7, HL **18d** ✓ | adf 5.9e-23, HL 1.5d ✗ | adf 0.69, HL 455d ✗ |
| `rv_credit_rates` | adf 1.2e-14, HL **26d** ✓ | adf 2.2e-23, HL 2.2d ✗ | adf 0.78, HL 624d ✗ |
| `rv_xterm` | adf 5.1e-7, HL **19d** ✓ | adf 4.8e-21, HL 1.9d ✗ | (removed) |

The defect in one table: **Kalman has the lowest ADF p-value in every row**
(most "stationary") so the old selector picked it — yet its 1.5–2.2 day
half-life is whitened noise. The band rejects it; DV01 is rejected as
too-slow/non-stationary; **OLS is selected for all three** — the method the
backtest already traded.

## What changed, what did not

| | before (audit) | after (repair) |
|---|---|---|
| published `rv_hy_ig` residual | whitened Kalman, std 0.005, HL 1.5d | OLS, std 0.023, HL 18d |
| dashboard / backtest residual | **different series** | **bit-identical** |
| `rv_xterm` DV01 | copy of pair-1 ratio | removed |
| `regime_signal_quality.parquet` | 63 rows | 56 rows |
| C22 framing | "43–67% across methods" | **67%, OLS only** |
| dashboard HIGH-conviction days | 45 (on noise) | 91 (on OLS signal) |
| **Strategy A net Sharpe** | **0.59** | **0.591 (bit-identical)** |

## Restated C22

On the canonical OLS residual, RV1's `equity_first` half-life (5.3d) is
**67% shorter** than `neither` (16.2d) — C22 still passes (threshold > 20%).
The equity-credit-lag effect is **real on the tradeable residual**, fully
consistent with the v5 verdict that it isn't monetizable as a *gate*. The
old "43–67% across methods" framing is retired: the 43% end was the
whitened Kalman residual (0.85d vs 1.46d = noise vs noise) and the DV01 leg
is non-stationary. "Across methods" implied a robustness that did not exist.

## Limitations / what this does not fix

- **The OLS hedge instability is real and remains** (C23 still fails on
  max-CV: β crosses zero on ~6% of days, regime-shifts in 2020). v5.5 makes
  the *selection* honest; it does not make the OLS hedge stable. Quantifying
  how much of Strategy A's P&L rides on the unstable-hedge episodes is a
  Tier-2 v6 (factor attribution) / v7 (scenario risk) job.
- **C18 non-degeneracy still fails** (`neither` is 85% of days) — unchanged,
  a documented v3 honest failure.
- This sprint does **not** re-open the v5 thesis verdict (the equity_first
  gate stays rejected) and does not tune any threshold.

## Reproducibility

```bash
# regenerate features.parquet via the canonical pipeline
PYTHONPATH=python/credit python -c "import pandas as pd; from signals.pipeline import enrich_with_rv; enrich_with_rv(pd.read_parquet('data/processed/features.parquet'))"

# before/after notebook (4 sections, 4 plots)
python scripts/build_notebook_v5_5.py
PYTHONPATH=python/credit jupyter nbconvert --to notebook --execute --inplace notebooks/55_foundation_repair.ipynb

# full suite — 202 pass, 3 documented honest failures (C18 ×2, C23 ×1)
PYTHONPATH=python/credit python -m pytest tests/ -q
```

Pre-repair artifacts are preserved byte-for-byte under
`data/processed/_pre_v5_5/` and `data/results/_pre_v5_5/` for the
before/after comparison.

## Test suite status

205 tests (grew from 190 — the +15 `test_canonical.py` cases for E1–E4 +
R9 leakage). **202 pass, 3 fail** — and the 3 are exactly the pre-registered
honest failures carried since v3: `test_c18_non_degenerate` ×2 and
`test_c23_hedge_ratio_cv_under_one` ×1. No new regression; the suite never
shrank.

## Next steps

The foundation is honest and consistent — Tier 2 (v6 factors → v7 scenarios
→ v8 paper trading) can build on a single residual the pipeline, dashboard,
and backtest all agree on. v6 should put a number on the OLS-hedge-
instability exposure that C23 flags; the restated `signal_freq` (28%
equity_first vs 23% neither, on OLS) is a candidate position-*sizing* input
for v10.
