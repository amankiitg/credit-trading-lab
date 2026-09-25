# Sprint v9.3 -- Notes

Scope: the signal cron hang. Diagnosis and hardening of both cron jobs. No signal
or strategy change.

---

## 2026-09-24 -- Incident

- 2026-09-24 21:31 UTC: `run_signal` logged
  `proposed weights for 2026-09-24: {...}` and then produced **no further output
  for over four hours**, until it was cancelled by hand.
- Nothing was written after that line, so the stored signal in Supabase stayed at
  `signal_as_of_date = 2026-09-22`.
- The 2026-09-23 run failed the same way. No log was available for it.

---

## Item 1 -- every step after the "proposed weights" log line

Steps in order, with the network calls and their timeouts as they were **before**
this sprint:

| # | Step | Network call | Timeout before |
|---|------|--------------|----------------|
| 1 | `from risk.stop_loss import compute_episodes` | none | n/a |
| 2 | `tidy.pivot(...)`, `close[list(held.columns)]` | none | n/a |
| 3 | `compute_episodes(held, close_matrix, sigma_matrix)` | none, pure CPU | **none, and uncoverable by any socket timeout** |
| 4 | per-ticker `stop_rows` loop, 8 `stop_state[...]` INFO lines | none | n/a |
| 5 | `set_setting("signal_as_of_date")`, `set_setting("signal_target_weights")`, `set_setting("signal_close_prices")` | Supabase HTTPS (postgrest) | 10s (`postgrest_client_timeout=10`) |
| 6 | `write_decision(as_of_date, "proposed")` | Supabase HTTPS | 10s |
| 7 | `client.table("stop_states").upsert(row).execute()` x8 | Supabase HTTPS | 10s each |
| 8 | `record_run("run_signal", today)` -> `cron_runs` | Supabase HTTPS | 10s |

**No Alpaca call and no yfinance call happens after that line.** Earlier in the
same job, before the line:

- yfinance, in step 3's `ingest(UNIVERSE)`. yfinance defaults to a 10s per-call
  timeout (`PriceHistory.history(..., timeout=10)`), so it was already bounded,
  though not deliberately and not visibly.
- The ingest retry loop, which may legitimately run for up to 4 hours (24 attempts
  at 10 minute intervals). It logs a warning per attempt, so it is never silent.

For completeness, the execution job's network calls before this sprint:

| Job | Call | Timeout before |
|-----|------|----------------|
| run_execution | Supabase (settings, positions, decisions, writes) | 10s |
| run_execution | **Alpaca** (`get_account`, `get_all_positions`, `get_asset`, `submit_order`, `get_order_by_id`, `close_position`) | **none at all** |
| run_execution | Resend email (`execution/alerts.py`) | 15s |

The Alpaca gap is real and worth stating plainly: `alpaca.common.rest` contains
zero occurrences of the word `timeout`, and its request is issued as
`self._session.request(method, url, **opts)` with no timeout in `opts`. A stalled
Alpaca connection would have blocked the execution job indefinitely.

---

## Item 2 -- the hang point

`risk/stop_loss.py::compute_episodes`, in the `if len(ep_indices) == 0` branch.

The episode scan is an outer `while i < n` that either advances `i` or enters an
episode. The inner loop can break on its **first** pass, which leaves
`ep_indices` empty and `i` unchanged. That branch then did `continue`, so the
outer loop re-entered the same index forever.

The precondition is exact: a day with a non-zero held weight and a usable price,
but no usable sigma. That arises from an upstream data gap. A single missing or
NaN price row makes the return NaN, which makes the 63 day rolling vol NaN, and
`apply_rebalance_control` carries the held weight forward, so the weight is still
non-zero on the day whose sigma is unusable.

Why this matches the symptoms exactly:

- It is a **pure CPU spin with no I/O at all**, so it produces no output and no
  exception. That is the observed "silent for over four hours".
- No socket timeout, no retry limit and no HTTP deadline can catch it. The process
  was busy, not blocked. Only a wall-clock deadline can.
- It is deterministic in the data, so it would recur on every run once the
  offending day was in the frame, which fits 09-23 and 09-24 both failing while
  09-22 succeeded.

### Proof

I did not settle for reading the code. I loaded the **pre-fix** `risk/stop_loss.py`
straight out of HEAD and called `compute_episodes` on a frame with a non-zero
weight on a NaN-sigma day, under a 5 second alarm:

```
PRE-FIX: still spinning after 5.0s -> infinite loop CONFIRMED in HEAD code
POST-FIX: returned in 0.001s, day1 z is NaN: True
```

### What I could not confirm

I could **not** verify that the live data actually contained such a day. The local
raw parquets end at 2026-06-22 and contain zero non-zero-weight days with a NaN
sigma or NaN price (checked). The Render logs and the live parquet copies are not
available to me. So the infinite loop is proven to exist and is the only thing in
that window that can hang silently and indefinitely, but I cannot prove it is what
the live run hit rather than something else.

What closes that gap is the step logging added below: the next run will show
`STEP begin: 4b v9.1 stop overlay compute (advisory)` with no matching `done`, and
the step budget will abandon it in 120s rather than four hours. If the hang
recurs, it will be identified definitively rather than inferred.

---

## Item 3 -- the fix

### 1. The root cause

`risk/stop_loss.py`: the empty-episode branch now advances the index and records
the day as unusable, logging one warning per ticker with the affected dates. The
day's outputs stay NaN, which is honest: the state for that day is genuinely
unknown, and inventing one would be worse.

### 2. `execution/job_guard.py` (new)

- `job_guards(max_secs, logger)`: installs the socket floor and a hard SIGALRM
  deadline, and always restores both. SIGALRM interrupts a blocking syscall under
  PEP 475 when the handler raises, so it bounds calls in libraries that offer no
  timeout knob, and it bounds a pure-CPU spin too.
- `step(name)`: logs `STEP begin: <name>` then `STEP done in Xs: <name>` or
  `STEP FAILED after Xs: <name>`, always re-raising. The last unmatched `begin`
  names the hang point.
- `current_step()`: survives unwinding, so the timeout message can name the step.
- `step_budget(seconds)`: a shorter deadline for one optional step, re-arming the
  job's remaining budget afterwards. Needed because SIGALRM has a single timer.
- `install_socket_timeout` / `restore_socket_timeout`: a floor for libraries that
  pass no timeout, restored so global state is not left altered.

**`JobTimeout` derives from `BaseException`, not `Exception`,** and that is
load-bearing. These jobs are full of `except Exception` blocks (the yfinance retry
loop, the advisory overlay guard, `get_live_nav`, stop_states writing, dust
cleanup). If the deadline were an Exception, one of them would swallow it, the job
would continue with its alarm already spent, and a timed-out run would go on to
write state and exit 0. This is the same reasoning that makes `KeyboardInterrupt`
and `SystemExit` BaseExceptions.

### 3. Timeouts on every network call

- yfinance: explicit `timeout=NETWORK_TIMEOUT_SECS` (default 30s) in
  `signals/load.py::fetch`.
- Supabase: already 10s, unchanged.
- Resend: already 15s, unchanged.
- Alpaca: `apply_request_timeout()` in `execution/alpaca_paper.py`. alpaca-py has
  no timeout parameter, so the function defaults one into the client session's
  request kwargs at the single choke point all calls pass through. Called from
  `connect()`, so both jobs get it. It returns False and logs when a client has no
  usable session, so a missing timeout is visible rather than assumed.
- `socket.setdefaulttimeout(NETWORK_TIMEOUT_SECS)` as a process-wide floor.

### 4. Every step now logs begin and done

run_signal: `1 NYSE calendar check`, `2 idempotency check`,
`3 refresh closes from yfinance (retry loop)`, `3b load universe closes`,
`4 v8.2 signal pipeline`, `4b v9.1 stop overlay compute (advisory)`,
`5 write signal settings to Supabase`, `5b write decision='proposed' to Supabase`,
`5c write stop_states to Supabase`, `6 record run`.

run_execution: `1 NYSE calendar check`, `2 idempotency check`,
`3 load stored signal from Supabase`, `3b fallback yfinance ingest`,
`4 decision gate (Supabase)`, `5 connect to Alpaca`,
`6 read NAV and current positions`, `6b position drift check`,
`6c fallback: recompute v8.2 signal locally`, `7 compute delta orders and apply
guards`, `7b shortability pre-check (Alpaca assets)`, `8 submit orders`,
`9 poll fills and build fill records`, `9b persist rejection audit to Supabase`,
`10 dust cleanup`, `11 reconcile and feed attribution`,
`11a write live attribution to Supabase`,
`11b write live_nav, positions and pnl_log to Supabase`, `12 record run`.

### 5. A timeout fails loudly with a clean exit

Both `main()` functions catch `JobTimeout`, log
`<job> TIMED OUT: <detail>. Last step started: <step>` at ERROR, cancel the
deadline, restore the socket timeout and **return 3**. Never an unhandled
traceback, never a silent 0.

### 6. The advisory overlay can no longer block the signal

The v9.1 stop ladder is display-only (its gate was REJECTED, weights are never
modified by it), so it should never be able to stop the run from writing a fresh
signal. It now runs under `step_budget(OVERLAY_MAX_SECS)` (default 120s); on
expiry the overlay is skipped with a warning and the run continues. This is what
turns a 4h15m worst case into a 2 minute one for the specific failure we saw.

Default budgets, all env-overridable:

| Variable | Default | Meaning |
|----------|---------|---------|
| `NETWORK_TIMEOUT_SECS` | 30 | per network call |
| `SIGNAL_JOB_MAX_SECS` | 4h15m (ingest budget + margin) | whole signal job |
| `OVERLAY_MAX_SECS` | 120 | advisory stop overlay only |
| `EXECUTION_JOB_MAX_SECS` | 30m | whole execution job |

---

## Item 4 -- tests

| Test | What it pins |
|------|--------------|
| `test_blocking_socket_read_is_cut_off_by_the_deadline` | a real blocking `recv()` with no socket timeout set is interrupted in about 1s |
| `test_job_timeout_is_not_caught_by_generic_exception_handling` | an `except Exception` cannot swallow the deadline |
| `test_job_guards_restores_the_socket_timeout` | global socket state is restored |
| `test_step_logs_begin_and_done` | both boundary lines are emitted |
| `test_step_logs_failure_and_reraises` | failures are logged and re-raised |
| `test_current_step_survives_unwinding` | the step name is still readable after a failure |
| `test_signal_job_exits_nonzero_when_a_step_hangs` | hung signal job returns 3 and names the step |
| `test_execution_job_exits_nonzero_when_a_step_hangs` | same for the execution job |
| `test_healthy_steps_leave_no_unmatched_begin` | negative control: a clean run leaves no dangling begin |
| `test_step_budget_restores_the_job_deadline` | a step budget shortens the deadline, never spends it |
| `test_signal_job_skips_an_over_budget_advisory_overlay` | an over-budget overlay is skipped and the run continues |
| `test_compute_episodes_does_not_hang_on_nan_sigma_with_non_zero_weight` | regression for the infinite loop, with its own alarm so a regression fails instead of hanging the suite |

`tests/test_job_guard.py` is new (11 tests). The stop-loss regression test is in
`tests/test_stop_loss.py`. Full suite: 415 passed, plus the 11 failures and 2
errors that are pre-existing (`pycredit`, verified identical at HEAD).

---

## Item 5 -- is it safe to re-run the signal for 2026-09-24?

### Not yet

**Do not trigger it until this fix is deployed.** A manual trigger now would run
the pre-fix code on the same kind of data that hung on 09-23 and 09-24, so it
would very likely hang again, and Render would sit on it for the full job budget.

### Once deployed, yes, and here is exactly what it does

- `as_of_date` is the latest close in the freshly fetched data, not the run date.
  At this hour (about 01:40 UTC = 21:40 ET on 2026-09-24) Yahoo has the 2026-09-24
  close, so the run would produce `as_of_date = 2026-09-24` and write
  `signal_as_of_date`, `signal_target_weights` and `signal_close_prices` for it.
- That is strictly better for the 14:31 UTC execution than the current
  2026-09-22 signal, and the position snapshot it will delta against is the
  corrected 2026-09-24 book.

### Idempotency, which is where the catch is

- **The execution job's idempotency is NOT affected.** Its gate is
  `cron_runs(job_name='run_execution', run_date=<utc date>)`. The signal job only
  ever writes `job_name='run_signal'`, so the two never collide.
- **The signal job's own idempotency IS affected, and this is the catch.** A manual
  run records `cron_runs(run_signal, 2026-09-25)` because Render's container date
  is UTC and it is already 2026-09-25 UTC. The scheduled 21:30 UTC signal run that
  evening would then log `already ran for 2026-09-25 -- exit 0 (idempotent)` and
  **skip**. The 2026-09-25 close would never become a signal, and the 2026-09-26
  execution would trade the 2026-09-24 signal, one day stale.
- Two ways out: delete the `(run_signal, 2026-09-25)` row from `cron_runs` after
  the manual run, or simply trigger the manual run after 21:30 UTC, at which point
  it IS the scheduled run.
- Neither is something I will do without being asked, since both touch live
  Supabase.

---

## House rules

- No look-ahead: nothing here feeds a signal. The fix bounds run time only.
- Costs untouched.
- No edge claims.
