# Sprint v9.3 -- Tasks

**Status:** code complete, NOT deployed (awaiting a decision on the push)

Scope: the signal cron hang of 2026-09-24. Diagnosis and hardening of both cron
jobs. No signal or strategy change.

Status legend: `[ ]` = not done, `[x]` = done, `[~]` = partially done.

---

- [x] **T1: Inventory every step after the "proposed weights" log line**

  List, in order, every step after that line, flagging each network call and
  whether it has a timeout.

  Acceptance: complete ordered inventory with timeouts named.
  Result: 8 steps. Only steps 5 to 8 make network calls, all Supabase with a 10s
  client timeout. No Alpaca and no yfinance call occurs after that line. The one
  step with no possible timeout is the pure-CPU `compute_episodes` call.
  Recorded in `notes.md`.

- [x] **T2: Find the most likely hang point and explain why**

  Acceptance: a named location with a mechanism, not a list of candidates.
  Result: `risk/stop_loss.py::compute_episodes`, the `len(ep_indices) == 0` branch,
  which did not advance the scan index. Precondition is a non-zero held weight on a
  day with no usable sigma, reachable from an upstream data gap. It is a pure CPU
  spin with no I/O, which is why it was silent and why no socket timeout could have
  caught it.
  Proof: the pre-fix module loaded from HEAD is still spinning at 5.0s; the fixed
  module returns in 0.001s.
  Stated limitation: the live NaN pattern could not be confirmed without the
  Render logs or live parquets.
  Files: `sprints/v9.3/notes.md`

- [x] **T3: Fix it**

  Three parts: explicit timeouts on every network call in both jobs, a log line
  before and after each step, and a timeout that fails loudly with a clean exit
  rather than hanging.

  Acceptance: all three, in both jobs.
  Result:
  1. Root cause fixed in `risk/stop_loss.py`, with the skipped days logged.
  2. `execution/job_guard.py` added: hard SIGALRM deadline, step trail, socket
     floor, per-step budget. `JobTimeout` derives from BaseException so no
     `except Exception` can swallow it.
  3. Timeouts: yfinance explicit (30s), Alpaca injected into the session (30s, it
     had none at all), Supabase 10s and Resend 15s unchanged.
  4. Both `main()` functions return 3 on timeout and name the step.
  5. The advisory stop overlay now has its own 120s budget, so a display-only step
     can no longer stop the run writing a fresh signal.
  Files: `risk/stop_loss.py`, `execution/job_guard.py`, `execution/alpaca_paper.py`,
  `signals/load.py`, `scripts/run_signal.py`, `scripts/run_execution.py`

- [x] **T4: Test that a hung call times out and exits non-zero**

  Acceptance: a hung call times out; the job exits non-zero.
  Result: 11 tests in `tests/test_job_guard.py`, plus a regression test in
  `tests/test_stop_loss.py`. Covers a real blocking socket read, the
  BaseException property, step logging, the socket restore, a hung signal job
  returning 3 with the step named, the same for the execution job, an over-budget
  overlay being skipped, and a negative control so the step-trail tests cannot
  pass vacuously.
  Files: `tests/test_job_guard.py`, `tests/test_stop_loss.py`

- [x] **T5: Re-run safety assessment**

  Say whether it is safe to re-run the signal for 2026-09-24 so the 14:31 UTC
  execution gets a fresh signal, and whether that affects the execution job's
  idempotency.

  Acceptance: a clear yes/no with the reasoning and the side effects named.
  Result: **Not yet, the fix is not deployed.** Once deployed, yes, and it would
  write `as_of_date = 2026-09-24`. The execution job's idempotency is NOT affected
  (different `job_name`). The catch is that the signal job's own idempotency IS:
  a manual run records `cron_runs(run_signal, 2026-09-25)` and would make that
  evening's scheduled signal run skip itself, leaving the 2026-09-26 execution
  with a one day stale signal. Fix is to clear that row afterwards or to run after
  21:30 UTC.
  Files: `sprints/v9.3/notes.md`

- [ ] **T6: Deploy**

  Push, then confirm on a real run that the step trail appears and that the
  overlay step completes inside its budget.

  Not done: I did not push, because deploying cron code was not requested this
  round and it is the kind of change I want a yes on. The signal cron fires at
  21:30 UTC and will hang again on the unfixed code if it is not deployed first.
