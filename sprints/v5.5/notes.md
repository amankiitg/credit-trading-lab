# Sprint v5.5 — Notes (Foundation Repair)

Corrective sprint. Findings, per-task numbers, before/after fingerprints.
See `PRD.md` for E1–E6.

---

## R0 — Backup pre-repair artifacts (2026-06-10)

Byte-identical backups taken before any regeneration:

- `data/processed/_pre_v5_5/features.parquet` — (4784, 56), `cmp` identical
- `data/results/_pre_v5_5/regime_signal_quality.parquet` — (63, 9), `cmp` identical

**"Before" fingerprint** — stored residual std (post-warmup, n=4721):

| residual column | std (before) | character |
|---|---|---|
| `rv_hy_ig_residual` | 0.004630 | Kalman posterior (whitened) |
| `rv_credit_rates_residual` | 0.006793 | Kalman posterior (whitened) |
| `rv_xterm_residual` | 0.005654 | Kalman posterior (whitened) |

Note: **all three** pairs currently store the Kalman *posterior* residual
(the ADF selector picked Kalman for all three, matching the v3 README
"Kalman wins ADF on all 3 pairs"). All three stds are in the whitened
range (~0.005–0.007), i.e. the published `features.parquet` signal is the
overfit residual the v5 backtest deliberately refused to trade (v5 used
OLS for Strategy A). This is the inconsistency v5.5 corrects.

---

## R1 — Kalman residual → one-step-ahead innovation (2026-06-10) ✓ E3

`signals.rv_signals.kalman_hedge` now stores the **innovation**
`e_t = y_t − [1, x_t]·s_{t|t-1}` (prior-state prediction error,
past-data-only) instead of the posterior leftover `y_t − [1, x_t]·s_{t|t}`.
One-line change in the filter loop (`resid[t] = innov`, before the state
update); the β path is untouched.

**Corrected Kalman residual std** (vs old whitened posterior):

| pair | innovation std (after) | posterior std (before) | ratio |
|---|---|---|---|
| `rv_hy_ig` | 0.007668 | 0.004630 | 1.66× |
| `rv_credit_rates` | 0.009669 | 0.006793 | 1.42× |
| `rv_xterm` | 0.008079 | 0.005654 | 1.43× |

The "Kalman variance ~20× smaller than OLS" claim from v3 was largely the
posterior-vs-honest artifact: switching to the tradeable innovation lifts
the std ~1.4–1.7×. (Innovations are near-white by construction, so Kalman
is still expected to fail the v5.5 [5,63]-day half-life band in R2 — which
is the correct outcome, not a tunable.)

**Validation:**
- `tests/test_canonical.py::test_e3_kalman_is_innovation` — independent
  reconstruction matches `kalman_hedge` to atol 1e-12. PASS.
- `tests/test_canonical.py::test_e3_kalman_std_exceeds_posterior` — std
  0.007668 > 0.00463. PASS.
- Kalman **β** byte-identical before/after (max |Δ| = 0.00e+00 vs git
  HEAD) → C23 hedge-ratio test provably unaffected.
- `test_rv_signals.py` + `test_regime_quality.py`: 11 passed, 1 failed =
  **C23 only**, the pre-registered honest failure documented in
  `sprints/v3/notes.md:216` ("✗ FAIL 4 of 9 combos"). No regression.

---

## R2 — Tradeability selector `select_tradeable_method` (2026-06-10) ✓ E2

New selector in `signals/rv_signals.py`. Qualify iff ADF p < 0.05 **AND**
OU half-life ∈ [5, 63] days; tiebreak = min median rolling-63d hedge-ratio
CV; returns `None` if nothing qualifies. `select_best_method` kept but
marked DEPRECATED (whitening detector).

**On real data, all three pairs select OLS** — the exact method the v5
backtest already used for Strategy A, so pipeline and backtest now agree:

| pair | OLS (chosen) | Kalman | DV01 |
|---|---|---|---|
| `rv_hy_ig` | adf 3.6e-7, HL **18.0d**, cv 0.15 ✓ | adf 5.9e-23, HL 1.48d ✗ | adf 0.69, HL 455d ✗ |
| `rv_credit_rates` | adf 1.2e-14, HL **25.8d**, cv 0.23 ✓ | adf 2.2e-23, HL 2.23d ✗ | adf 0.78, HL 624d ✗ |
| `rv_xterm` | adf 5.1e-7, HL **19.0d**, cv 0.26 ✓ | adf 4.8e-21, HL 1.87d ✗ | adf 0.61, HL 250d ✗ |

**The bug, in one table:** Kalman has the *lowest* ADF p-value in every
row (most "stationary", ~1e-21) so the old ADF selector picked it — yet
its 1.5–2.2 day half-life is whitened noise. The [5,63] band rejects it
correctly. DV01 is rejected for the opposite reason (HL 250–624 days and
not even stationary, ADF p 0.6–0.8). Only OLS qualifies per pair, so the
hedge-CV tiebreak never binds on real data (as the PRD anticipated; it is
unit-tested separately).

**Validation:** `tests/test_canonical.py` — 5 passed:
- `test_e2_selector_picks_only_in_band` — whitened (HL≈1, stationary) is
  rejected by the floor; in-band (HL≈12) chosen; slow (HL≈300) rejected.
- `test_e2_selector_tiebreak_prefers_stable_hedge` — two qualifiers, lower
  hedge-CV wins.
- `test_e2_selector_returns_none_when_nothing_qualifies` — all-whitened
  pair → `None` (not force-filled).

---

## R3 — `canonical_residuals()` + remove rv_xterm DV01 (2026-06-10) ✓ E4

**rv_xterm DV01 removed.** `dv01_hedge` no longer computes/returns the
`hr3 = dv01_4y/dv01_9y` copy of pair-1's ratio; `build_all_residuals`
iterates `for name in dv` so rv_xterm exposes only `{ols, kalman}`. The
real DV01 pairs (rv_hy_ig, rv_credit_rates) keep it.

**`canonical_residuals()`** — the single place a method is chosen and a
residual computed. Calls `build_all_residuals` once + `select_tradeable_method`
once, returns per pair `{method, residual, hedge_ratio, z, diagnostics}`;
`method=None` + all-NaN for a not-tradeable pair. Output:

| pair | chosen | resid std (canonical/OLS) | available methods |
|---|---|---|---|
| `rv_hy_ig` | ols | 0.02305 | ols, kalman, dv01 |
| `rv_credit_rates` | ols | 0.03162 | ols, kalman, dv01 |
| `rv_xterm` | ols | 0.02402 | ols, kalman (no dv01) |

Canonical residual stds are ~5× the old whitened posteriors
(0.0046/0.0068/0.0057) — these are the real, tradeable residuals.

**Validation:** `tests/test_canonical.py` — 8 passed total:
- `test_e4_xterm_has_no_dv01` — rv_xterm absent from `dv01_hedge`; no
  `dv01` key under rv_xterm in `build_all_residuals`; real DV01 pairs keep it.
- `test_e4_no_copy_of_pair1_dv01` — guard against the copy-paste returning.
- `test_canonical_selects_one_method_per_pair` — canonical residual/hedge
  are bit-equal to `results[pair][chosen]` (single computation path).
- Regression: `test_rv_signals` + `test_regime_quality` + `test_ab_test`
  = 18 passed, 1 failed = **C23 only** (still exactly the documented 4
  combos; rv_xterm/dv01 correctly drops out). No new regression.

---

## R4 — Rewire pipeline + regenerate features.parquet (2026-06-10) ✓ E2

`enrich_with_rv` now sources from `canonical_residuals` (dropped
`select_best_method`). Regenerated `features.parquet` surgically (loaded
current frame, re-ran enrichment, wrote back). Selector log:

```
rv_hy_ig:        canonical=ols  adf_p=3.56e-07  half_life=18.0d  hedge_cv=0.152
rv_credit_rates: canonical=ols  adf_p=1.19e-14  half_life=25.8d  hedge_cv=0.226
rv_xterm:        canonical=ols  adf_p=5.05e-07  half_life=19.0d  hedge_cv=0.260
```

**Surgical diff vs `_pre_v5_5` backup:** schema identical (4784, 56),
index monotonic+unique. Exactly **8 columns changed**, 48 byte-stable:

| changed (8) | unchanged (48) |
|---|---|
| `rv_{hy_ig,credit_rates,xterm}_residual` | all regimes (`vol/equity/equity_credit_lag`) |
| `hedge_ratio_{hy_ig,cr}` | spreads, returns, z-scores, flags, buyhold |
| `z_rv_{hy_ig,credit_rates,xterm}` | — |

**E2 enforced on disk** — stored residual (post-warmup, std / half-life):

| residual | std (before→after) | half-life | |z|>2 days |
|---|---|---|---|
| `rv_hy_ig_residual` | 0.00463 → **0.02314** | 18.0d | 465 |
| `rv_credit_rates_residual` | 0.00679 → **0.03184** | 25.8d | 535 |
| `rv_xterm_residual` | 0.00565 → **0.02415** | 19.0d | 483 |

All half-lives ≥ 5 (E2 ✓), ADF p < 0.05. The published signal is now the
real OLS residual the backtest trades — the whitened Kalman residual is
gone from disk.

**Benign change:** leading-NaN warmup went 63 → 125 (OLS 126d window vs
Kalman 63d init). Inside the backtest warmup (378) and the test slice
`iloc[252:]`; no downstream impact. The dashboard reads these columns
directly, so its Today View / conviction tiers now reflect the OLS signal
automatically (validated as identical to the backtest in R5).

---

## R5 — Route backtest through canonical source + E1 equality (2026-06-10) ✓ E1

`backtest/ab_test.py`: added `_resolve_method(residuals, pair, method)` —
`method=None` resolves to the tradeability-selected method via
`select_tradeable_method` (raises if the pair is not tradeable). Changed
the four headline entry points (`compare`, `walk_forward`,
`parameter_grid`, `subperiod_split`) from hardcoded `method="ols"` to
`method=None`. The robustness panel (`hedge_method_panel`) still passes
explicit ols/kalman/dv01. `load_inputs` unchanged (2-tuple) — no caller
breakage; the generator's `compare(features, residuals)` now resolves
canonical automatically.

**Headline reproduces v5 exactly** (method resolved to `ols`):

| metric | v5 | v5.5 (canonical) |
|---|---|---|
| Strategy A net Sharpe | 0.59 | **0.591** |
| Strategy A hit rate | 81% | **80.9%** |
| Strategy A trades | ~94 | **94** |
| ΔS (B−A) | −0.41 | **−0.411** |
| ΔS 95% CI | [−0.82, −0.01] | **[−0.815, −0.009]** |

The repair left the validated numbers bit-for-bit intact — it only made
the residual selection honest and consistent. (Effectively an early E6
pass; R7 formalizes with the equity-curve overlay + random baseline.)

**Validation:** `tests/test_canonical.py` — 10 passed:
- `test_e1_all_consumers_read_identical_residual` — parquet column,
  `dashboard.loader.load_features()`, and the backtest's fed residual are
  **bit-identical** (`check_exact=True`) for all 3 pairs.
- `test_e1_backtest_method_is_canonical_not_hardcoded` — `_resolve_method`
  output == `canonical_residuals` selection per pair (no hardcoded coincidence).
- Regression: `test_ab_test` + `test_rv_signals` + `test_regime_quality`
  = 18 passed, 1 failed = **C23 only**. No new regression.

---

## R6 — Re-point C19/C20/C21 at canonical residual + [5,63] band (2026-06-10)

`tests/test_rv_signals.py`:
- **C19** (stationarity) — now documented as the *canonical* residual
  (E1-identical to the stored column); skips with reason if a pair is
  not tradeable (all-NaN). PASS ×3.
- **C20** (cointegration) — logic unchanged (min ADF across methods <
  0.05) but documented that existence of a stationary residual ≠
  tradeability (Kalman innovation is most stationary yet whitened). PASS ×3.
- **C21** (half-life) — band tightened **[1, 126] → [5, 63]**, renamed
  `test_c21_halflife_in_tradeable_band`. PASS ×3 (18.0 / 25.8 / 19.0 d).

**The proof the tightening works** — the OLD whitened residuals now fail
the band they used to pass:

| old stored residual | half-life | old [1,126] | new [5,63] |
|---|---|---|---|
| `rv_hy_ig_residual` | 1.50d | pass | **REJECT** |
| `rv_credit_rates_residual` | 2.23d | pass | **REJECT** |
| `rv_xterm_residual` | 1.87d | pass | **REJECT** |

The criterion now catches the whitening it was blind to. C19/C20/C21 =
9 passed.

---

## R7 — Strategy A re-validated through unchanged v5 engine (2026-06-10) ✓ E6

Ran the full A/B (`compare`, `method=None` → canonical OLS) through the
**unmodified** engine / state machine / cost model.

| metric | v5 | v5.5 | Δ |
|---|---|---|---|
| net Sharpe (annualized) | 0.59 | **0.591** | +0.001 |
| hit rate | 81% | **80.9%** | — |
| trades | ~94 | **94** | — |
| max drawdown | — | **−$152,025** | — |
| total net P&L | — | **$760,372** | — |
| avg holding / turnover | — | 15.7d / 5.0 per yr | — |

**E6 gate (|ΔSharpe| ≤ 0.1): PASS** with Δ = +0.001 — no root-cause note
needed. Equity curve is **bit-identical** to the pre-repair recompute
(max diff $0.000000): the repair did not perturb Strategy A, because
Strategy A always traded the OLS residual, which R1–R6 never touched.

**C29 sanity (per-trade Sharpe basis, vs Sprint-1 random-entry baseline):**
Strategy A trade_sharpe **5.00** vs random p95 **1.70** → excess **+3.30**,
beats random. (Per-trade t-stat basis, not annualized — the 0.591 above is
the tradeable Sharpe; nothing too-good-to-be-true, and it reproduces v5.)

Plot: `sprints/v5.5/plots/strategy_a_revalidated.png` — v5.5 and v5
curves coincide exactly. Engine/state-machine/costs unchanged (frozen list
respected).

---

## R8 — Restate C22 on the canonical residual (2026-06-10) ✓ E5

Regenerated `data/results/regime_signal_quality.parquet`: **56 rows**
(was 63 — the 7 `rv_xterm/dv01` rows are gone). C24 schema test passes.

**RV1 half-life, equity_first vs neither, by method:**

| method | equity_first | neither | % shorter | verdict |
|---|---|---|---|---|
| **ols (canonical)** | **5.3d** | **16.2d** | **67%** | ✓ valid, both in [5,63] |
| kalman | 0.85d | 1.46d | 41.8% | ✗ whitened (noise vs noise) |
| dv01 | 61.6d | 374.8d | 83.6% | ✗ too-slow / non-stationary |

**Restated C22 (canonical OLS only): equity_first 5.3d is 67% shorter
than neither 16.2d → PASS** (threshold >20%). The statistical
equity-credit-lag effect is **real on the tradeable residual** — which is
fully consistent with the v5 verdict that it isn't monetizable as a
*gate* (a real per-trade quality effect, but gating starves trade count;
see signal_freq below).

**RETIRED — the old "43–67% shorter across methods" framing.** The "43%"
end was the **whitened Kalman** residual (0.85 vs 1.46 days = comparing
noise to noise), and the "83%" DV01 leg is non-stationary/too-slow.
"Across methods" implied robustness that did not exist — two of three
legs were degenerate. The honest claim is **67%, OLS only**. (Propagation
of this errata to README + `sprints/v3/notes.md` is R10.)

**signal_freq, restated honestly** (fraction of days |z|>1.5, OLS): the
contaminated Kalman read was a flat ~12% (noise tail). On the canonical
OLS residual it's **equity_first 28% vs neither 23%** — a modest
opportunity-rate edge, but `credit_first` is actually richest (31%) and
equity_first is only 7.8% of days. This is exactly why the v5 gate failed.

Plot: `sprints/v5.5/plots/c22_restated.png` — log-scale half-lives with
the [5,63] band shaded; OLS valid, Kalman/DV01 greyed as not-tradeable.
Validation: `test_regime_quality` = 2 passed, 1 failed = **C23 only**.

---

## R9 — Leakage / trailing-only re-check (2026-06-10)

Added `tests/test_canonical.py` leakage probes: shock a single **future**
bar (index 3000, +10.0) of `hy_spread` or `ig_spread`, recompute the
residual, assert **every residual strictly before that bar is
bit-identical** (`check_exact=True`). Parametrized over {OLS, Kalman} ×
{hy_spread, ig_spread} = 4 cases, all PASS. A non-vacuous guard confirms
the shock *did* move t≥T0.

This proves the corrected **Kalman innovation is trailing-only** — the
innovation at t is invariant to data at t+k for all k≥1 — closing the one
loose end from R1 (the old posterior residual was self-referential within
a timestep but never future-leaking; the innovation fix keeps it that way).
`test_r9_fill_lag_preserved` asserts `FILL_LAG == 1` (no same-bar fill).

**Full suite: 202 passed, 3 failed.** The 3 failures are *exactly* the
pre-registered honest failures carried since v3:
- `test_c18_non_degenerate[equity_regime]` — top label 0.851 > 0.70
- `test_c18_non_degenerate[equity_credit_lag]` — `neither` dominates (85%)
- `test_c23_hedge_ratio_cv_under_one` — OLS/Kalman β CV

No new regression; suite grew 190 → 205 (the +15 canonical tests), never
shrank.

---

## R8.5 — Before/after notebook (2026-06-10)

`notebooks/06_foundation_repair.ipynb` (generator
`scripts/build_notebook_v5_5.py`) — executes clean via nbconvert (exit 0,
5/5 code cells, 0 errors). Four sections, each with a saved plot:

1. **residual_before_after.png** — whitened Kalman (jittery noise on 0)
   vs OLS (persistent mean-reverting structure); std 0.0046 → 0.0231
   (5.1×), half-life 1.5d → 18.0d.
2. **selector_band.png** — half-life by method with [5,63] band; ADF p
   annotated (Kalman lowest ~1e-21 yet whitened; OLS the only in-band pick).
3. **dashboard_signal_before_after.png** — z_rv before/after + HIGH-
   conviction signal-days **45 (whitened) → 91 (OLS)** on a consistent
   `equity_first ∧ |z|>2` definition (the v4 "178" used a looser tier
   count — context, not a match target).
4. **strategy_a_equity.png** — net equity curve; Sharpe 0.591 = v5.

Reads "before" from `_pre_v5_5/` backups, "after" from regenerated
artifacts.

---

## R10 — Errata docs + sprint close (2026-06-10)

- `sprints/v5.5/WALKTHROUGH.md` written (defect, fixes, E1–E6, before/after,
  restated C22, limitations, reproducibility).
- `README.md`: added a **Sprint 5.5 — Foundation Repair** section with the
  E1–E6 table; corrected the bottom-line "~43% faster" → "~67% (OLS)";
  flagged the stale V6 "Kalman wins ADF" and V8 "43–67% across methods"
  lines with ⚠ Sprint-5.5 errata pointers; fixed the C22 row in the Sprint-3
  falsification table.
- `sprints/v3/notes.md`: appended a ⚠ Sprint-5.5 errata block flagging the
  ADF-selector defect, the whitened-Kalman C22 leg, and the `rv_xterm` DV01
  copy.

### Sprint v5.5 — close

**All 12 tasks (R0–R10 + R8.5) complete. E1–E6 all PASS.** Strategy A
unchanged (net Sharpe 0.591, bit-identical to v5). Full suite: 202 passed,
3 failed = the pre-registered honest failures (C18 ×2, C23 ×1); suite grew
190 → 205, never shrank.

| criterion | result |
|---|---|
| E1 — one residual, bit-identical across consumers | ✓ |
| E2 — no whitened residual survives (HL ≥ 5d) | ✓ |
| E3 — Kalman = one-step innovation | ✓ |
| E4 — `rv_xterm` DV01 removed | ✓ |
| E5 — C22 restated (67% OLS only) | ✓ |
| E6 — Strategy A re-validated (0.591, Δ +0.001) | ✓ |

Foundation is honest and consistent; Tier 2 (v6) can proceed.
