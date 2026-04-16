# Sprint v1 — Tasks Update (patch)

Three atomic update tasks, each 5–15 min. Every ingest/signal task is
paired with a validation task; this patch's "signal" is purely
derivational, so the pairing is (flag construction ↔ flag invariants)
and (stub column reservation ↔ schema + NaN audit).

All three tasks completed 2026-04-17 alongside the PRD update; marked
`[x]` for traceability. See `notes.md` amendment entry for run logs.

---

- [x] **Task U1: Build `signals/flags.py` and extend pipeline**
  - Acceptance: `signals.flags.compute_flags(df, spreads, window=63,
    thresholds)` returns a `bool`-dtype frame with 12 columns
    (`{spread}_{entry_long,entry_short,exit,stop}`) derived from the
    `{spread}_z63` series. Defaults: `entry=2.0, exit=0.5, stop=4.0`.
    NaN z-scores produce `False`, never NaN. Invalid thresholds
    (`exit ≥ entry` or `stop ≤ entry`) raise `ValueError`.
    `signals/pipeline.py` calls `compute_flags(...)` after the z-score
    step; the 12 flag columns appear in the documented per-spread
    order inside `features.parquet`.
  - Files: `signals/flags.py`, `signals/pipeline.py`.
  - Validation: `tests/test_signals.py::test_flag_threshold_semantics`,
    `test_flags_handle_nan_z_score`,
    `test_flag_thresholds_reject_bad_config` all pass. Pipeline run
    logs show flag step adds exactly 12 columns.

- [x] **Task U2: Reserve RV signal stubs**
  - Acceptance: `signals.flags.rv_stubs(index)` returns a float64
    frame with exactly 5 columns —
    `rv_hy_ig_residual`, `rv_credit_rates_residual`,
    `rv_xterm_residual`, `hedge_ratio_hy_ig`, `hedge_ratio_cr` —
    all entirely NaN. Pipeline writes them as the final 5 columns
    of `features.parquet`.
  - Files: `signals/flags.py`, `signals/pipeline.py`.
  - Validation: `tests/test_signals.py::test_rv_stubs_are_all_nan`
    passes. Schema test asserts the five names in exact order.

- [x] **Task U3: Re-validate — schema + NaN audit + firing rates**
  - Acceptance: `features.parquet` has shape `(N, 49)`. Schema test
    (`test_features_schema`) asserts the full 49-column layout and
    enforces dtype discipline (numeric cols float64, flag cols bool).
    `test_features_no_nan_post_warmup` excludes flag + stub columns
    from the NaN check. `test_flags_no_nan_and_bool_dtype` asserts
    flag columns are NaN-free across the **entire** frame (including
    warmup). Full suite is **16/16 green**. Flag firing rates printed
    per column; each must satisfy `0 < fire_rate < 0.25` (C7).
    Notebook re-executed; `PHASE1 STATUS` line reflects C7 + C8.
  - Files: `tests/test_signals.py`,
    `notebooks/01_signal_validation.ipynb`, `sprints/v1/notes.md`
    (append results), `sprints/v1/WALKTHROUGH.md` (append amendment
    section).
  - Validation: fails if any flag fires on 0% or ≥ 25% of rows, if any
    RV stub contains a non-NaN value, if schema drifts from the 49
    ordered columns, or if the notebook errors on re-run.
