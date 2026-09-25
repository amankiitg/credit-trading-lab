"""Tests for execution/job_guard.py and the cron jobs' hang behaviour (v9.3).

Motivating incident: the 2026-09-24 signal run logged its proposed weights at
21:31 UTC and then produced nothing for over four hours until it was cancelled by
hand. Nothing wrote, and there was no way to tell where it had stopped.

These tests pin the properties that make that impossible:

  - a genuinely blocking call is cut off by the deadline, including a blocking
    socket read with no socket-level timeout set;
  - a step trail exists, so the last unmatched 'begin' names the hang point;
  - a hung job exits non-zero, never 0, and names the step it died in;
  - the deadline cannot be swallowed by the ordinary `except Exception` blocks
    that are everywhere in these jobs.
"""

from __future__ import annotations

import logging
import socket
import time

import pytest

from execution.job_guard import (
    DEFAULT_NETWORK_TIMEOUT_SECS,
    JobTimeout,
    current_step,
    job_guards,
    step,
    step_budget,
)


def _blocking_recv() -> None:
    """Block forever on a real socket read.

    A listening socket that never calls accept() means connect() succeeds and
    recv() waits indefinitely: a genuine blocking syscall with no network
    dependency. It is the case that a socket-level default timeout cannot help
    with, which is exactly why the SIGALRM deadline is the guarantee and the
    socket floor is only a best effort.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(server.getsockname())
    client.recv(1)  # never returns


# ---------------------------------------------------------------- the guarantee

def test_blocking_socket_read_is_cut_off_by_the_deadline() -> None:
    """A hung call must be interrupted, not waited on."""
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(None)  # a true blocking read, not a socket timeout
    started = time.monotonic()
    try:
        with pytest.raises(JobTimeout):
            with job_guards(1, logging.getLogger("test.guard")):
                _blocking_recv()
    finally:
        socket.setdefaulttimeout(previous)

    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"deadline took {elapsed:.1f}s, expected about 1s"


def test_job_timeout_is_not_caught_by_generic_exception_handling() -> None:
    """The deadline must survive the `except Exception` blocks that are everywhere.

    The yfinance retry loop, the advisory stop-overlay guard, get_live_nav,
    stop_states writing and dust cleanup all catch Exception. If the deadline
    were an Exception, one of those would swallow it, the job would carry on with
    its alarm already spent, and a timed-out run would report success.
    """
    swallowed = None
    try:
        with job_guards(1, logging.getLogger("test.swallow")):
            try:
                time.sleep(30)
            except Exception as exc:  # noqa: BLE001 -- this is the thing under test
                swallowed = exc
    except JobTimeout:
        pass

    assert swallowed is None, (
        f"a generic handler swallowed the deadline: {swallowed!r}"
    )


def test_job_guards_restores_the_socket_timeout() -> None:
    """Global socket state must not be left altered."""
    original = socket.getdefaulttimeout()
    try:
        with job_guards(60, logging.getLogger("test.restore")):
            assert socket.getdefaulttimeout() == DEFAULT_NETWORK_TIMEOUT_SECS
        assert socket.getdefaulttimeout() == original
    finally:
        socket.setdefaulttimeout(original)


# ---------------------------------------------------------------- the step trail

def test_step_logs_begin_and_done(caplog) -> None:
    log = logging.getLogger("test.step.ok")
    with caplog.at_level(logging.INFO, logger="test.step.ok"):
        with step("3 refresh closes", log):
            pass

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("STEP begin: 3 refresh closes") for m in messages), messages
    assert any(m.startswith("STEP done in") and "3 refresh closes" in m
               for m in messages), messages


def test_step_logs_failure_and_reraises(caplog) -> None:
    log = logging.getLogger("test.step.fail")
    with caplog.at_level(logging.ERROR, logger="test.step.fail"):
        with pytest.raises(ValueError, match="boom"):
            with step("7b shortability pre-check", log):
                raise ValueError("boom")

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "STEP FAILED" in m and "7b shortability pre-check" in m and "boom" in m
        for m in messages
    ), messages


def test_current_step_survives_unwinding() -> None:
    """After a timeout unwinds, the step name must still be readable.

    A context manager that reset the name on the way out would leave the error
    message pointing at nothing, which is the opposite of the point.
    """
    with pytest.raises(ValueError):
        with step("5 write signal settings to Supabase", logging.getLogger("test.cur")):
            raise ValueError("x")

    assert current_step() == "5 write signal settings to Supabase"


# ---------------------------------------------------------------- job level

def test_signal_job_exits_nonzero_when_a_step_hangs(monkeypatch, caplog) -> None:
    """Task item 4: a hung call times out and the job exits non-zero."""
    import execution.calendar_utils as cal
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: time.sleep(300))

    with caplog.at_level(logging.INFO):
        code = run_signal.main(max_secs=1)

    assert code == 3, f"a hung signal job must exit non-zero, got {code}"
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "run_signal TIMED OUT" in combined
    assert "3 refresh closes from yfinance" in combined, (
        "the timeout must name the step it died in"
    )


def test_execution_job_exits_nonzero_when_a_step_hangs(monkeypatch, caplog) -> None:
    """The same guarantee for the execution job, on a Supabase read."""
    import dashboard.supabase_client as sb
    import execution.calendar_utils as cal
    import scripts.run_execution as run_exec

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(sb, "get_setting", lambda key: time.sleep(300))

    with caplog.at_level(logging.INFO):
        code = run_exec.main(max_secs=1)

    assert code == 3, f"a hung execution job must exit non-zero, got {code}"
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "run_execution TIMED OUT" in combined
    assert "3 load stored signal from Supabase" in combined


def test_healthy_steps_leave_no_unmatched_begin(caplog) -> None:
    """Negative control: when nothing hangs, the trail closes cleanly.

    Without this, the two tests above would pass just as well if every job
    logged a begin and never a done.
    """
    log = logging.getLogger("test.trail.ok")
    with caplog.at_level(logging.INFO, logger="test.trail.ok"):
        with job_guards(60, log):
            with step("1 first", log):
                pass
            with step("2 second", log):
                pass

    messages = [r.getMessage() for r in caplog.records]
    begins = [m for m in messages if m.startswith("STEP begin:")]
    dones = [m for m in messages if m.startswith("STEP done in")]
    assert len(begins) == 2
    assert len(dones) == 2, messages


# ---------------------------------------------------------------- per-step budget

def test_step_budget_restores_the_job_deadline() -> None:
    """A step budget must shorten the deadline, not consume it.

    SIGALRM has one timer, so arming a per-step budget replaces the job's. If the
    remainder were not re-armed, an optional step could silently spend the whole
    job guarantee, which is the bug this guards against.
    """
    log = logging.getLogger("test.rearm")
    started = time.monotonic()

    with pytest.raises(JobTimeout):
        with job_guards(3, log):
            try:
                with step_budget(1, log):
                    time.sleep(30)
            except JobTimeout:
                pass  # optional step abandoned on purpose, as designed
            time.sleep(30)  # the JOB deadline must still be armed here

    elapsed = time.monotonic() - started
    assert elapsed < 10.0, (
        f"the job deadline was lost after a step budget expired ({elapsed:.1f}s)"
    )


def test_signal_job_skips_an_over_budget_advisory_overlay(monkeypatch, caplog) -> None:
    """The advisory overlay must never stop the run from writing a fresh signal.

    The v9.1 stop ladder is display-only (its gate was REJECTED, weights are never
    modified), so an over-budget or broken overlay has to be skipped, not fatal.
    """
    import execution.calendar_utils as cal
    import risk.stop_loss as sl
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(sl, "compute_episodes", lambda *a, **k: time.sleep(300))
    monkeypatch.setattr(run_signal, "OVERLAY_MAX_SECS", 1.0)

    with caplog.at_level(logging.INFO):
        code = run_signal.main(max_secs=120)

    messages = [r.getMessage() for r in caplog.records]
    assert any("stop overlay exceeded its" in m for m in messages), messages
    assert any("5 write signal settings to Supabase" in m and "begin" in m
               for m in messages), (
        "the run must continue past the overlay to the signal write"
    )
    assert code != 3, "the job deadline must not have fired: only the step did"
