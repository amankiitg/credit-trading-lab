"""One plain-text summary email per cron run -- sprint v9.5.

Both cron jobs send exactly one email at the end of every run, whatever the
outcome. That is the point of the try/finally in `execute_job`: there is a single
place where a job ends, so there is a single place where the email is sent, and a
run cannot pass silently because a `return` happened to be taken before the send.
The 2026-09-24 incidents are why. A signal run spun for over four hours, and a
missing session shipped a book whose five weights had all moved by 0.765, and both
were visible only in the logs of a job nobody was watching.

Three rules, all deliberate:

  - **Nothing here may affect the run.** `send_summary` catches everything,
    including a failing import, logs at WARNING and returns. The exit code is
    whatever the job decided and never something this module chose.
  - **No email in a dry run.** A rehearsal should not produce a daily email, and
    an operator who runs one repeatedly should not be mailed each time. The skip
    is logged.
  - **The subject carries the outcome**, so the inbox itself is the alert:
    `[OK]`, `[SKIP]` or `[FAIL]`, then the job name, then the date.

`RunSummary` is a plain accumulator. Each job fills in what it has as it goes, so
an early return or a crash still reports the part that actually happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

logger = logging.getLogger("daily_summary")

OK = "[OK]"
SKIP = "[SKIP]"
FAIL = "[FAIL]"

# The stale-signal skip in run_execution. A deliberate refusal to trade, so it
# reads as a skip rather than a failure.
SKIP_EXIT_CODES = frozenset({4})

# A job deadline. Both cron mains already returned this.
TIMEOUT_EXIT_CODE = 3
UNEXPECTED_EXIT_CODE = 1


@dataclass
class RunSummary:
    """What one job run wants to report. Every field is optional."""

    job: str
    run_date: str
    exit_code: int = 0
    status: str | None = None       # explicit mark; None means derive from exit_code
    reason: str | None = None
    signal_as_of: str | None = None
    nav_frozen: float | None = None
    nav_live: float | None = None
    pnl_net: float | None = None
    turnover_cost: float | None = None
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    gap_fills: list[str] = field(default_factory=list)
    data_check: str | None = None
    extra: list[str] = field(default_factory=list)

    def mark_skip(self, reason: str) -> None:
        """Record a deliberate no-op so the subject reads [SKIP] and not [OK]."""
        self.status = SKIP
        self.reason = reason


def status_for(summary: RunSummary) -> str:
    """[OK], [SKIP] or [FAIL]. An explicit mark wins over the exit code.

    The exit code alone cannot say enough: run_signal exits 1 both for a
    data-quality block, which is a deliberate refusal to write, and for a failed
    Supabase write, which is a real failure. The job knows which one it was and
    marks it. Everything else falls back to the code.
    """
    if summary.status:
        return summary.status
    if summary.exit_code == 0:
        return OK
    if summary.exit_code in SKIP_EXIT_CODES:
        return SKIP
    return FAIL


def subject_for(summary: RunSummary) -> str:
    return f"{status_for(summary)} {summary.job} {summary.run_date}"


def body_for(summary: RunSummary, last_step: str | None = None) -> str:
    """Plain text, a few lines, and only the fields the job actually filled in."""
    lines = [
        f"{summary.job} finished on {summary.run_date} with exit code "
        f"{summary.exit_code} ({status_for(summary)})."
    ]
    if summary.reason:
        lines.append(f"Reason: {summary.reason}")
    if summary.signal_as_of:
        lines.append(f"Signal as_of_date: {summary.signal_as_of}")
    if summary.data_check:
        lines.append(f"Data check: {summary.data_check}")
    if summary.gap_fills:
        lines.append("Gap fills: " + "; ".join(summary.gap_fills))
    if summary.nav_frozen is not None:
        nav = f"NAV frozen for sizing: ${summary.nav_frozen:,.2f}"
        if summary.nav_live is not None:
            nav += f"; live Alpaca: ${summary.nav_live:,.2f}"
        lines.append(nav)
    if summary.pnl_net is not None:
        pnl = f"Day P&L: net ${summary.pnl_net:,.2f}"
        if summary.turnover_cost is not None:
            pnl += f" (turnover cost ${summary.turnover_cost:,.2f})"
        lines.append(pnl)

    lines.append(
        f"Orders: {len(summary.filled)} filled, {len(summary.skipped)} skipped, "
        f"{len(summary.rejected)} rejected"
    )
    lines += [f"  filled: {x}" for x in summary.filled]
    lines += [f"  skipped: {x}" for x in summary.skipped]
    lines += [f"  rejected: {x}" for x in summary.rejected]
    lines += summary.extra

    if status_for(summary) != OK:
        lines.append(f"Last step started: {last_step or '(none)'}")
    return "\n".join(lines)


def send_summary(
    summary: RunSummary,
    *,
    dry_run: bool = False,
    last_step: str | None = None,
) -> bool:
    """Send the one summary email. Never raises, never changes an exit code.

    Returns True only when an email actually went out. A dry run, a missing
    configuration, a Resend failure and a broken import all return False, because
    none of them is the run's problem: requirement is that an alert can never
    affect trading.
    """
    try:
        subject = subject_for(summary)

        if dry_run:
            logger.info(
                "summary email skipped: dry run (would have sent %r)", subject
            )
            return False

        # Imported here, inside the try, for the reason learned on 2026-09-24:
        # execution/alerts.py was in the working tree but not in the repository,
        # so the import raised and took the whole run down with it.
        from execution.alerts import send_alert_email

        body = body_for(summary, last_step=last_step)
        sent = send_alert_email(subject=subject, body=body)
        if not sent:
            logger.warning(
                "summary email NOT sent for %s: send_alert_email returned False "
                "(usually RESEND_API_KEY or a recipient is unset)",
                summary.job,
            )
        return bool(sent)
    except Exception as exc:
        logger.warning(
            "summary email failed (%s: %s) -- ignoring it; the run's exit code is "
            "unchanged",
            type(exc).__name__, exc,
        )
        return False


def _exit_code_for(exc: BaseException) -> int:
    """The code the process would have exited with, for an exception that escaped."""
    if isinstance(exc, SystemExit):
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else UNEXPECTED_EXIT_CODE
    return UNEXPECTED_EXIT_CODE


def execute_job(
    *,
    job: str,
    budget_secs: float,
    body: Callable[[RunSummary], int],
    dry_run: bool = False,
    log: logging.Logger | None = None,
    run_date: str | None = None,
) -> int:
    """Run one job under its deadline and send exactly one summary, always.

    The single exit path. Every way a job can finish passes through the same
    `finally`: a normal return, a deliberate skip, a deadline, and an unexpected
    exception that is re-raised unchanged so its traceback still reaches the
    scheduler.
    """
    from execution.job_guard import JobTimeout, current_step, job_guards

    log = log or logger
    summary = RunSummary(job=job, run_date=run_date or date.today().isoformat())
    exit_code = 0
    try:
        with job_guards(budget_secs, log):
            exit_code = body(summary)
    except JobTimeout as exc:
        log.error(
            "%s TIMED OUT: %s. Last step started: %s",
            job, exc, current_step() or "(none)",
        )
        exit_code = TIMEOUT_EXIT_CODE
        summary.reason = f"timed out: {exc}"
    except BaseException as exc:
        # Unexpected. Behaviour is deliberately unchanged: the exception is
        # re-raised so the traceback and the non-zero exit still happen, but the
        # summary records it first.
        exit_code = _exit_code_for(exc)
        summary.reason = f"unexpected {type(exc).__name__}: {exc}"
        raise
    finally:
        # The guards have already unwound by the time this runs, so cancel_job_
        # deadline has fired and a slow email cannot be cut off by a spent alarm.
        summary.exit_code = exit_code
        send_summary(summary, dry_run=dry_run, last_step=current_step())
    return exit_code
