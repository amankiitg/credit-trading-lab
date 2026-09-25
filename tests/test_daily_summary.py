"""Tests for execution/daily_summary.py -- one summary email per cron run.

The contract is narrow and worth pinning precisely:

  - every outcome produces exactly one email, including a crash and a deadline;
  - the subject says which outcome it was;
  - nothing about sending can change the run's exit code.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from execution.daily_summary import (
    FAIL,
    OK,
    SKIP,
    RunSummary,
    body_for,
    execute_job,
    send_summary,
    status_for,
    subject_for,
)


def _summary(**kw) -> RunSummary:
    base = dict(job="run_execution", run_date="2026-09-24")
    base.update(kw)
    return RunSummary(**base)


# ------------------------------------------------------------------ status

def test_exit_zero_is_ok() -> None:
    assert status_for(_summary(exit_code=0)) == OK


def test_stale_skip_exit_is_skip_not_failure() -> None:
    """Exit 4 is a deliberate refusal to trade, so it must not read as a failure."""
    assert status_for(_summary(exit_code=4)) == SKIP


@pytest.mark.parametrize("code", [1, 2, 3, 5, 137])
def test_other_non_zero_is_failure(code: int) -> None:
    assert status_for(_summary(exit_code=code)) == FAIL


def test_an_explicit_mark_wins_over_the_exit_code() -> None:
    """The overloaded case: run_signal exits 1 for a block and for a real failure.

    A data-quality block is a deliberate refusal, so the job marks it and the
    subject says [SKIP] even though the code is 1.
    """
    s = _summary(exit_code=1)
    assert status_for(s) == FAIL, "unmarked, exit 1 is a failure"
    s.mark_skip("data-quality gate blocked")
    assert status_for(s) == SKIP


def test_subject_carries_status_job_and_date() -> None:
    assert subject_for(_summary(exit_code=0)) == "[OK] run_execution 2026-09-24"
    assert subject_for(_summary(exit_code=4)) == "[SKIP] run_execution 2026-09-24"
    assert (
        subject_for(_summary(job="run_signal", exit_code=3))
        == "[FAIL] run_signal 2026-09-24"
    )


# ------------------------------------------------------------------ body

def test_body_reports_the_execution_fields() -> None:
    s = _summary(
        exit_code=0,
        signal_as_of="2026-09-23",
        nav_frozen=101013.45,
        nav_live=101089.45,
        pnl_net=-12.34,
        turnover_cost=4.56,
        filled=["SPY buy_to_open $5,000.00"],
        skipped=["LQD sell_to_open GUARD_SKIPPED_NOT_SHORTABLE"],
        rejected=["GLD sell_to_open REASON_NOT_SOON"],
    )
    body = body_for(s, last_step="12 record run")

    assert "exit code 0" in body
    assert "Signal as_of_date: 2026-09-23" in body
    assert "NAV frozen for sizing: $101,013.45" in body
    assert "live Alpaca: $101,089.45" in body
    assert "Day P&L: net $-12.34" in body
    assert "Orders: 1 filled, 1 skipped, 1 rejected" in body
    assert "filled: SPY buy_to_open $5,000.00" in body
    assert "skipped: LQD sell_to_open GUARD_SKIPPED_NOT_SHORTABLE" in body
    assert "rejected: GLD sell_to_open REASON_NOT_SOON" in body
    assert "Last step started" not in body, "an OK run needs no failure detail"


def test_body_reports_the_signal_fields() -> None:
    s = _summary(
        job="run_signal",
        exit_code=0,
        signal_as_of="2026-09-23",
        data_check="passed",
        gap_fills=["EFA 2026-09-23 (source=alpaca_iex)"],
    )
    body = body_for(s)

    assert "Data check: passed" in body
    assert "Gap fills: EFA 2026-09-23 (source=alpaca_iex)" in body


def test_body_reports_a_gate_block_with_its_reason() -> None:
    s = _summary(job="run_signal", exit_code=1, signal_as_of="2026-09-23")
    s.data_check = "BLOCKED (5 problem(s)): EFA missing 2026-09-23"
    s.mark_skip("data-quality gate blocked the signal for 2026-09-23")

    body = body_for(s, last_step="4c data-quality gate")

    assert "Data check: BLOCKED (5 problem(s)): EFA missing 2026-09-23" in body
    assert "Reason: data-quality gate blocked the signal for 2026-09-23" in body
    assert "Last step started: 4c data-quality gate" in body


def test_body_gives_the_exit_code_and_last_step_on_a_failure() -> None:
    s = _summary(exit_code=3, reason="timed out: job exceeded its hard deadline")

    body = body_for(s, last_step="3 refresh closes from yfinance")

    assert "exit code 3" in body
    assert "Last step started: 3 refresh closes from yfinance" in body


def test_body_says_so_when_no_step_ever_started() -> None:
    body = body_for(_summary(exit_code=1), last_step=None)
    assert "Last step started: (none)" in body


def test_body_omits_fields_the_job_never_filled_in() -> None:
    """An early skip has almost nothing to report, and must not print "None"."""
    s = _summary(exit_code=4)
    s.mark_skip("signal is 3 NYSE sessions old")
    body = body_for(s, last_step="3c signal staleness guard")

    assert "None" not in body
    assert "NAV" not in body
    assert "P&L" not in body
    assert "Reason: signal is 3 NYSE sessions old" in body


# ------------------------------------------------------------------ sending

def test_send_summary_sends_one_email(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append((subject, body)) or True,
    )

    assert send_summary(_summary()) is True
    assert len(sent) == 1
    assert sent[0][0] == "[OK] run_execution 2026-09-24"


def test_send_summary_is_silent_on_a_dry_run(monkeypatch, caplog) -> None:
    """Requirement 4: a rehearsal must not email, and must say that it did not."""
    sent = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append(subject) or True,
    )

    with caplog.at_level(logging.INFO):
        assert send_summary(_summary(), dry_run=True) is False

    assert sent == [], "a dry run must send nothing"
    assert "summary email skipped: dry run" in caplog.text
    assert "[OK] run_execution 2026-09-24" in caplog.text


def test_send_summary_returns_false_when_the_sender_returns_false(
    monkeypatch, caplog
) -> None:
    """An unconfigured sender is a skip, logged at WARNING, not an exception."""
    monkeypatch.setattr(
        "execution.alerts.send_alert_email", lambda subject, body: False
    )

    with caplog.at_level(logging.WARNING):
        assert send_summary(_summary()) is False

    assert "summary email NOT sent" in caplog.text


def test_send_summary_swallows_a_raising_sender(monkeypatch, caplog) -> None:
    def _boom(subject, body):
        raise RuntimeError("resend returned 500")

    monkeypatch.setattr("execution.alerts.send_alert_email", _boom)

    with caplog.at_level(logging.WARNING):
        assert send_summary(_summary()) is False

    assert "summary email failed (RuntimeError: resend returned 500)" in caplog.text


def test_send_summary_swallows_a_missing_alerts_module(monkeypatch, caplog) -> None:
    """The 2026-09-24 lesson: a missing module must not take the run down."""
    import sys

    monkeypatch.setitem(sys.modules, "execution.alerts", None)

    with caplog.at_level(logging.WARNING):
        assert send_summary(_summary()) is False

    assert "summary email failed" in caplog.text


# ------------------------------------------------------------------ the exit path

def test_execute_job_sends_one_ok_email() -> None:
    sent = []

    def _body(summary: RunSummary) -> int:
        summary.signal_as_of = "2026-09-23"
        return 0

    code = execute_job(
        job="run_execution",
        budget_secs=30,
        body=_body,
        run_date="2026-09-24",
    )

    assert code == 0


def test_execute_job_returns_the_jobs_code_and_one_email(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append(subject) or True,
    )

    code = execute_job(
        job="run_execution",
        budget_secs=30,
        body=lambda summary: 4,
        run_date="2026-09-24",
    )

    assert code == 4
    assert sent == ["[SKIP] run_execution 2026-09-24"]


def test_execute_job_emails_a_failure_and_reraises(monkeypatch) -> None:
    """An unexpected exception must still be reported, and still propagate."""
    sent = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append((subject, body)) or True,
    )

    def _explode(summary: RunSummary) -> int:
        raise ValueError("edge case in the signal pipeline")

    with pytest.raises(ValueError):
        execute_job(
            job="run_signal",
            budget_secs=30,
            body=_explode,
            run_date="2026-09-24",
        )

    assert len(sent) == 1
    subject, body = sent[0]
    assert subject == "[FAIL] run_signal 2026-09-24"
    assert "exit code 1" in body
    assert "unexpected ValueError: edge case in the signal pipeline" in body


def test_execute_job_sends_nothing_on_a_dry_run(monkeypatch, caplog) -> None:
    sent = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append(subject) or True,
    )

    with caplog.at_level(logging.INFO):
        code = execute_job(
            job="run_execution",
            budget_secs=30,
            body=lambda summary: 0,
            dry_run=True,
            run_date="2026-09-24",
        )

    assert code == 0
    assert sent == []
    assert "summary email skipped: dry run" in caplog.text


def test_a_failing_email_does_not_change_the_exit_code(monkeypatch, caplog) -> None:
    """Requirement 3, at the exit-path level: the job's verdict is the job's."""
    def _boom(subject, body):
        raise RuntimeError("resend unreachable")

    monkeypatch.setattr("execution.alerts.send_alert_email", _boom)

    with caplog.at_level(logging.WARNING):
        code = execute_job(
            job="run_execution",
            budget_secs=30,
            body=lambda summary: 2,
            run_date="2026-09-24",
        )

    assert code == 2, "the halt code must survive a failed summary email"
    assert "summary email failed" in caplog.text


def test_a_failing_email_does_not_mask_an_unexpected_exception(monkeypatch) -> None:
    """The summary must not replace the original traceback with its own."""
    def _boom(subject, body):
        raise RuntimeError("resend unreachable")

    monkeypatch.setattr("execution.alerts.send_alert_email", _boom)

    def _explode(summary: RunSummary) -> int:
        raise KeyError("the real problem")

    with pytest.raises(KeyError):
        execute_job(
            job="run_execution",
            budget_secs=30,
            body=_explode,
            run_date="2026-09-24",
        )


# ------------------------------------------------- real jobs, real outcomes

def test_a_hung_job_emails_fail(monkeypatch, caplog) -> None:
    """Requirement 5: a real deadline produces one [FAIL] email, end to end.

    Drives run_signal.main() for real, with ingest hanging, so the JobTimeout
    path is exercised rather than simulated. The email must carry the exit code
    and the step the job died in.
    """
    import logging
    import time

    import execution.calendar_utils as cal
    import signals.etf_universe as etf
    import scripts.run_signal as run_signal

    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: time.sleep(300))

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append((subject, body)) or True,
    )

    with caplog.at_level(logging.INFO):
        code = run_signal.main(max_secs=1)

    assert code == 3, "a hung job still exits 3"
    assert len(sent) == 1, "a timeout must still produce exactly one email"

    subject, body = sent[0]
    assert subject == f"[FAIL] run_signal {date.today().isoformat()}"
    assert "exit code 3" in body
    assert "timed out" in body
    assert "Last step started: 3 refresh closes from yfinance" in body


def test_a_skip_through_a_real_job_emails_skip(monkeypatch, caplog) -> None:
    """A closed market is [SKIP]: the job stopped on purpose, on a real path."""
    import logging

    import execution.calendar_utils as cal
    import scripts.run_signal as run_signal

    monkeypatch.setattr(cal, "is_trading_day", lambda d: False)

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "execution.alerts.send_alert_email",
        lambda subject, body: sent.append((subject, body)) or True,
    )

    with caplog.at_level(logging.INFO):
        code = run_signal.main(max_secs=30)

    assert code == 0
    assert len(sent) == 1
    subject, body = sent[0]
    assert subject == f"[SKIP] run_signal {date.today().isoformat()}"
    assert "NYSE closed" in body
    assert "exit code 0" in body
