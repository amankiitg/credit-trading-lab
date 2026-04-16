# Sprint v1 — PRD Update (patch)

Patch to the v1 PRD. Phase 1 was closed with features.parquet at shape
`(4784, 32)` (see `PRD.md`, `WALKTHROUGH.md`, commit `sprint-v1`). This
document pre-registers a **schema-only amendment** so the downstream
consumers (Phase 3 RV book, dashboard, backtest) can depend on a
stable column set before those phases exist. No trading logic is
introduced; no statistical claim in the original PRD is revised.

## Overview

Extend `data/processed/features.parquet` with two column groups:

1. **Signal-state flags** — 12 boolean columns derived from the
   canonical 63-day z-score (`{spread}_z63`) for each of the three
   spreads. These are stateless threshold tripwires, not trades.
2. **RV signal stubs** — 5 float64 columns reserved for Phase 3
   relative-value signals and hedge ratios. All-NaN in v1.

Final schema: **49 columns** (was 32).

## Economic Hypothesis

Unchanged. Flags and stubs do not introduce a new hypothesis — they
operationalize the existing z-scores into booleans the strategy layer
can consume, and reserve column names for a later phase.

## Falsification Criteria (additions)

Pre-registered before re-running the pipeline:

- **C7** — every flag column must be `bool` dtype, NaN-free across
  the entire frame (including warmup), and fire on strictly between
  0% and 25% of all rows. Firing on 0% indicates a threshold that
  never triggers; firing on >25% indicates a threshold too loose to
  encode "extreme."
- **C8** — every RV stub column must be present in the schema, be
  `float64`, and be entirely NaN in v1. A non-NaN stub value is
  evidence of a Phase 3 leak into Phase 1.

Existing C1–C6 are unchanged in spirit; C1's NaN audit explicitly
**excludes** flag and RV-stub columns so the amendment does not
trivially fail a criterion written before flags existed.

## Signal Definition

### Signal-state flags (new)

For each spread `s ∈ {hy_spread, ig_spread, hy_ig}` and its 63-day
z-score `z = {s}_z63`:

- `{s}_entry_long   = z < -entry`  (mean-reversion long setup)
- `{s}_entry_short  = z > +entry`
- `{s}_exit         = |z| < exit`  (position-closing zone)
- `{s}_stop         = |z| > stop`  (risk-control zone)

Thresholds (defaults, enforced at call time):
- `entry = 2.0`, `exit = 0.5`, `stop = 4.0`
- Must satisfy `exit < entry < stop`; violation raises `ValueError`.

Flags are stateless — no position memory. NaN z-scores (warmup rows)
produce `False`, never NaN.

### RV signal stubs (new)

Five float64 columns, all-NaN in v1:

| column | Phase 3 target |
|---|---|
| `rv_hy_ig_residual` | residual of HY-vs-IG regression |
| `rv_credit_rates_residual` | residual of credit-vs-rates regression |
| `rv_xterm_residual` | cross-term residual (term structure) |
| `hedge_ratio_hy_ig` | HY/IG pair hedge ratio |
| `hedge_ratio_cr` | credit/rates pair hedge ratio |

## Data

Unchanged — no new ingest. Flags are derived from existing z-scores.

## Success Metrics (additions)

- Flag firing rate per column ∈ (0%, 25%).
- Flags are NaN-free across the full frame; bool dtype.
- RV stubs 100% NaN; float64 dtype.
- Full test suite green (target 16/16 after extensions).
- `features.parquet` shape = `(N, 49)`.

## Research Architecture

New module `signals/flags.py` — pure; same dataframe-in, dataframe-out
contract as the existing `signals/features.py` and `signals/zscore.py`.
`signals/pipeline.py` gains two steps (flags, stubs) after z-scores.
No new I/O boundary; `features.parquet` remains the sole processed
output.

## Risks & Biases

- Hardcoded thresholds (2.0 / 0.5 / 4.0) were **not** chosen by
  looking at v1 z-score distributions. They are defaults from the
  PRD's amendment; a separate threshold-tuning sprint would be
  needed before relying on firing rates for sizing.
- Stubs introduce a contract debt — Phase 3 is committed to
  populating exactly these column names with the documented
  semantics. Renaming later would break the schema.

## Out of Scope

- Populating RV stubs (Phase 3).
- Threshold tuning or per-spread threshold dispersion (later sprint).
- Combining flags into a position or trade (strategy layer, v2+).
- Threshold sensitivity study.

## Dependencies

No new external libraries. Uses `pandas`, `numpy` already pinned in
`requirements.txt`.
