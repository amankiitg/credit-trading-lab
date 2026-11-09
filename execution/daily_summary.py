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
    # A fail-closed refusal that stopped the job before it could act. Rendered with
    # the [FAILED] prefix so the single most important fact of that run is in the
    # body even when the subject line is only the generic [FAIL] job date.
    failure: str | None = None
    signal_as_of: str | None = None
    nav_frozen: float | None = None
    nav_live: float | None = None
    # The book's result: live Alpaca NAV now minus the live NAV recorded by the
    # previous run. None when there is no previous reading to compare against.
    previous_live_nav: float | None = None
    nav_change: float | None = None
    # Today's trading frictions, reported on their own line because they are a drag
    # on the book's result rather than the result itself.
    turnover_cost: float | None = None
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    gap_fills: list[str] = field(default_factory=list)
    data_check: str | None = None
    # The basis refresh: how many position rows were rewritten and at what NAV, or
    # why it failed. A failed refresh is reported in the body and lifts the subject to
    # at least [SKIP], because a stale sizing basis is a real operational problem that
    # should not be readable only by opening the email.
    basis_positions: int | None = None
    basis_nav: float | None = None
    basis_skipped: str | None = None
    basis_failed: str | None = None
    # The session's book P&L decomposed by sleeve and into carry vs price. Written only
    # when the four sleeves reconciled to the whole-book total.
    sleeve_pnl: dict | None = None
    sleeve_pnl_note: str | None = None
    execution_pnl: dict | None = None
    execution_note: str | None = None
    reconciliation: dict | None = None
    reconciliation_note: str | None = None
    extra: list[str] = field(default_factory=list)

    def mark_skip(self, reason: str) -> None:
        """Record a deliberate no-op so the subject reads [SKIP] and not [OK]."""
        self.status = SKIP
        self.reason = reason

    def record_basis_refresh(self, report: dict) -> None:
        """Record the outcome of execution.snapshot.refresh_snapshot.

        A dry run reports `skipped` but is not a failure, and the two look completely
        different in the email, so the report's own `failed` flag decides which.
        """
        if report.get("failed"):
            self.basis_failed = str(report.get("skipped") or "unknown reason")
            return
        if report.get("skipped"):
            self.basis_skipped = str(report["skipped"])
            return
        self.basis_positions = report.get("tickers")
        self.basis_nav = report.get("nav")

    def record_sleeve_pnl(self, report: dict) -> None:
        """Record the day's sleeve decomposition, or why there is not one.

        Three outcomes and they read differently on purpose. A written day carries its
        numbers. A day with no prior snapshot is not computed, which is the expected
        state on the first run and on any day after a run that wrote no snapshot, and
        is not a failure. A day whose sleeves did not reconcile, or whose write was
        rejected, is FAILED: those are real problems and the numbers are missing
        rather than absent by construction.
        """
        # Independent of whether the decomposition was written: the traded part explains
        # the session either way, and the reconciliation it drives is the only thing that
        # would notice the two disagreeing.
        self.record_execution_pnl(report.get("execution"))
        # The residual the email carries is the PREVIOUS session's. At 17:30 ET the
        # session being measured has no equity close yet (it is stamped tomorrow);
        # the previous session's close is stamped today and is published. Step 8
        # supplies that block whenever it could attempt the measurement.
        self.record_prior_reconciliation(report.get("prior_reconciliation"))

        if report.get("written"):
            self.sleeve_pnl = {
                "trade_date": report.get("trade_date"),
                "prior_date": report.get("prior_date"),
                "sleeves": report.get("sleeves") or {},
                "carry": 0.0 if report.get("carry_total") is None else report["carry_total"],
                "price": 0.0 if report.get("price_total") is None else report["price_total"],
                "book": 0.0 if report.get("book_gross_pnl") is None else report["book_gross_pnl"],
            }
            return

        status = report.get("status")
        reason = report.get("reason") or "no reason recorded"
        if status in ("mismatch", "write_failed", "error"):
            self.sleeve_pnl_note = f"Sleeve P&L FAILED ({status}): {reason}"
        else:
            self.sleeve_pnl_note = f"Sleeve P&L: not computed ({reason})"

    def record_execution_pnl(self, report: dict | None) -> None:
        """The traded part of the session, and the equity reconciliation that follows.

        A gap is reported as a WARNING and nothing more. It is measured against equity
        read at the run, not at the close, so a gap the size of an intraday move is
        expected; refusing the session over it would hide a decomposition that is still
        the best available.
        """
        if not report:
            return
        if report.get("status") != "ok":
            self.execution_note = (
                "Execution P&L: not available "
                f"({report.get('reason') or report.get('status')})"
            )
            return

        self.execution_pnl = {
            "total": report.get("total") or 0.0,
            "fill_count": report.get("fill_count") or 0,
            "by_sleeve": report.get("by_sleeve") or {},
        }
        self.execution_note = report.get("note")

        convention = report.get("convention") or {}
        if convention.get("verdict") == "unknown" and convention.get("reason"):
            # The self-check could not run (too little history, or a failed DB read).
            # Left unsaid it is indistinguishable from a check that passed.
            self.extra.append(
                f"Convention self-check: not available ({convention['reason']})"
            )
        if convention.get("warning"):
            # The anchor's assumption, not the book, has changed. Say so every run until
            # someone looks: a wrong convention reads as a permanent gap that nobody can
            # explain and everybody learns to ignore.
            self.execution_note = (
                f"{convention['warning']}: the naive pairing now explains the trailing "
                f"window better (mean |gap| "
                f"{convention.get('naive_mean_abs', 0.0):.2f} against "
                f"{convention.get('current_mean_abs', 0.0):.2f})."
            )

        reconciliation = report.get("reconciliation")
        if reconciliation:
            self.reconciliation = reconciliation
            if reconciliation.get("warn"):
                self.execution_note = (
                    "WARNING: execution P&L does not reconcile. Pipeline plus execution "
                    f"{reconciliation['expected']:+,.2f} against an equity change of "
                    f"{reconciliation['nav_change']:+,.2f}, a gap of "
                    f"{reconciliation['gap']:+,.2f} dollars. {report.get('note') or ''}"
                ).strip()
        elif report.get("reconciliation_reason"):
            # A quiet reconciliation used to be rendered nowhere at all: the residual
            # only reached the email when it crossed the warning threshold, so a healthy
            # day and a missing measurement looked identical. Say which one it is.
            #
            # "residual" rather than "reconciliation": the measured line above is
            # "pipeline + execution X vs equity change Y (residual Z)", so this is the
            # same word for the same quantity when it cannot be measured -- including
            # the evening case where today's close has not been published yet.
            self.reconciliation_note = (
                "Reconciliation: residual not available "
                f"({report.get('reconciliation_reason')})"
            )

    def record_prior_reconciliation(self, report: dict | None) -> None:
        """The residual for the previous session, which is the one the evening can measure.

        Replaces the current session's "not yet published" note when step 8 attempted
        the measurement: that note is the expected state of every 17:30 ET run and is
        not news once the previous session's residual is on the email. A prior block
        that could not be measured keeps its own named reason, with its date, so a
        blank line can never be read as a quiet day.
        """
        if not report:
            return
        session = report.get("session") or "previous session"
        reconciliation = report.get("reconciliation")
        if reconciliation:
            self.reconciliation = {**reconciliation, "session": session}
            self.reconciliation_note = None
            return
        reason = report.get("reason") or "no reason recorded"
        self.reconciliation = None
        self.reconciliation_note = (
            f"Reconciliation ({session}): residual not available ({reason})"
        )


# Severity order for the subject line. A failed basis refresh lifts a clean run to
# [SKIP], but must never soften a real failure, so the code only ever moves up.
_SEVERITY: dict[str, int] = {OK: 0, SKIP: 1, FAIL: 2}


def _base_status(summary: RunSummary) -> str:
    """The status the run's own outcome earns, before any escalation.

    An explicit mark wins over the exit code: run_signal exits 1 both for a
    data-quality block, which is a deliberate refusal to write, and for a failed
    Supabase write, which is a real failure. The job knows which one it was and marks
    it. Everything else falls back to the code.
    """
    if summary.status:
        return summary.status
    if summary.exit_code == 0:
        return OK
    if summary.exit_code in SKIP_EXIT_CODES:
        return SKIP
    return FAIL


def status_for(summary: RunSummary) -> str:
    """[OK], [SKIP] or [FAIL], with a failed basis refresh escalated to at least [SKIP].

    The refresh is what stops the sizing basis going stale, so failing to refresh it
    is a problem the subject line should carry rather than leaving it in a log line
    nobody reads. Escalation is one-way: a [FAIL] run stays [FAIL].
    """
    status = _base_status(summary)
    if summary.basis_failed and _SEVERITY[status] < _SEVERITY[SKIP]:
        return SKIP
    return status


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
    if summary.failure:
        # The fail-closed refusal, stated in the words the operator acts on.
        lines.append(f"[FAILED] {summary.failure}")
    if summary.signal_as_of:
        lines.append(f"Signal as_of_date: {summary.signal_as_of}")
    if summary.data_check:
        lines.append(f"Data check: {summary.data_check}")
    if summary.gap_fills:
        lines.append("Gap fills: " + "; ".join(summary.gap_fills))

    if summary.basis_failed:
        lines.append(f"basis refresh FAILED: {summary.basis_failed}")
    elif summary.basis_nav is not None:
        lines.append(
            f"basis refreshed: {summary.basis_positions} positions, "
            f"NAV ${summary.basis_nav:,.2f}"
        )
    elif summary.basis_skipped:
        # Reached on a dry run, where no email is sent anyway, but stated rather than
        # omitted so a missing line is never ambiguous.
        lines.append(f"basis refresh: not attempted ({summary.basis_skipped})")

    if summary.nav_frozen is not None:
        nav = f"NAV frozen for sizing: ${summary.nav_frozen:,.2f}"
        if summary.nav_live is not None:
            nav += f"; live Alpaca: ${summary.nav_live:,.2f}"
        lines.append(nav)

    # The book's P&L, as the move in live Alpaca NAV since the previous run. Reported
    # as the headline number rather than the day's simulated costs, because the costs
    # are a drag on the result, not the result. Both are shown, on separate lines.
    if summary.nav_change is None:
        if summary.nav_live is not None and summary.previous_live_nav is None:
            lines.append(
                "Book P&L: unavailable (no previous live NAV on record to compare "
                "against)"
            )
    else:
        lines.append(
            f"Book P&L (live NAV move since previous run): "
            f"${summary.nav_change:+,.2f}"
        )
    if summary.turnover_cost is not None:
        lines.append(f"Turnover cost today: ${summary.turnover_cost:,.2f}")

    # The session's P&L split by sleeve and into carry vs price, on the book held at the
    # previous close. Distinct from the Book P&L line above, which is the raw equity
    # move and includes intraday marks and the trades themselves.
    if summary.sleeve_pnl is not None:
        sleeves = ", ".join(
            f"{name} ${value:+,.2f}"
            for name, value in summary.sleeve_pnl["sleeves"].items()
        )
        lines.append(
            f"Sleeve P&L {summary.sleeve_pnl['trade_date']} on the "
            f"{summary.sleeve_pnl['prior_date']} book: {sleeves}; "
            f"carry ${summary.sleeve_pnl['carry']:+,.2f}, "
            f"price ${summary.sleeve_pnl['price']:+,.2f}, "
            f"book ${summary.sleeve_pnl['book']:+,.2f}"
        )
    elif summary.sleeve_pnl_note:
        lines.append(summary.sleeve_pnl_note)

    # What the decomposition above cannot see: the shares traded during the session, and
    # whether the whole thing adds up to the move in account equity.
    if summary.execution_pnl is not None:
        traded = ", ".join(
            f"{name} {value:+,.2f}"
            for name, value in summary.execution_pnl["by_sleeve"].items()
            if abs(float(value)) >= 0.005
        )
        lines.append(
            f"Execution P&L: {summary.execution_pnl['fill_count']} fills, "
            f"{summary.execution_pnl['total']:+,.2f} total"
            + (f" ({traded})" if traded else "")
        )
    if summary.execution_note:
        lines.append(summary.execution_note)

    # The residual is the whole point of the reconciliation: it is the only number that
    # says whether the decomposition explains the account. Print it on every healthy
    # run, not only when it is large enough to warn -- a residual that is never shown
    # cannot be watched, and "no line" must never be read as "nothing to report".
    if summary.reconciliation is not None:
        session = summary.reconciliation.get("session")
        if session:
            # The previous session's residual. One line, dated, every run: the
            # session it belongs to is not the run date, and an undated number
            # would read as today's.
            gap = float(summary.reconciliation["gap"])
            money = f"-${abs(gap):,.2f}" if gap < 0 else f"${gap:,.2f}"
            lines.append(f"Reconciliation ({session}): residual {money}")
        elif not summary.reconciliation.get("warn"):
            lines.append(
                "Reconciliation: pipeline + execution "
                f"{summary.reconciliation['expected']:+,.2f} vs equity change "
                f"{summary.reconciliation['nav_change']:+,.2f} "
                f"(residual {summary.reconciliation['gap']:+,.2f})"
            )
    elif summary.reconciliation_note:
        lines.append(summary.reconciliation_note)

    lines.append(
        f"Orders: {len(summary.filled)} filled, {len(summary.skipped)} skipped, "
        f"{len(summary.rejected)} rejected"
    )
    lines += [f"  filled: {x}" for x in summary.filled]
    lines += [f"  skipped: {x}" for x in summary.skipped]
    lines += [f"  rejected: {x}" for x in summary.rejected]
    lines += summary.extra

    # Deliberately the base status, not the escalated one: a clean run whose basis
    # refresh failed should report the refresh problem in its own line rather than
    # implying the run itself stopped partway through a step.
    if _base_status(summary) != OK:
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
