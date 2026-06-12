# Sprint v5.5 — Tasks

Twelve atomic tasks (R0–R10 + R8.5), each 10–20 minutes of focused work.
A **corrective** sprint: every fix is paired with a test that encodes the
criterion it satisfies, plus a leakage re-check (R9), a sanity baseline
(R7), and a before/after notebook (R8.5). Status starts at `[ ]`; flip to
`[x]` as each lands.

Pre-req: Tier-1 built; `features.parquet` (4784 × 56),
`regime_signal_quality.parquet`, `random_baseline.parquet`, and
`pycredit` present. See `PRD.md` §Falsification for E1–E6.

**Ordering is dependency-driven:** R1→R2→R3 build the new core; R4/R5
rewire consumers; R6 re-points tests; R7/R8 re-validate; R8.5 renders the
before/after notebook; R9 guards leakage; R10 documents.

---

- [x] **Task R0: Backup pre-repair artifacts**
  - Acceptance: `data/processed/_pre_v5_5/features.parquet` and
    `data/results/_pre_v5_5/regime_signal_quality.parquet` exist as
    byte-copies of the current files. A one-line manifest in
    `sprints/v5.5/notes.md` records their shapes and the
    `rv_hy_ig_residual` std (0.00463) as the "before" fingerprint.
  - Files: `data/processed/_pre_v5_5/`, `data/results/_pre_v5_5/`,
    `sprints/v5.5/notes.md`.
  - Validation: fails if the backup differs from the live file; fails
    if regeneration happens before the backup exists.

- [x] **Task R1: Kalman residual → one-step-ahead innovation**
  - Acceptance: `signals.rv_signals.kalman_hedge` returns the
    **prior/innovation** residual `eₜ = yₜ − Hₜ·sₜ|ₜ₋₁` (the value
    already computed as `innov` before the state update), not the
    posterior `yₜ − Hₜ·sₜ|ₜ`. `βₜ` return is unchanged. A unit test in
    `tests/test_canonical.py` reconstructs the innovation independently
    and asserts equality at a handful of dates, and asserts the
    full-sample `rv_hy_ig` Kalman residual std > 0.00463 (the old
    posterior std). **(E3)**
  - Files: `signals/rv_signals.py`, `tests/test_canonical.py`.
  - Validation: fails if the stored residual still matches the posterior
    (std ≈ 0.0046); fails if the innovation uses `xₜ`'s updated state.

- [x] **Task R2: Tradeability selector `select_tradeable_method`**
  - Acceptance: new `select_tradeable_method(results, warmup=252,
    hl_min=5, hl_max=63, adf_alpha=0.05, cv_window=63)` in
    `signals/rv_signals.py` returns, per pair, `(chosen_method | None,
    diagnostics)` where `diagnostics[method] = {adf_p, half_life,
    hedge_cv, qualified}`. A method qualifies iff `adf_p < adf_alpha`
    AND `hl_min ≤ half_life ≤ hl_max`; tiebreak = min `hedge_cv`;
    `None` if no qualifier. Unit test on a synthetic set (one whitened
    series HL≈1, one in-band HL≈12, one slow HL≈300) confirms only the
    in-band series is chosen. **(E2)**
  - Files: `signals/rv_signals.py`, `tests/test_canonical.py`.
  - Validation: fails if a sub-5-day half-life method is ever selected;
    fails if `None` is not returned when nothing qualifies; fails if the
    tiebreak ignores hedge CV when ≥2 qualify.

- [x] **Task R3: `canonical_residuals()` + fix `rv_xterm` DV01**
  - Acceptance: (a) `rv_xterm`'s DV01 hedge is **removed** (method
    marked unavailable for pair 3) — `dv01_hedge` no longer returns the
    copy-paste `dv01_4y/dv01_9y` for `rv_xterm`; a test asserts the
    `rv_xterm` DV01 ratio is absent or not element-wise equal to
    `rv_hy_ig`'s. (b) new `canonical_residuals(features, cmd, pycredit)`
    builds all methods, runs `select_tradeable_method`, and returns per
    pair `{method, residual, hedge_ratio, z}` — the **single source of
    truth**. **(E4)**
  - Files: `signals/rv_signals.py`, `tests/test_canonical.py`.
  - Validation: fails if `rv_xterm` DV01 still equals `rv_hy_ig` DV01;
    fails if `canonical_residuals` recomputes anything two different ways.

- [x] **Task R4: Rewire pipeline + regenerate `features.parquet`**
  - Acceptance: `signals.pipeline.enrich_with_rv` sources residuals,
    hedge ratios, and `z_rv` from `canonical_residuals` (deletes the
    `select_best_method` call). Regenerated `features.parquet`: each
    stored residual has half-life ≥ 5 and ADF p < 0.05, or is NaN with
    the pair listed not-tradeable in notes. The per-pair selected method
    is printed and recorded. **(E2)**
  - Files: `signals/pipeline.py`, `data/processed/features.parquet`,
    `sprints/v5.5/notes.md`.
  - Validation: fails if any stored non-NaN residual has half-life < 5;
    fails if column schema changes break downstream readers; fails if a
    disqualified method is back-filled.

- [x] **Task R5: Route backtest + dashboard through the canonical source**
  - Acceptance: `backtest.ab_test.build_strategy` consumes the canonical
    residual for the pair (no hardcoded `method="ols"`; it uses whatever
    `canonical_residuals` selected). A test in `tests/test_canonical.py`
    loads the residual three ways — from `features.parquet`, from the
    dashboard's accessor, and from `ab_test` — and asserts all three are
    bit-identical per pair via `assert_series_equal`. **(E1)**
  - Files: `backtest/ab_test.py`, `tests/test_canonical.py`,
    (`dashboard/` accessor if one is needed).
  - Validation: fails if any two consumers differ beyond float
    round-trip; fails if `ab_test` still pins a method independent of
    selection.

- [x] **Task R6: Re-point C19/C20/C21 tests at the canonical residual**
  - Acceptance: `tests/test_rv_signals.py` C19 (stationarity) and C21
    (half-life) test the **canonical** residual and the `[5, 63]` band
    (not the old `[1, 126]`); C20 keeps its "≥1 method stationary"
    intent but is documented to no longer imply tradeability. Tests pass
    for tradeable pairs and `xfail`/skip with a reason for any
    not-tradeable pair.
  - Files: `tests/test_rv_signals.py`.
  - Validation: fails if a test passes on a whitened residual; fails if a
    not-tradeable pair is asserted tradeable; fails if the band was
    loosened just to make a test green.

- [x] **Task R7: Re-validate Strategy A + sanity baseline**
  - Acceptance: re-run the v5 A/B through the **unchanged** engine on the
    unified residual. Record Strategy A net Sharpe, hit rate, trade
    count, max DD in `sprints/v5.5/notes.md` next to the v5 numbers
    (0.59 / 81% / ~94). Re-confirm the C29 sanity baseline: Strategy A
    net Sharpe vs the random-entry p95 from `random_baseline.parquet`.
    Overlay the new vs old Strategy-A equity curve →
    `sprints/v5.5/plots/strategy_a_revalidated.png`. **(E6)**
  - Files: `sprints/v5.5/notes.md`, `sprints/v5.5/plots/`.
  - Validation: fails if ∣ΔSharpe∣ > 0.1 vs v5 without a root-cause note;
    fails if the random baseline is not re-run; fails if the engine,
    state machine, or costs were modified to hit a number.

- [x] **Task R8: Restate C22 on the tradeable residual**
  - Acceptance: regenerate `regime_signal_quality.parquet` and recompute
    the `equity_first` vs `neither` half-life comparison on the
    **canonical** residual only. Degenerate legs (half-life < 5 or
    non-stationary) are excluded from any "shorter half-life" claim.
    `sprints/v5.5/notes.md` states the restated number, retires the old
    "43–67% across methods" framing, and a plot
    `sprints/v5.5/plots/c22_restated.png` shows per-method half-lives
    with the whitened/slow legs greyed out. **(E5)**
  - Files: `data/results/regime_signal_quality.parquet`,
    `sprints/v5.5/notes.md`, `sprints/v5.5/plots/`.
  - Validation: fails if the restated claim still leans on a sub-5-day
    half-life leg; fails if the old framing is left unretired in any doc.

- [x] **Task R8.5: Before/after notebook `55_foundation_repair.ipynb`**
  - Acceptance: a runnable notebook that tells the repair story end to
    end, reading the `_pre_v5_5/` backups for "before" and the
    regenerated artifacts for "after". Four sections, each with a saved
    plot to `sprints/v5.5/plots/`: (1) **residual** — old whitened-Kalman
    vs new OLS residual for `rv_hy_ig`, with the std jump 0.0046 → 0.0231
    annotated; (2) **selector** — the per-pair table {method × adf_p,
    half_life, hedge_cv, qualified} showing ADF would pick Kalman while
    the half-life band picks OLS; (3) **dashboard signal** — before/after
    `z_rv` series and the HIGH-conviction count vs the v4 baseline (178);
    (4) **Strategy A** — old vs new equity curve + headline metrics from
    R7. Notebook executes top-to-bottom without error via `nbconvert`.
  - Files: `notebooks/55_foundation_repair.ipynb`,
    `sprints/v5.5/plots/*.png`.
  - Validation: fails if any cell errors on execute; fails if a plot is
    missing an axis label, units, or the date range/universe in the
    title; fails if "before" is recomputed live instead of read from the
    `_pre_v5_5/` backup (the backup is the ground-truth "before").

- [x] **Task R9: Leakage / trailing-only re-check**
  - Acceptance: re-run the v5-style leakage probe on the corrected
    pipeline — perturb a single **future** bar of the input spreads and
    confirm no canonical residual value at any **earlier** date changes
    (and hence no earlier position changes). Assert the Kalman innovation
    at t is invariant to data at t+k for all k ≥ 1. Result logged in
    notes; test added to `tests/test_canonical.py`.
  - Files: `tests/test_canonical.py`, `sprints/v5.5/notes.md`.
  - Validation: fails if any past residual moves when a future bar is
    perturbed; fails if `fill_lag=1` is not preserved end-to-end.

- [x] **Task R10: Errata docs + sprint close**
  - Acceptance: `sprints/v5.5/notes.md` finalized (findings, per-pair
    selected method, before/after fingerprints, restated C22, Strategy-A
    re-validation); `sprints/v5.5/WALKTHROUGH.md` written; `README.md`
    Sprint-3/Sprint-5 sections get an errata note pointing to v5.5 and
    the corrected canonical-residual story; `sprints/v3/notes.md` flags
    the ADF-selector defect. Full suite green (or documented xfails).
  - Files: `sprints/v5.5/notes.md`, `sprints/v5.5/WALKTHROUGH.md`,
    `README.md`, `sprints/v3/notes.md`.
  - Validation: fails if any doc still presents the whitened Kalman
    residual as the published signal; fails if the test count shrank;
    fails if E1–E6 are not each evaluated with a stored number.
