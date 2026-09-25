# Sprint v9.2 -- Tasks

**Status:** code complete, not yet deployed

Sprint scope: execution-layer resilience, driven by the live failure of
2026-09-24. No signal or strategy change is in scope.

Status legend: `[ ]` = not done, `[x]` = done, `[~]` = partially done.

**Dependency order:** T0 -> T1 -> T2 -> T3. T0 gates everything else because the
position cache had to be true before any further reasoning about the book.

**Deployment gate:** none of this is live until the commit is pushed and Render
redeploys the `credit-lab-execution` cron. See `notes.md`.

---

- [x] **T0: State reconciliation after the crashed run**

  Pull actual positions from Alpaca, compare against the Supabase `positions`
  cache, print an explicit diff, and correct the cache to match reality. Report
  the diff before proceeding.

  Acceptance: diff reported per ticker; cache corrected and verified by
  read-back; `live_nav` corrected.
  Result: cache was stale at 2026-09-23. All 8 tickers agreed in sign, none
  differed by `DELTA_MIN_NOTIONAL` or more. Corrected under trade_date
  2026-09-24, read-back residual $0.0000, `live_nav` 101,013.45 -> 101,089.45.
  Files: `scripts/sync_positions_from_alpaca.py`, `execution/alpaca_paper.py`
  (`diff_positions`).

- [x] **T1: Run-level resilience, one failing leg cannot abort the run**

  Wrap every order submission so a single failure cannot abort the run or strand
  a partial book. Classify the failure, log it as a rejected or skipped leg with a
  distinct reason code, and continue through the remaining legs. Never halt via an
  unhandled traceback.

  Acceptance: a run with one failing order completes, logs the failure, and
  leaves recorded state consistent with what actually executed.
  Result: `submit_orders` returns one `SubmitOutcome` per leg and never raises for
  a per-leg failure. Policy is continue-and-log for order-level rejections, halt
  only for transport-class failures (unknown submission state), and a halt writes
  full state and leaves `cron_runs` unrecorded. Three run-level tests drive
  `run_execution.main()` with Alpaca and Supabase stubbed.
  Files: `execution/alpaca_paper.py`, `scripts/run_execution.py`,
  `tests/test_paper_execution.py`

- [x] **T1a: Persist the rejection audit trail to Supabase**

  Alpaca stores no record of a submit-time rejection, so the local rejection log
  is the only audit trail and must reach Supabase.

  Acceptance: every rejected or skipped leg is persisted with its reason code.
  Result: `order_rejections` table (append-only) plus `build_rejection_rows` and
  `write_order_rejections`. Covers guard rejections, the shortability pre-check,
  and every skip or rejection at submission. A write failure logs ERROR and does
  not abort the run.
  Files: `sprints/v9.2/supabase_schema.sql`, `execution/alpaca_paper.py`,
  `dashboard/supabase_client.py`, `scripts/run_execution.py`

- [x] **T1b: Backfill the five real fills from the crashed run**

  Reconstruct the records the crashed run never wrote, flagged as backfilled.

  Acceptance: fill records, `pnl_log` and attribution are complete for
  2026-09-24 and clearly marked as reconstructed.
  Result: `scripts/backfill_execution_run.py`. 7 legs reconstructed, 5 matched to
  real fills and marked through the v6.5 cost model, 2 recorded as rejected with
  reason codes. Wrote the reconciliation JSON, the `pnl_log` row, 5
  `live_attribution` rows, 5 parquet rows and 2 `order_rejections` rows, all
  `backfilled=true`. Re-runnable without duplicating.
  Files: `scripts/backfill_execution_run.py`

- [x] **T2: Shortability handling before submitting a short**

  Pre-check the asset `shortable` flag before submitting a short. Skip and record
  a name the signal wants short but cannot be shorted. Only opening or increasing
  a short is blocked; `buy_to_close` and reducing an existing short still go
  through.

  Acceptance: tests for the non-shortable path and for the reduce-still-allowed
  path.
  Result: `shorts_requiring_check`, `get_shortable_flags` (fails closed) and
  `apply_shortable_filter` (pure), wired into `run_execution.py` step 7b. Five
  unit tests plus one run-level test.
  Files: `execution/alpaca_paper.py`, `scripts/run_execution.py`,
  `tests/test_paper_execution.py`

- [x] **T3: Notes, tests and status**

  Record T1 and T2 as dated findings from the live run, with the LQD
  non-shortable event and the run-crash-on-single-failure event as the motivating
  causes. Confirm the execution test suite passes.

  Acceptance: notes record both events; full execution suite green.
  Result: `sprints/v9.2/notes.md`, `sprints/v9.2/TASKS.md`. Full suite 398 passed;
  the 11 failures and 2 errors are pre-existing and proven identical at HEAD in a
  clean worktree (missing `pycredit` extension), unrelated to this work.
  Files: `sprints/v9.2/notes.md`, `sprints/v9.2/TASKS.md`

- [x] **T4: Review changes after approval**

  Four changes requested on review, plus one hazard found while preparing the
  dry run.

  Acceptance: all four requested changes implemented and tested; the dry run
  documented.
  Result:
  1. `CROSSING_FLAT_NOT_SHORTABLE` as a distinct code for the open leg of a
     long-to-short crossing; crossing behaviour kept, since ending flat is the
     smaller deviation from a short target than staying long.
  2. The `order_rejections` write-failure ERROR line now prints every field of
     every affected leg, because the disk is temporary and the log is the
     fallback.
  3. `reconcile()` admits one share's value as tolerance for whole-share short
     legs, and records the `tolerance` used per leg. Flagged legs on the
     2026-09-24 artifact drop from 4 to 2; long legs keep the 0.5% tolerance.
  4. Pre-commit hooks: none exist in this repository, verified (no config, no
     `hooksPath`, samples only, no lint tooling installed). The equivalent checks
     were run by hand: Pylance clean on every changed file, plus an AST
     unused-import scan that found and removed three unused imports, one of them
     this sprint's own.
  5. Found and fixed while preparing the dry run, by rehearsing `main()` locally
     rather than only reading it: dry-run mode was writing `live_nav` (with the
     placeholder 100,000), writing a zero `pnl_log` row, clearing the drift
     alert, recording `cron_runs` (which would have made the real scheduled run
     skip itself the same day via the idempotency gate), and flagging every
     dry-run leg as a reconciliation discrepancy. All five are now skipped or
     excluded in dry-run, each with an explicit log line. A dry run writes
     nothing to Supabase.
  Files: `execution/alpaca_paper.py`, `scripts/run_execution.py`,
  `tests/test_paper_execution.py`, `sprints/v9.2/notes.md`

- [x] **T5: Deploy**

  Push the sprint work so the Render execution cron picks it up. Then the operator
  runs a manual dry run with `DRY_RUN=true` to validate the Python version Render
  uses.

  Acceptance: pushed; dry-run confirmation lines documented in `notes.md`.
  Result: pushed to `main`. The clean-dry-run log sequence and the lines that
  indicate a problem are recorded in the notes under "What a clean dry run looks
  like". 71 passed in `tests/test_paper_execution.py`.
  Files: `sprints/v9.2/notes.md`

