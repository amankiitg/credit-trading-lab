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

## 2026-09-25 -- Follow-up: the signal was built from a gapped close matrix

After the hang fix deployed, the signal run completed and produced a book in which
EFA, EEM, IEF, HYG and LQD had all moved by the same ~0.76 factor while SPY, TLT
and GLD were recomputed. This section is the diagnosis and the fix.

### The gap

Those five tickers each had **4895 rows against 4896** for SPY, TLT and GLD, and
sigma was NaN from 2026-09-23. Sigma at date d covers the trailing 63 rows, so a
NaN return on 2026-09-23 makes sigma NaN from 2026-09-23 onward. The missing
session is therefore **2026-09-23**, for EFA, EEM, IEF, HYG and LQD.

Cannot be reproduced from the repo copies: all eight local parquets end
2026-06-22, have 4830 rows and share one date set, so they contain no holes at
all. They were all written at 2026-08-11 21:01:25 and are internally consistent.

Alpaca's IEX daily bars cover **every** session in that window for all eight
tickers including 2026-09-23, so 2026-09-23 was an ordinary Wednesday session with
data available, and the hole is a Yahoo-side gap in exactly five of the eight
fetches.

### The 0.76 factor: CONFIRMED, and reproduced exactly

Confirmed, and not approximately. Mimicking the missing session on local data and
re-running the same pipeline as `run_signal`:

```
SPY  ratio=+1.36999   (recomputed)
EFA  ratio=+0.76501   sigma_prev=nan   sigma_last=nan
EEM  ratio=+0.76501   sigma_prev=nan   sigma_last=nan
TLT  ratio=+1.36999   (recomputed)
IEF  ratio=+0.76501   sigma_prev=nan   sigma_last=nan
HYG  ratio=+0.76501   sigma_prev=nan   sigma_last=nan
LQD  ratio=+0.76501   sigma_prev=nan   sigma_last=nan
GLD  ratio=+1.56787   (recomputed)

five ratios identical? True   value: 0.765007
```

The chain, with the code that does it:

1. `load_universe_close` outer-joins the per-ticker parquets, so a session one
   ticker lacks becomes NaN for that ticker on a date the others have.
2. `compute_trend`: `defined = trail_ret.notna() & sigma.notna()`. The NaN row
   makes the log return NaN, which makes the 63 day rolling `sigma` NaN, so
   `defined` is False for that name from 2026-09-23. `weight` is then NaN there.
3. `apply_rebalance_control` treats a NaN desired weight as a data gap rather than
   an exit, and deliberately carries the previous held weight forward (its own
   comment says so).
4. Its final joint gross re-cap, `scale = (g_max / gross).clip(upper=1.0)` over
   the whole row, then multiplies **every** name, including the carried-forward
   ones. That single factor is why five names move identically.

So it is not "carried forward and scaled" as a suspicion, it is exactly that, and
the five moving together is the signature of the re-cap rather than of five
coincidentally similar signals.

### Item 2 -- repair tool

`scripts/backfill_raw_gap.py`, report-only by default, `--apply` to write. It
detects holes with the same first-observation rule the gate uses, fetches real
daily bars from Alpaca IEX, and inserts them.

Deliberately not a forward fill: repeating the previous close invents a zero
return, which would flatten realised vol and leave the row looking healthy. A real
bar goes in or the gap is reported and left alone.

`adj_close` is derived rather than copied, because Alpaca returns raw OHLC and this
pipeline reads `adj_close`. The inserted row is scaled by that ticker's own
cumulative adjustment factor (adj_close/close from its most recent existing row),
so the ticker's existing convention is preserved.

Demonstrated end to end on a throwaway copy with an injected hole at 2026-06-18
for the same five tickers: detected all five, fetched real bars (EFA close 104.39,
volume 995,588, adj ratio 1.0), inserted, verified coverage across 4830 dates, and
a re-run reported no gaps. The repo's own parquets were not touched (their mtimes
are still 2026-08-11).

Two honest caveats about the source:

- IEX close is **not** the consolidated close. On the injected test the IEX close
  was 104.39 against the 104.41 the Yahoo-sourced row held, about 0.02% apart.
  Fine for a repair, not byte-identical to a Yahoo row.
- IEX `volume` is an IEX-only fraction of consolidated volume. Nothing downstream
  reads volume (`load_universe_close` returns `adj_close` only), so this is inert
  here, but it would matter if volume were ever used.

### Item 3 -- data-quality gate

`signals/data_quality.py` (pure, no I/O) and a gate in `run_signal` step 4c, before
any write:

- `find_row_gaps(close)`: dates where a ticker has no observation but the universe
  does. Leading NaN is **not** a gap, because staggered inception (GLD from 2004,
  EFA/EEM/TLT later) is legitimate. Only NaN after a ticker's first observation is
  a hole.
- `check_signal_ready(close, sigma, as_of_date)`: the two conditions from the live
  failure, the row gap and NaN sigma on the as-of date.

On failure it logs every offending ticker and date at ERROR, logs
`REFUSING to write a signal for <date>`, says nothing was written so the stored
signal is unchanged, and returns 1. Nothing reaches Supabase.

Note the gate blocks on a gap anywhere in history, not only a recent one. That is
the safe default for a book that trades every session; if an ancient hole ever
blocks a run, the log names it and the repair tool exists.

### Item 4 -- the "nan" stop states

`stop_states.state` held the literal string `'nan'` for exactly EFA, EEM, IEF, HYG
and LQD, the five gapped tickers. Fixed with `canonical_state()` in
`risk/stop_loss.py`, which maps None, float nan, `np.nan`, the strings "nan" and
"None", empty strings and any unknown text to the explicit string **UNKNOWN**, and
passes through only NORMAL, REDUCED and STOPPED.

Two things worth recording precisely:

- The old code was `str(latest_state.get(t)) if ... is not None else "NORMAL"`. So
  a None cell became the string "NORMAL", which asserts a state that was never
  computed. That is its own bug, quieter than "nan" but the same category.
- I could not pin how the float nan arose. `np.full(n, None, dtype=object)` yields
  None locally and `str(None)` is "None", never "nan", so a genuine float nan had
  to be in that column. The current `compute_episodes` does not put one there, and
  its state seeding is unchanged since the v9.1 commit (93adae7, 2026-08-11), which
  is the day before the existing rows were written. Most likely a pandas version
  difference on Render turning an all-None object column numeric. The fix covers
  every candidate, so the exact provenance does not change the remedy.
- `updated_at` does **not** refresh on an upsert (the `now()` default applies on
  insert only), so those rows still read 2026-08-12 even if they were rewritten
  since. `updated_at` is not a reliable "last written" indicator here.

The five existing bad rows still say 'nan' in Supabase. Correcting them is an
UPDATE, which is a live write I have not done.

### Open questions for the operator

1. **The repair does not run on Render.** The parquets there are re-fetched by
   `ingest()` every run and the disk is ephemeral, so repairing the repo copies
   helps local work and backtests but cannot fix the live path. Either wire the
   Alpaca gap repair into `ingest` (self-healing, mixes two price sources) or rely
   on the gate to refuse the signal and repair by hand (safe, but blocks the
   signal until Yahoo self-heals or someone acts).
2. The five stale `stop_states` rows want a one-off UPDATE to 'UNKNOWN'.
3. Yahoo may already have backfilled 2026-09-23; the next run will show it, because
   the gate will either pass or name the gap.

### Tests

23 new: `tests/test_data_quality.py` (10, including a miniature of the live shape
asserting 5 row gaps and 5 NaN sigmas, and a job-level test that the gate refuses
to write) and the `canonical_state` cases in `tests/test_stop_loss.py`. Full suite
438 passed with the same 11 pre-existing `pycredit` failures.

---

## House rules

- No look-ahead: nothing here feeds a signal. The hang fix bounds run time and the
  gate refuses to write from incomplete data.
- Costs untouched.
- No edge claims. The 0.765 factor is a reproduction of a bug, not a result.

