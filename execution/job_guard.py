"""Job deadlines and step logging for the cron jobs -- sprint v9.3.

A cron job that hangs forever is worse than one that fails outright: it holds
the scheduler slot, writes nothing, and stays invisible until someone notices a
stale dashboard hours later. The 2026-09-24 signal run did exactly that, sitting
silent for over four hours with no way to tell where it had stopped.

This module gives a job three things:

  1. A hard deadline, `install_job_deadline`. SIGALRM is delivered on the main
     thread and the handler raises, which under PEP 475 interrupts a blocking
     syscall instead of letting Python retry it. That is what makes this a real
     guarantee even for a library that offers no timeout at all, such as
     alpaca-py. It also bounds a pure-CPU spin, which no socket timeout can.
  2. A step trail, `step`. Every step logs 'begin' then 'done' (or 'FAILED'),
     and the step in flight is remembered so the timeout message can name it.
     The last unmatched 'begin' is the point where the job stopped.
  3. A socket-level floor, `install_socket_timeout`, for libraries that pass no
     timeout of their own.

Nothing here is specific to one job; both cron scripts use it.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from contextlib import contextmanager

logger = logging.getLogger("job_guard")


class JobTimeout(BaseException):
    """Raised when a job exceeds its hard deadline.

    Derives from BaseException, not Exception, for the same reason
    KeyboardInterrupt does. Ordinary error handling is everywhere in these jobs
    (the yfinance retry loop, the advisory stop-overlay guard, get_live_nav,
    stop_states writing, dust cleanup) and all of it catches Exception. If the
    deadline were an Exception, one of those blocks would swallow it, the job
    would carry on with its alarm already spent, and a timed-out run would go on
    to write state and report success. BaseException makes that impossible by
    accident.
    """


# Env-overridable so the values can be tuned per Render service without a code
# change. NETWORK_TIMEOUT_SECS applies per call; JOB_MAX_SECS applies per run.
DEFAULT_NETWORK_TIMEOUT_SECS: float = float(
    os.environ.get("NETWORK_TIMEOUT_SECS", "30")
)

# The most recently started step, for the timeout message. Deliberately never
# cleared: by the time a timeout unwinds the stack the step's own context
# manager has already exited, so a value that was reset would leave the error
# message pointing at nothing. Module state is needed because the SIGALRM
# handler has no other way to know where it fired.
_last_step_started: str = ""

# Monotonic expiry of the job deadline, so a per-step budget can shorten it and
# then restore the remainder. 0.0 means no deadline is armed.
_job_deadline_at: float = 0.0


def current_step() -> str:
    """Name of the most recently started step, or "" before the first step."""
    return _last_step_started


def install_socket_timeout(seconds: float = DEFAULT_NETWORK_TIMEOUT_SECS) -> float | None:
    """Set the process-wide default socket timeout, returning the previous value.

    A floor, not the guarantee: libraries that pass an explicit `timeout=None`
    to urllib3 bypass this. The SIGALRM deadline is what actually bounds them.
    Callers should restore the returned value when the job ends, so a long-lived
    process (or a test run) is not left with altered global state.
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    return previous


def restore_socket_timeout(previous: float | None) -> None:
    """Undo install_socket_timeout()."""
    try:
        socket.setdefaulttimeout(previous)
    except (TypeError, ValueError):
        pass


def install_job_deadline(seconds: float, log: logging.Logger | None = None) -> bool:
    """Arm a hard deadline, returning True when armed.

    Returns False, rather than raising, when SIGALRM is unavailable (for example
    off the main thread). The caller logs the outcome so a missing deadline is
    visible instead of assumed.
    """
    global _job_deadline_at
    log = log or logger
    if seconds is None or seconds <= 0:
        log.warning("job deadline DISABLED (seconds=%s)", seconds)
        return False
    try:
        signal.signal(signal.SIGALRM, _raise_job_timeout)
        signal.alarm(max(1, int(round(seconds))))
    except (ValueError, AttributeError, OSError) as exc:
        log.warning("could not arm a %.0fs job deadline: %s", seconds, exc)
        return False
    _job_deadline_at = time.monotonic() + seconds
    return True


def remaining_job_secs() -> float | None:
    """Seconds left on the job deadline, or None when none is armed."""
    if _job_deadline_at <= 0:
        return None
    return max(0.0, _job_deadline_at - time.monotonic())


@contextmanager
def step_budget(seconds: float, log: logging.Logger | None = None):
    """Bound one step with its own shorter deadline, then restore the job's.

    SIGALRM has a single timer, so arming here replaces the job deadline; the
    job's remaining budget is re-armed on the way out. Use this for OPTIONAL
    work only, where the caller is willing to catch JobTimeout and carry on
    without it. The advisory stop overlay is the motivating case: it must never
    be able to stop the run from writing a fresh signal.
    """
    log = log or logger
    remaining = remaining_job_secs()
    budget = seconds if remaining is None else min(seconds, remaining)
    try:
        signal.signal(signal.SIGALRM, _raise_job_timeout)
        signal.alarm(max(1, int(round(budget))))
    except (ValueError, AttributeError, OSError) as exc:
        log.warning("could not arm a %.0fs step budget: %s", budget, exc)
        yield
        return
    try:
        yield
    finally:
        left = remaining_job_secs()
        try:
            signal.alarm(0 if left is None or left <= 0 else max(1, int(round(left))))
        except (ValueError, AttributeError, OSError):
            pass


def cancel_job_deadline() -> None:
    """Disarm the deadline, so a long-lived interpreter is not left armed."""
    try:
        signal.alarm(0)
    except (ValueError, AttributeError, OSError):
        pass


def _raise_job_timeout(signum, frame) -> None:
    raise JobTimeout(
        f"job exceeded its hard deadline while in step: {current_step() or '(none)'}"
    )


@contextmanager
def job_guards(
    max_secs: float,
    log: logging.Logger | None = None,
    network_secs: float | None = None,
):
    """Install the socket floor and the hard deadline, and always restore them.

    Outermost context for a cron entry point:

        try:
            with job_guards(budget, logger):
                return _run()
        except JobTimeout as exc:
            ...
    """
    log = log or logger
    net = DEFAULT_NETWORK_TIMEOUT_SECS if network_secs is None else network_secs
    previous_socket = install_socket_timeout(net)
    armed = install_job_deadline(max_secs, log)
    log.info(
        "guards: per-call network timeout %.0fs; job deadline %s",
        net,
        f"{max_secs:.0f}s (armed)" if armed else "NOT ARMED",
    )
    try:
        yield
    finally:
        cancel_job_deadline()
        restore_socket_timeout(previous_socket)


@contextmanager
def step(name: str, log: logging.Logger | None = None):
    """Log a step's begin and end, so a hang can be located from the log alone.

    Emits 'STEP begin: <name>', then either 'STEP done in Xs: <name>' or
    'STEP FAILED after Xs: <name> (<type>: <detail>)'. The exception is always
    re-raised, so wrapping a step never swallows a failure.
    """
    global _last_step_started
    log = log or logger
    _last_step_started = name
    started = time.monotonic()
    log.info("STEP begin: %s", name)
    try:
        yield
    except BaseException as exc:
        log.error(
            "STEP FAILED after %.1fs: %s (%s: %s)",
            time.monotonic() - started, name, type(exc).__name__, exc,
        )
        raise
    else:
        log.info("STEP done in %.1fs: %s", time.monotonic() - started, name)
