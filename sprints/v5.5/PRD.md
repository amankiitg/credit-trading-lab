# Sprint v5.5 — Foundation Repair (pre-v6 gate)

A **corrective** sprint, not new research. Tier 1 (v1–v5) is built and
the v5 thesis rejection stands. But auditing the signal-generation
layer before starting Tier 2 surfaced a foundational inconsistency:
**three different residual series travel under the single name
`rv_hy_ig`**, and the one published to `features.parquet` and the
dashboard is a known-overfit (whitened) residual. This sprint collapses
them to one principled, honest, consistently-consumed residual, then
re-validates Strategy A and restates the C22 half-life finding. No new
signals, no new strategy, no engine changes.

## Overview

The v5 PRD already diagnosed the core problem (§Signal Definition,
lines 110–115): *"Kalman is over-fit — half-life ~1.5d; DV01 is too
slow — ~450d"*, and so the v5 backtest deliberately fixed the canonical
hedge method to **OLS rolling-126d** for Strategy A. The backtest was
right. The defect is that the **data pipeline never adopted that
decision**: `signals.pipeline.enrich_with_rv` still calls
`select_best_method`, which ranks hedge methods by **lowest ADF
p-value**. ADF rewards whitening, so it systematically selects the
Kalman *posterior* residual — the most over-fit candidate — and writes
*that* into `features.parquet`. Everything downstream of the parquet
(Today View, `z_rv`, conviction tiers, the C22 regime-quality table)
therefore presents a residual that **the backtest itself refused to
trade**.

This sprint:

1. Replaces the ADF selector with a **tradeability** selector
   (stationary **and** half-life in a tradeable band; tiebreak on
   hedge-ratio stability).
2. Fixes the Kalman residual to store the **one-step-ahead innovation**
   (prior residual, past-data-only) instead of the posterior leftover.
3. Routes pipeline, dashboard, and backtest through a single
   `canonical_residuals()` function so all three consume **bit-identical**
   series.
4. Fixes or removes the `rv_xterm` DV01 hedge ratio, which is currently
   a silent copy of pair-1's 4y/9y ratio.
5. Re-validates Strategy A on the unified residual and restates C22
   honestly.

## Economic Hypothesis

Not a return hypothesis — a **methodological** one. Claim under test:
*the residual we publish, visualize, and quote half-life statistics for
should be the same tradeable residual the backtest monetizes, and it
should be selected by a criterion that cannot be won by destroying the
signal.* The failure mode being corrected is **selection on
stationarity**: a whitened residual (level-state absorbing each
observation) is trivially stationary (ADF p ~1e-17) and trivially
"reverts" (half-life < 1 day), yet has no persistent dislocation left to
trade. A correct selector must reward a residual that is *both*
stationary *and* mean-reverting on a horizon a trader can actually hold.

**Who is on the other side / why it could be wrong.** N/A in the trading
sense. The way this sprint could "fail" is if, after honest selection,
**no hedge method qualifies for a pair** (e.g. `rv_credit_rates` or
`rv_xterm` have no method that is both stationary and in-band). That is
a legitimate, documentable outcome — we report the pair as *not
tradeable* rather than forcing a pick. A second way it could fail: the
re-validated Strategy A net Sharpe departs materially from the v5 0.59.
Since Strategy A was already OLS, a large departure would itself signal
a deeper bug and must be investigated, not papered over.

## Falsification Criteria

Pre-registered, written before any code changes. Continues the Tier-1
namespace; uses the **E** (errata) prefix, E1–E6, picking up after C31.

- **E1 — One residual, consumed identically.** A single
  `canonical_residuals(features, cmd, pycredit)` returns, per pair, the
  chosen `(method, residual, hedge_ratio, z)`. The residual that
  `features.parquet` stores, the residual the dashboard reads, and the
  residual `backtest.ab_test` trades are **bit-identical** for each pair
  (`pandas.testing.assert_series_equal`, exact). Fails if any two paths
  differ by more than floating-point round-trip noise.

- **E2 — No whitened residual survives selection.** Every *selected*
  (stored) residual has OU half-life **≥ 5 trading days** and ADF
  p < 0.05 on post-warmup data. No selected residual has half-life
  < 5 days. If a pair has no qualifying method, `features.parquet`
  stores NaN for that residual and the pair is listed as *not tradeable*
  in `sprints/v5.5/notes.md` — it is **not** back-filled with a
  disqualified method.

- **E3 — Kalman residual is honest.** The Kalman series produced by
  `kalman_hedge` is the **one-step-ahead innovation** `yₜ − Hₜ·sₜ₋₁`
  (uses only data ≤ t−1 to form the prediction), not the posterior
  `yₜ − Hₜ·sₜ`. Verified by: (a) a unit test asserting the stored Kalman
  residual at t equals the prediction error against the *prior* state;
  (b) the full-sample Kalman residual std is **strictly greater** than
  the old posterior std (0.00463 for `rv_hy_ig`).

- **E4 — `rv_xterm` DV01 is real or absent.** The DV01 hedge ratio for
  `rv_xterm` is either (a) derived from the instruments it actually
  hedges, with the derivation documented, or (b) removed, with the
  method marked unavailable for pair 3. A test asserts the `rv_xterm`
  DV01 ratio series is **not element-wise equal** to the `rv_hy_ig`
  DV01 ratio (the current copy-paste defect).

- **E5 — C22 restated honestly.** The equity-credit-lag half-life
  comparison is recomputed on the **canonical tradeable residual only**.
  Degenerate method legs (half-life < 5 days = whitened, or
  non-stationary) are **excluded** from any "shorter half-life on
  `equity_first`" claim. `sprints/v5.5/notes.md` records the restated
  number and explicitly retires the old "43–67% across methods" framing,
  whose 43% leg was the whitened Kalman residual.

- **E6 — Strategy A re-validated, reported faithfully.** Strategy A
  (RV1, canonical hedge, no gate) is re-run through the **unchanged** v5
  engine on the unified residual. The new net Sharpe, hit rate, trade
  count, and max drawdown are reported in `sprints/v5.5/notes.md`
  alongside the v5 numbers (0.59 / 81% / ~94 / from v5). **Pass = an
  honest, reproduced report**, not a target Sharpe. A delta > 0.1 in
  Sharpe vs v5 triggers a documented root-cause note (expected delta ≈ 0
  if pair-1 canonical resolves to OLS, as anticipated).

## Signal Definition

No change to the underlying RV math (frozen from v3). The only changes
are to **how the Kalman residual is computed** and **which method is
selected**.

### Kalman residual (corrected)

For state `sₜ = [αₜ, βₜ]ᵀ` evolving as a random walk and observation
`yₜ = Hₜ·sₜ + εₜ` with `Hₜ = [1, xₜ]`:

- **Innovation (store this):** `eₜ = yₜ − Hₜ·sₜ|ₜ₋₁`, the prediction
  error using the *prior* (pre-update) state. Past-data-only; this is
  the tradeable deviation.
- **Posterior (do NOT store):** `yₜ − Hₜ·sₜ|ₜ`, measured after `sₜ`
  absorbed `yₜ`; shrunk toward zero (= `eₜ · R/S`), which is what made
  it look 20× "better" and pass ADF trivially.

`βₜ` (the hedge ratio) is unchanged — it is read off the filtered state
as before.

### `rv_xterm` DV01 hedge (corrected)

Current code (`rv_signals.py:218`): `hr3 = dv01_4y / dv01_9y` — a verbatim
copy of pair-1's bond-duration ratio, unrelated to hedging `hy_ig`
against the 2s10s slope. Resolution (decided in task R3): **remove** the
DV01 method for pair 3 and mark it unavailable, unless a defensible
slope-DV01 derivation is cheap. `rv_xterm` then selects among
{OLS, Kalman-innovation} only.

### Tradeability selector

Replaces `select_best_method`. For each pair, over post-warmup data:

1. **Qualify** a method iff `ADF p < 0.05` **AND**
   `OU half-life ∈ [5, 63]` trading days.
2. **Tiebreak** among qualifiers: minimum rolling-63d **coefficient of
   variation** of the hedge ratio (`std/|mean|`) — the most stable,
   least overfit hedge. (Often only one method qualifies, because the
   half-life band alone separates whitened-Kalman from too-slow-DV01;
   the tiebreak binds only when ≥2 qualify.)
3. **No qualifier** → pair marked not tradeable; residual stored as NaN.

Parameters (explicit): `hl_min = 5`, `hl_max = 63`, `adf_alpha = 0.05`,
`cv_window = 63`, `warmup = 252`. The band `[5, 63]` is pre-registered:
5 days is the floor below which a daily-sampled signal has reverted
before it can be acted on; 63 days (one quarter) is the ceiling above
which the "reversion" is too slow to be distinguishable from a
non-stationary drift over a realistic holding period.

## Data

| artifact | source | role this sprint |
|---|---|---|
| `data/processed/features.parquet` | v3 | **regenerated** — stores canonical residual / hedge ratio / z_rv per pair |
| `data/raw/credit_market_data.parquet` | v1 | rates legs; unchanged |
| `data/results/regime_signal_quality.parquet` | v3 | **regenerated** for the C22 restatement |
| `data/benchmarks/random_baseline.parquet` | v1 | sanity baseline for the Strategy-A re-validation |
| `pycredit` | v2 | DV01 recompute; unchanged |

- **Frequency / range:** daily, 2007-04-11 → 2026-04-15. No new ingest.
- **Point-in-time:** all residuals remain trailing-only; the Kalman fix
  *strengthens* this (innovation uses strictly t−1 state). Re-verified
  in R9.
- **Known biases:** unchanged from v5 (ETF-proxy residuals, next-day
  fills, flat borrow). This sprint removes a *selection* bias, not a
  data bias.
- **Backups:** the pre-repair `features.parquet` and
  `regime_signal_quality.parquet` are copied to
  `data/processed/_pre_v5_5/` before regeneration so the errata can show
  before/after.

## Success Metrics

Passing E1–E6 is the bar. Headline table (printed by the v5.5 notebook /
notes):

| metric | target | criterion |
|---|---|---|
| All 3 consumers read identical residual | exact equality | E1 |
| Min selected-residual half-life | ≥ 5 days | E2 |
| Selected-residual ADF p | < 0.05 (or pair = NaN) | E2 |
| Kalman residual std vs old posterior | strictly larger | E3 |
| `rv_xterm` DV01 ≠ `rv_hy_ig` DV01 | not equal (or absent) | E4 |
| C22 restated on tradeable residual | degenerate legs excluded | E5 |
| Strategy A net Sharpe vs v5 (0.59) | reproduced, ∣Δ∣ ≤ 0.1 | E6 |

Reported but not pass/fail: per-pair selected method and the full
qualify/disqualify table (method × {ADF p, half-life, hedge CV,
qualified?}); the Strategy-A equity curve overlaid on the v5 curve.

## Research Architecture

```
signals/
  rv_signals.py     -- EDIT: kalman_hedge → innovation; remove rv_xterm DV01;
                       NEW select_tradeable_method(); NEW canonical_residuals()
  pipeline.py       -- EDIT: enrich_with_rv calls canonical_residuals (drops select_best_method)
backtest/
  ab_test.py        -- EDIT: source residuals from canonical_residuals (no hardcoded method="ols")
dashboard/
  (views reading features.parquet) -- no code change; consumes regenerated parquet
data/processed/
  features.parquet                 -- REGENERATED
  _pre_v5_5/features.parquet       -- NEW backup
data/results/
  regime_signal_quality.parquet    -- REGENERATED (C22 restatement)
tests/
  test_rv_signals.py   -- EDIT: C19/C20/C21 target canonical residual + [5,63] band
  test_canonical.py    -- NEW: E1 equality, E2 half-life floor, E3 Kalman innovation, E4 xterm
  test_ab_test.py      -- EDIT: assert Strategy A consumes canonical residual
notebooks/
  55_foundation_repair.ipynb       -- NEW: before/after story (residual, selector, dashboard, Strategy A)
sprints/v5.5/
  PRD.md, TASKS.md, notes.md, WALKTHROUGH.md, plots/
```

**Data flow (after):**
`canonical_residuals()` → (selected residual, hedge_ratio, z) → written
once to `features.parquet` → read identically by dashboard and by
`ab_test.build_strategy` → v5 `engine.run` (unchanged) → metrics.

The single-source-of-truth function is the whole point: there is exactly
one place where a method is chosen and a residual is computed, and every
consumer imports from it.

## Risks & Biases

- **Strategy A could change.** If pair-1 canonical resolves to something
  other than OLS, Strategy A's P&L shifts. Mitigation: the half-life band
  is expected to leave OLS as the sole qualifier for pair 1; E6 makes any
  shift explicit and root-caused rather than silent.
- **A pair may have no qualifier.** `rv_credit_rates` / `rv_xterm` might
  fail the band. This is allowed and documented (E2), not forced.
- **Test churn.** C19/C21 were written against the old (whitened) stored
  residual. They must be re-pointed at the canonical residual and the new
  band; risk of masking a real failure while "fixing" a test. Mitigation:
  every test edit is reviewed against the criterion it encodes, and the
  new `test_canonical.py` adds independent coverage.
- **Look-ahead regression.** Changing the Kalman update path risks
  introducing a subtle peek. Mitigation: R9 re-runs the v5 leakage check
  (perturb a future bar; confirm no past residual or position changes).
- **Multiple testing.** Unchanged from v5 — C22 is restated, not
  re-discovered; no new hypothesis is mined.

## Out of Scope

- Any Tier-2 work (factors, scenarios, paper trading). v5.5 is a gate
  *before* v6.
- New signals, new pairs, new hedge methods, new data.
- Changing `backtest.engine.run`, `execution.position.run_state_machine`,
  the cost model, or the C++ pricer — all frozen and validated.
- Re-opening the v5 thesis verdict (the equity_first gate stays
  rejected). v5.5 only restates the C22 *half-life statistic*, not the
  tradeability conclusion.
- Tuning thresholds, z-window, or hedge windows.

## Dependencies

**Existing (no version change):** `pandas`, `numpy`, `scipy`,
`statsmodels`, `pyarrow`, `matplotlib`, `pycredit` (v2).

**New:** none.

**Prior sprint outputs:** `sprint-v3` (`features.parquet`,
`regime_signal_quality.parquet`), `sprint-v1`
(`random_baseline.parquet`), `sprint-v2` (`pycredit`). The v5 engine and
state machine are imported unchanged.
