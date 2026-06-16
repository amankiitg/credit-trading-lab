# Sprint v8.1 — Tasks

**Sprint closed. All tasks done. Two gate definitions (E2, E5) were corrected
during implementation after running against real data — see notes.md for the
exact corrections and why the original PRD wording was wrong.**

Status: `[ ]` = not done, `[x]` = done.

**Dependency order:** T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8.
No hard stop-and-close gate in this sprint (unlike v6.6/v7.1) — E1–E5 are
correctness bugs to fix, not research dead-ends. If any fails, fix the code and
re-run the same task; do not skip downstream tasks.

---

- [x] **Task T1: Universe ingest**
  - Extend `signals/load.py` (or call `fetch`/`write_raw` directly with an
    explicit ticker list) to pull `TLT`, `EFA`, `EEM`, `GLD` alongside the
    existing `SPY`, `HYG`, `LQD`, `IEF`. Use the existing yfinance boundary —
    do not add a second ingest path.
  - Acceptance: `data/raw/{TLT,EFA,EEM,GLD}.parquet` written with the same
    schema as the existing tickers (`open, high, low, close, adj_close,
    volume`); row count and date range printed for all 8 tickers.
  - Files: `data/raw/TLT.parquet`, `data/raw/EFA.parquet`, `data/raw/EEM.parquet`, `data/raw/GLD.parquet`
  - Validation: fails if any ticker is missing `adj_close`; fails if a new
    ingest path is added instead of reusing `signals.load.fetch`.

- [x] **Task T2: Trend signal construction**
  - Implement `trail_ret_i(t)` and `signal_i(t)` exactly as specified in the
    PRD (`L = 120` trading days, sign-only, long-only/flat: 1 or 0). Vectorized
    pandas, no per-name loop beyond iterating the 8-ticker universe.
  - Acceptance: signal frame (date x ticker) printed with shape, NaN count
    before warmup, and fraction of days each ticker is long (sanity: should be
    roughly 40-60%, not 0% or 100%, for each ticker over its full history).
  - Files: `signals/trend.py` (or equivalent module path)
  - Validation: fails if `L` is anything other than 120; fails if the signal
    uses `close` instead of `adj_close`; fails if any per-name threshold other
    than `> 0` is introduced.

- [x] **Task T3: Vol targeting + leverage cap -> target position vector**
  - Implement `sigma_i(t)` (63d trailing annualized realized vol),
    `raw_weight_i(t) = signal_i(t) * min(v/sigma_i(t), w_max)` with `v=0.10`,
    `w_max=0.50`, then the gross-leverage scaling with `G_max=2.0`, exactly as
    specified in the PRD.
  - Acceptance: `target_position_vector` frame (date x ticker, weights) saved;
    summary stats printed (mean gross exposure, mean net exposure, max gross
    exposure observed).
  - Files: `data/processed/v8_1_target_positions.parquet`
  - Validation: fails if any parameter (`v`, `w_max`, `G_max`, `W=63`) differs
    from the PRD; fails if the gross cap is applied before per-name capping
    instead of after.

- [x] **Task T4: No-look-ahead test (E1) + reproducibility test (E4)**
  - E1: take the target-position pipeline, perturb `adj_close` for every
    ticker at every date after some cutoff date `t0`, recompute, and assert
    the position vector for all dates `<= t0` is bit-for-bit unchanged.
  - E4: run the full pipeline twice on identical inputs and assert the two
    output frames are bit-for-bit identical (`pandas.testing.assert_frame_equal`,
    no tolerance).
  - Acceptance: both tests pass and are part of the pytest suite (not a
    one-off script).
  - Files: `tests/test_trend_signal.py`
  - Validation: fails if the perturbation test perturbs dates `<= t0` (that
    would not test look-ahead); fails if reproducibility is only checked
    visually instead of with an exact frame-equality assertion.

- [x] **Task T5: Point-in-time universe membership check (E5)**
  - For each ticker, assert its first non-NaN entry in `signal_i` is no
    earlier than `L + W` trading days after that ticker's first available
    `adj_close` date (i.e. no value is ever produced before there is enough
    real history to compute it — no synthetic backfill, no shorter warmup for
    convenience).
  - Acceptance: a table of (ticker, first adj_close date, first valid signal
    date, expected minimum gap) printed with 0 violations.
  - Files: `tests/test_trend_signal.py` (same module as T4, separate test function)
  - Validation: fails if any ticker's first valid signal date is earlier than
    its data warmup allows.

- [x] **Task T6: Vol-target tracking (E2) + leverage-cap (E3) checks**
  - E2: for each ticker, compute the realized annualized vol over all days it
    was active (signal=1) and confirm it falls within [5%, 20%] on average
    (2x band around the v=10% target) across the full sample.
  - E3: assert `sum(abs(target_position_vector), axis=1) <= 2.0` on every row,
    100% of days.
  - Acceptance: both results printed with explicit PASS/FAIL per check.
  - Files: `tests/test_trend_signal.py`
  - Validation: fails if E2 is checked only in-sample on a cherry-picked
    subperiod instead of the full sample; fails if E3 has any violating day.

- [x] **Task T7: Illustrative visualization (not a performance claim)**
  - Plot (a) the target-position heatmap (date x ticker, weight) and (b) gross
    and net exposure over time. Title and caption must state explicitly that
    this is a visualization of the construction, not a performance result —
    no cumulative PnL or Sharpe number on this plot.
  - Acceptance: plot saved with the explicit non-performance-claim caption
    visible in the title.
  - Files: `sprints/v8.1/plots/positions_and_exposure.png`
  - Validation: fails if any PnL, return, or Sharpe figure appears on the plot
    or in its caption.

- [x] **Task T8: S1 guardrail statement + sprint close**
  - Write the S1 guardrail verbatim in `notes.md`: "This is a learning
    instrument. The rule is mechanical and fully documented. No predictive
    claim, IC test, or Sharpe/performance threshold is in scope for v8.1."
    Close the sprint with an explicit E1-E5/S1 status table.
  - Acceptance: guardrail statement present verbatim; status table present.
  - Files: `sprints/v8.1/notes.md`
  - Validation: fails if the guardrail statement is paraphrased; fails if any
    performance claim appears anywhere in the sprint's close-out text.
