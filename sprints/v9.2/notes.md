# Sprint v9.2 -- Notes

Sprint scope: execution-layer resilience. Motivated entirely by a live failure on
2026-09-24, not by a research question. No signal or strategy change is in scope.

---

## 2026-09-24 -- Live incident: one rejected leg killed the run

### What happened (from Alpaca's own records, not from inference)

- `run_execution` fired on schedule at 14:30 UTC and the run was approved
  (`auto_approve=true`).
- 7 legs were intended: EFA, EEM, TLT, IEF, HYG, LQD, GLD. Reconstructed from the
  frozen signal weights (`signal_as_of_date=2026-09-22`) applied to the
  2026-09-23 position snapshot with the frozen nav. The reconstruction reproduces
  all five real fills to the cent, which is what makes it trustworthy.
- 5 legs filled at 14:31:51-52 UTC: EFA buy_to_open $475.28 notional, EEM
  buy_to_open $277.18, TLT sell_to_open 4 shares, IEF sell_to_open 2 shares, HYG
  buy_to_open $304.29.
- LQD sell_to_open $290.39 was rejected by Alpaca with code 42210000, "asset
  cannot be sold short".
- GLD sell_to_open $580.74 was never attempted.
- Nothing was written back: no fill records, no pnl_log, no attribution, no
  reconciliation JSON, no `cron_runs` row. The position cache therefore sat at
  2026-09-23 while the broker held a newer book.

### Root cause 1 (T1): one unhandled exception aborted the entire run

`submit_orders` called `client.submit_order(...)` with no exception handling. The
`APIError` propagated out of `main()`, so every step after submission (poll fills,
build records, mark costs, dust cleanup, reconcile, write positions, write
pnl_log, record run) was skipped. One broker rejection cost the book its remaining
legs and its entire bookkeeping.

### Root cause 2 (T2): no shortability pre-check

LQD reports `shortable=false` today. The signal wanted LQD shorter, which is a
sell_to_open (opening or increasing a short). Nothing consulted the asset flag
before submitting.

Shortability is not static. LQD filled sell_to_open on 2026-09-01, 2026-09-03 and
2026-09-14, then reported `shortable=false` on 2026-09-24. A cached or hardcoded
list would have been wrong, so the check has to be live, per run.

### Alpaca does not record a submit-time rejection

A rejected submit leaves no order in the order history at all. Checked every
status over a 30 day window: the LQD order is simply absent. The local rejection
log is therefore the only audit trail for such a leg, which is why T1 persists it.

---

## 2026-09-24 -- T1 fix: per-leg failure isolation

### Implementation

- `execution/alpaca_paper.py::submit_orders` now returns one `SubmitOutcome` per
  PENDING leg, in order, so a caller can never lose a leg. Each leg is attempted
  through `_submit_one_safe`, which converts any exception into a classified
  outcome.
- `classify_submit_failure(exc) -> (reason_code, halts_run)`.
- Reason codes (stable, distinct): `QTY_ROUNDS_TO_ZERO`, `SKIPPED_NOT_SHORTABLE`,
  `ASSET_NOT_SHORTABLE_AT_SUBMIT`, `SHORTABLE_CHECK_FAILED`, `ALPACA_API_ERROR`,
  `SUBMIT_EXCEPTION_UNKNOWN_STATE`, `SKIPPED_AFTER_HALT`.
- `build_fill_records` consumes outcomes and carries `reason_code`, `detail`,
  `leg` and `backfilled` onto every `FillRecord`. It raises if a PENDING leg has
  no outcome, rather than dropping it (P1).
- `reconcile()` records `leg`, `reason_code`, `detail` and `backfilled` per leg,
  plus `rejected_or_skipped_legs` and `backfilled_legs` counts.
- `build_rejection_rows` plus `dashboard.supabase_client.write_order_rejections`
  persist every rejected or skipped leg to the Supabase `order_rejections` table.
  Append-only, because the whole point is that a row is never overwritten.

### Policy decision: continue-and-log, halt only on unknown state

- Continue-and-log is the default for order-level failures. The broker evaluated
  the order and declined it, so the state is known and the rest of the book should
  still trade.
- A transport-class failure halts submission. `classify_submit_failure` treats
  anything that is not an `APIError` as unknown state, because Alpaca may or may
  not have received the order and continuing would stack ambiguity on ambiguity.
  The remaining legs are recorded `NOT_ATTEMPTED` with `SKIPPED_AFTER_HALT`.
- A halt is clean, never a traceback: state is written to Supabase, the
  reconciliation JSON is written, `cron_runs` is deliberately NOT recorded (it is
  the idempotency gate, and leaving it unwritten is what lets the next tick
  retry), and the process exits 2.
- If the `order_rejections` write itself fails, the run logs ERROR and continues.
  The book being recorded matters more than the audit row, and the loss is
  reported rather than silent. The local reconciliation JSON still holds it.

---

## 2026-09-24 -- T2 fix: shortability pre-check

- `shorts_requiring_check(orders)`: tickers with a PENDING `sell_to_open` leg.
- `get_shortable_flags(client, tickers)`: reads the asset `shortable` flag per
  ticker. Fails closed on a lookup error, reason `SHORTABLE_CHECK_FAILED`, because
  an unverified short risks another 42210000 abort whereas a skipped short is
  logged, tracked and recoverable on the next run.
- `apply_shortable_filter(orders, shortable)`: pure, no I/O. Blocks only PENDING
  `sell_to_open` legs and leaves every other leg alone.
- Wired into `run_execution.py` step 7b, before anything is submitted, so a
  non-shortable leg is never sent at all.

### Known live constraint: a wanted but unshortable short

- The wanted short becomes a skipped leg. It is logged, written to
  `order_rejections`, and shown in the reconciliation JSON with its reason code.
  The book runs with the short missing and tracked, never silently approximated.
- Only opening or increasing a short is blocked. `buy_to_close` (reducing or
  closing a short) and `sell_to_close` (reducing or closing a long) still go
  through, so a broker restriction on opening shorts can never trap an existing
  position.
- Consequence worth stating plainly: for a long-to-short crossing on a
  non-shortable name, leg 1 (`sell_to_close`) still runs and leg 2
  (`sell_to_open`) is skipped, so the book ends FLAT in that name rather than
  short. That is a bigger deviation than doing nothing, and it is intentional: the
  leg that shrinks the position is risk-reducing and is not the leg the
  restriction applies to.
- This sits alongside the earlier v8.6 finding that fractional shorts are
  rejected, so short legs are quantized to whole shares. Together they mean a
  short leg can be smaller than sized, or absent entirely. Both are recorded, not
  assumed away.

---

## 2026-09-24 -- Schema

New `order_rejections` table (append-only) and `backfilled` columns on `pnl_log`
and `live_attribution`. Applied to the live project. See `supabase_schema.sql`.

---

## 2026-09-24 -- Crash recovery: reconciliation and backfill

### Task 0, state reconciliation

- The Supabase position cache was stale at 2026-09-23.
- Alpaca vs cache diff: all 8 tickers agreed in sign and none differed by as much
  as `DELTA_MIN_NOTIONAL` ($250). The cache had no structural error, no phantom
  position and no wrong sign, only a missing day. That is the honest reading: the
  dropped write was a bookkeeping loss, not a corrupted book.
- Corrected by writing Alpaca's actual book under trade_date 2026-09-24 and
  updating `live_nav` from 101,013.45 to 101,089.45. Verified by read-back, max
  residual $0.0000.
- The substantive residue of the crash was the two legs that never landed: LQD
  -$290.39 and GLD -$580.74 of intended short notional, about $871 gross.

### Task 1 addition, backfill of the five real fills

`scripts/backfill_execution_run.py` reconstructs the 7 intended legs from the
frozen inputs, matches the 5 real fills against Alpaca's order history, marks them
through the v6.5 cost model, and writes the records a live run would have written,
each flagged `backfilled=true`:

- `execution/logs/reconciliation_2026-09-24.json`: 7 legs, 4 flagged. Two flags
  are whole-share quantization on the short legs (TLT intended 345.24, filled
  321.04; IEF intended 257.30, filled 180.36), two are the unexecuted legs.
- `pnl_log` for 2026-09-24: gross 1,558.12, turnover cost 1.1777, net -1.1777
  (same convention as a live row, where `gross_pnl` is filled notional).
- 5 rows to `live_attribution`, 5 rows appended to
  `data/processed/attribution.parquet`.
- 2 rows to `order_rejections`: LQD `ASSET_NOT_SHORTABLE_AT_SUBMIT`, GLD
  `SKIPPED_AFTER_HALT`.
- `cron_runs` for 2026-09-24 remains deliberately unwritten, so the next tick
  retries.
- Re-running the script is idempotent: it deletes only its own prior backfilled
  rows for that date and refuses to append the parquet twice.
- The attribution mark uses the real same-day close from Alpaca's IEX feed. The
  live path cannot do this, because at run time it only has the previous
  session's signal closes and the local raw parquet ends at 2026-06-22. The
  reconstructed rows are flagged precisely so this difference is visible.

### Schema change note

`feed_attribution` now emits a `backfilled` column, 18 columns instead of 17.
Historical parquet rows are normalized to False. Consumers read columns by name,
so the added column is backward compatible. `test_feed_attribution_schema_match`
was updated to the new expected column list.

---

## Test results

- `tests/test_paper_execution.py`: 66 passed (51 before this sprint, so 15 new).
- Full suite: 398 passed, plus 11 failed and 2 errors. Every one of those 11
  failures and 2 errors reproduces identically at HEAD in a clean `git worktree`
  and is caused by the `pycredit` compiled extension not being importable in this
  interpreter. None of them touch execution, attribution or Supabase paths.
  Verified by running the same test files at HEAD, not assumed.

## Deployment

The execution cron runs `python scripts/run_execution.py` on Render at 14:31 UTC
on weekdays. Nothing in this sprint is live until the commit is pushed and Render
redeploys. At the time of writing the next scheduled run is 2026-09-25 14:31 UTC.
Until then, tomorrow's run would hit LQD again; with T2 live it is skipped and
recorded instead of aborting the run.

## House rules compliance

- No look-ahead: the backfill marks against the run date's own close, which is
  after the fact by construction and is flagged as backfilled. Nothing here feeds
  a signal.
- Costs are marked through the existing v6.5 model, unchanged.
- No edge claims. This sprint makes the execution layer survivable; it says
  nothing about returns.
