# Sprint v9.6 -- drift on shares, a fresh sizing basis, honest P&L in the email

Three fixes from Monday's run.

## 1. The drift check compares share counts, not dollars

`check_position_drift` diffed Alpaca `market_value` against the cached snapshot and
flagged anything past `DELTA_MIN_NOTIONAL`. A price move changes `market_value` with no
trade at all, so an ordinary market day looked like drift and the alert fired for
nothing. Share counts only change when something actually trades.

   - `execution/alpaca_paper.get_live_book` reads both views off one set of position
     objects, so the dollars that size the delta orders and the shares the drift check
     compares always describe the same book. `get_current_positions` is now a thin
     wrapper over it returning just the dollar view, so the delta math is untouched.
   - `diff_share_counts` is new. `diff_positions` was left alone on purpose: it is
     still used for dollar reconciliation by `scripts/sync_positions_from_alpaca.py`,
     so changing its meaning would have broken that.
   - `SHARE_EPSILON = 1e-6` because share counts are floats that have been through JSON
     and a NUMERIC column. A millionth of a share is far below any real difference.
   - A snapshot row with no `shares` value is reported as **uncomparable**, not as
     flat. Reporting it as zero would have flagged every ticker on the first run after
     the migration, which is the same false alarm being removed.

**Schema change, applied to the live project.** The snapshot had no share counts, so
one was added: `alter table positions add column if not exists shares numeric;`
(`sprints/v9.6/supabase_schema.sql`). Nullable, so no existing row is invalidated.
Verified afterwards against `information_schema.columns`.

Proof that the fix is real rather than asserted: the retained dollar comparison, run on
the same numbers the new test uses (10 shares, price 6000 to 6600), returns
`material: True`. So the old code would have alerted on a pure price move, and the new
test `test_a_price_move_alone_does_not_raise_drift` fails against it and passes now.

The dashboard's drift banner reads either basis. Alerts written before this change have
no `basis` key, so they are still labelled in dollars rather than silently relabelled.

## 2. The sizing basis is refreshed from Alpaca on every path that does not trade

The proposal in Panel H and the next morning's execution are both sized from the cached
Supabase snapshot, deliberately, so that what was approved is what executes. But that
snapshot was only written at the end of a successful execution run, so a rejected,
skipped or refused day left the basis to age indefinitely. Execution sizing is
unchanged and still reads the snapshot, which is what keeps it matching the approval.

`execution/snapshot.py` (new) is the one place that refreshes it. It is called:

   - at the end of a successful `run_signal`, so the proposal is built from a book at
     most one session old; and
   - from `run_execution` on the reject, no-approval and stale-signal paths, so a day
     that does not trade still advances the basis.

Two details worth recording:

   - **It does not go through `DRY_RUN_DEFAULT`.** `connect(dry_run=True)` returns no
     client at all, and `DRY_RUN_DEFAULT` defaults to *true*, so a refresh that
     inherited it would have been a silent no-op on the signal cron. Reading positions
     and NAV is not a trading action, so the read needs no dry-run gate; the `dry_run`
     argument gates only the Supabase writes.
   - **It never raises.** It is bookkeeping around work that has already finished or
     been deliberately skipped, so a broker or Supabase failure is logged and reported
     as `skipped`, and cannot change an exit code.

A refresh that finds no positions in the universe writes `live_nav` only and says so,
rather than writing an empty snapshot.

## 3. The summary reports the book's P&L, with cost on its own line

The email called the day's simulated costs "Day P&L", which billed a small friction as
the result. It now reports:

    Book P&L (live NAV move since previous run): $+850.00
    Turnover cost today: $12.50

The book's P&L is live Alpaca NAV now minus the live NAV recorded by the previous run.
`run_execution` keeps the stored NAV in a `previous_live_nav` separate from the frozen
sizing NAV so "no stored NAV yet" is distinguishable from "stored NAV equals live"; in
the first case the email says the P&L is unavailable rather than reporting zero. Cost
keeps its own line and is never added into the headline.

## Verification

532 tests pass, 13 of them new in `tests/test_snapshot_refresh.py`. The 11 failures and
2 errors are pre-existing and all trace to the unbuilt `pycredit` C++ extension.

No job run was triggered. The only live change was the additive `shares` column.
