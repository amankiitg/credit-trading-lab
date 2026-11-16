"""The traded part of a session's P&L: shares traded x (fill price - prior close).

The sleeve decomposition measures the book that was HELD into a session. It is blind to
trades by construction, and that blindness is a real error rather than a rounding one:

  - buy 100 at 10:00 on a day the fund closes 1% up, and the position snapshot at the
    close shows the shares. The sleeve row books (close - prior close) on them for the
    whole session, but the account only earned that move from the fill price onward.
  - sell a position at noon and the snapshot never shows it at all, so the sleeve row
    misses a P&L the account did earn.

The missing piece is shares traded x (fill price - prior close), summed over the session's
fills. It cannot be reconstructed from stored data -- the snapshot is end of day, and the
close is one number -- so it is read from the broker, which records every fill.

Nothing here places, sizes or cancels an order: it reads the activity history and does
arithmetic on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

# The session boundary is New York's, not UTC's. A US equity session runs 14:30-21:00
# UTC, so regular-hours fills agree either way -- but the book's timestamps must not be
# read as if the trading day ended at midnight UTC: an after-hours fill at 20:30 ET is
# 01:30 UTC the NEXT day, and the UTC read would book it to the wrong session.
SESSION_TZ = ZoneInfo("America/New_York")

# Alpaca's fill sides. sell_short and buy_to_cover are the short leg's two directions,
# and they sign the same way as sell and buy respectively: the sign belongs to the
# direction of the trade, not to the instrument.
_BUY_SIDES = frozenset({"buy", "buy_to_cover"})
_SELL_SIDES = frozenset({"sell", "sell_short"})

# A reconciliation gap smaller than this is noise on a book this size: a few cents of
# accrual, a fee, a rounding in a fill price. Above it, something is unaccounted for.
RECONCILE_WARN_DOLLARS = 50.0

# The portfolio-history endpoint does not stamp a point on most session dates. Measured
# over 2026-10-02..11-04 it had 24 points for 24 sessions but on the wrong labels: every
# Monday was missing and every Saturday present, because each close is stamped with the
# FOLLOWING calendar day. Reading the close of session D as "the first stamp strictly
# after D" recovers all 24 sessions (max residual $1.84); reading it as "the stamp on D"
# recovers 14 and reaches $1,974.87. convention_check() re-tests that every run and says
# so if the endpoint ever changes its mind.
EQUITY_STAMP_OFFSET_DAYS = 1


@dataclass(frozen=True)
class ExecutionPnl:
    """One session's traded P&L, with a reason recorded for every fill left out."""

    rows: list[dict] = field(default_factory=list)
    total: float = 0.0
    fill_count: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def by_sleeve(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in self.rows:
            sleeve = row["sleeve"]
            out[sleeve] = out.get(sleeve, 0.0) + float(row["pnl"])
        return out


def signed_shares(fill: Mapping[str, object]) -> float:
    """The fill's quantity as a signed number of shares, or 0.0 if the side is unknown.

    An unknown side is not guessed at. Treating it as a buy would put a sell's P&L in
    with the wrong sign, which is worse than leaving it out and naming it.
    """
    try:
        qty = abs(float(fill.get("qty") or 0.0))
    except (TypeError, ValueError):
        return 0.0
    side = str(fill.get("side") or "").strip().lower()
    if side in _BUY_SIDES:
        return qty
    if side in _SELL_SIDES:
        return -qty
    return 0.0


def session_of(fill: Mapping[str, object]) -> str | None:
    """The session a fill belongs to, or None when its timestamp will not parse.

    Alpaca stamps fills in UTC. The session is the New York trading date, so the stamp
    is converted to America/New_York before the date is taken. For the regular session
    the UTC date happens to agree (14:30-21:00 UTC), which is what made the old UTC-only
    reading look right; it disagrees for anything after 19:00 ET, where UTC has already
    rolled over to the next day and the fill would be credited to a session that has not
    happened yet.
    """
    stamp = fill.get("transaction_time") or fill.get("date")
    if not stamp:
        return None
    text = str(stamp).replace("Z", "+00:00")
    # Alpaca sends five fractional digits ("...T14:31:31.35178+00:00"). Python before
    # 3.11 accepts only three or six and raises on the rest -- which would silently
    # drop every fill instead of failing loudly, so the fraction is normalised first.
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SESSION_TZ).date().isoformat()


def execution_pnl(
    fills: Iterable[Mapping[str, object]],
    close_now: Mapping[str, float],
    sleeve_of: Mapping[str, str] | None = None,
    session: str | None = None,
) -> ExecutionPnl:
    """Shares traded x (close - fill price), per ticker, for one session.

    This is the missing term, not a slippage measure. The price leg books
    shares_held_at_prior_close x (close - prior close); the account actually held a
    different book for part of the session, and the difference is exactly the shares
    traded earning the move from their fill to the close:

        true = S_prev x (close - prev) + traded x (close - fill)

    A buy above the close loses the difference, and so does a sell below it -- the shares
    left the book above where they finished. Both signs fall out of the arithmetic once
    sells are negative shares traded.
    """
    rows: list[dict] = []
    skipped: list[str] = []
    total = 0.0
    count = 0

    for fill in fills:
        if session is not None and session_of(fill) != session:
            continue

        ticker = str(fill.get("symbol") or "").strip().upper()
        shares = signed_shares(fill)
        if not ticker or shares == 0.0:
            skipped.append(f"{ticker or '?'}: unreadable fill side or quantity")
            continue

        try:
            price = float(fill.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0.0:
            skipped.append(f"{ticker}: no fill price")
            continue

        settle = close_now.get(ticker)
        if settle is None or float(settle) <= 0.0:
            skipped.append(f"{ticker}: no close to measure the fill against")
            continue

        pnl = shares * (float(settle) - price)
        sleeve = (sleeve_of or {}).get(ticker)
        rows.append(
            {
                "ticker": ticker,
                "sleeve": sleeve,
                "shares": shares,
                "fill_price": price,
                "close": float(settle),
                "pnl": pnl,
            }
        )
        total += pnl
        count += 1

    return ExecutionPnl(rows=rows, total=total, fill_count=count, skipped=skipped)


def close_equity(
    equity: Mapping[str, float], session: str, offset_days: int = EQUITY_STAMP_OFFSET_DAYS
) -> float | None:
    """The account equity at the close of `session`, or None when it is not there.

    The endpoint stamps a close with the following calendar day, so the close of session D
    is the first stamp strictly after D. Reading it as "the stamp on D" loses every Monday
    (whose close is stamped Tuesday) and invents every Saturday.
    """
    day = date.fromisoformat(session) + timedelta(days=offset_days - 1)
    for stamp in sorted(equity):
        if date.fromisoformat(str(stamp)) > day:
            return float(equity[stamp])
    return None


def published_closes(
    equity: Mapping[str, float],
    sessions: list[str],
    *,
    start: str | None = None,
    before: str | None = None,
) -> dict[str, float]:
    """Session -> close for every session in `sessions` this read actually covers.

    The row-by-row sibling of close_equity(), for storing the series rather than
    reconciling one session. close_equity() takes the first stamp after a session,
    which is the tolerant reading the reconciliation needs -- a Monday whose close is
    labelled Tuesday still has a close. For a stored series that tolerance is wrong at
    the edge of the read: a session with no stamp of its own near the window's start
    would be labelled with a point from days later and written as this session's close.

    So the label must be exactly the next calendar day (EQUITY_STAMP_OFFSET_DAYS), and
    a session the read does not cover is skipped instead of guessed. Where both agree
    -- every session the nightly read covers -- the values are identical, and
    tests/test_execution_pnl.py pins that against close_equity().

    `before` is exclusive, so the evening job can ask for "D-1 and earlier" from the
    same read that includes a stamp for D itself once the broker has published it.
    """
    available = {str(stamp)[:10] for stamp in equity}
    out: dict[str, float] = {}
    for raw in sorted({str(day)[:10] for day in sessions if day}):
        if before is not None and raw >= str(before)[:10]:
            continue
        if start is not None and raw < str(start)[:10]:
            continue
        label = (
            date.fromisoformat(raw) + timedelta(days=EQUITY_STAMP_OFFSET_DAYS)
        ).isoformat()
        if label in available:
            out[raw] = float(equity[label])
    return out


def equity_pair_reason(
    equity: Mapping[str, float],
    sessions: list[str],
    session: str,
) -> str | None:
    """Why the equity change for `session` cannot be measured, or None when it can.

    A bare None from session_equity_change cannot distinguish the three ways a pair is
    missing, and they are not the same thing in an email:

      - the session is not in the calendar at all (a weekend, a holiday);
      - the previous close has no stamp (the book has no opening value);
      - THIS session's close has no stamp yet. That is the normal state of an evening
        run: the endpoint labels each close with the FOLLOWING calendar day, so a run
        at 17:30 ET on day D is asking for a point labelled D+1, which the broker has
        not written yet.

    Naming it is the difference between "the reconciliation has nothing to compare"
    and "the equity close has not been published yet". The answer to the second is to
    wait for the next run, not to pair an earlier stamp and call it today's.
    """
    if session not in sessions:
        return f"{session} is not a trading session in the calendar"
    index = sessions.index(session)
    if index == 0:
        return (
            f"{session} is the first session in the calendar, so there is no prior "
            "equity close to pair it against"
        )
    previous = sessions[index - 1]
    if close_equity(equity, previous) is None:
        return f"equity close for the previous session {previous} is not published"
    if close_equity(equity, session) is None:
        return "equity close not yet published"
    return None


def session_equity_change(
    equity: Mapping[str, float],
    sessions: list[str],
    session: str,
) -> float | None:
    """The change in account equity across one session, or None when it cannot be known.

    Both sides of the reconciliation have to be measured close to close, which means the
    equity points on either side of the session and not a NAV sampled in the middle of the
    morning. A missing point is answered with None rather than with the nearest earlier
    value: a nearest-value lookup silently compares two different windows, which is the
    failure mode this whole exercise exists to remove.
    """
    try:
        index = sessions.index(session)
    except ValueError:
        return None
    if index == 0:
        return None

    closing = close_equity(equity, session)
    opening = close_equity(equity, sessions[index - 1])
    if closing is None or opening is None:
        return None
    return closing - opening


def convention_check(
    book_by_session: Mapping[str, float],
    equity: Mapping[str, float],
    sessions: list[str],
    window: int = 10,
) -> dict:
    """Is the stamp-offset reading still the better one? Re-tested every run.

    Two readings of the same series: the close of a session is the first stamp after it
    (what this module uses), or it is the stamp on the session's own date (what a plain
    reading would assume). The one that explains the trailing window better is the one the
    data is actually following, so if the endpoint changes convention the comparison flips
    and the email says so instead of the reconciliation drifting quietly.

    Execution P&L is deliberately left out: this compares two readings of the same series
    against the same expected values, so including it would only shrink both residuals
    equally.
    """
    checked = [
        day
        for day in sessions
        if day in book_by_session and equity
    ]
    checked = checked[-window:] if window > 0 else checked[::]
    if len(checked) < 3:
        return {"verdict": "unknown", "reason": "not enough sessions to compare"}

    current: list[float] = []
    naive: list[float] = []
    for day in checked:
        expected = float(book_by_session[day])
        shifted = session_equity_change(equity, sessions, day)
        if shifted is not None:
            current.append(abs(shifted - expected))

        index = sessions.index(day)
        if index == 0:
            continue
        opening = equity.get(sessions[index - 1])
        closing = equity.get(day)
        if opening is None or closing is None:
            continue
        naive.append(abs(float(closing) - float(opening) - expected))

    if len(current) < 3 or len(naive) < 3:
        return {"verdict": "unknown", "reason": "not enough paired sessions to compare"}

    current_mean = sum(current) / len(current)
    naive_mean = sum(naive) / len(naive)
    return {
        "verdict": "naive_better" if naive_mean < current_mean else "current_better",
        "current_mean_abs": current_mean,
        "naive_mean_abs": naive_mean,
        "sessions": len(current),
        "warning": (
            "EQUITY_LAG convention may have changed"
            if naive_mean < current_mean
            else None
        ),
    }


def reconcile(
    expected_pnl: float,
    nav_change: float,
    warn_dollars: float = RECONCILE_WARN_DOLLARS,
) -> dict:
    """Compare what the pipeline explains against the change in account equity.

    A warning, never a refusal. A gap is evidence of something unexplained, not proof of
    a bug, and refusing the session over it would withhold a decomposition that is still
    the best available.
    """
    gap = float(nav_change) - float(expected_pnl)
    return {
        "expected": float(expected_pnl),
        "nav_change": float(nav_change),
        "gap": gap,
        "warn": abs(gap) > warn_dollars,
        "warn_dollars": warn_dollars,
    }


def broker_distribution_debit(
    rows: Iterable[Mapping], *, sleeve_of: Mapping[str, str] | None = None
) -> tuple[float, dict[str, float]]:
    """The cash the broker moved for distributions this session, and its sleeve split.

    A distribution reads positive when the account is long and receives it and negative
    when it is short and owes it. Only the negative side needs an adjustment: paper
    credits the long side on the pay date exactly as the book's carry credits it on the
    ex-date, so the two already agree there.

    The measured ledger on the 2026-11-05 pay date was four CDIV rows -- IEF -91.26,
    TLT -113.25, LQD -100.39, HYG -131.25 -- against a -413.50 residual on that same
    session. That is the whole discrepancy, and it is a date, not an error.
    """
    total = 0.0
    by_sleeve: dict[str, float] = {}
    for row in rows or ():
        try:
            amount = float(row.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            continue
        if amount >= 0.0:
            continue
        total += amount
        if sleeve_of is not None:
            sleeve = sleeve_of.get(str(row.get("symbol") or ""))
            if sleeve:
                by_sleeve[sleeve] = by_sleeve.get(sleeve, 0.0) + amount
    return total, by_sleeve


def distribution_add_back(carry_short: float, broker_debit: float) -> float:
    """The paper-side timing item, with both of its legs.

    The economic book accrues a short's distribution on the EX-date, so the sleeve row
    books the carry there. Paper does not: the cash leaves the account on the PAY date,
    and until then its equity sits above the economic book by the accrual. Adding the
    accrual back is therefore correct only BETWEEN those two dates.

    Once the broker's dividend activity appears, the same money has moved, so the item
    has to be reversed rather than kept: the pay date's reconciliation needs the debit
    as a negative term or it inherits the accrual as an unexplained loss. Over a period
    that contains both dates the two legs cancel, which is the state the dashboard
    bridge should show.
    """
    return -float(carry_short or 0.0) + float(broker_debit or 0.0)


def short_distribution_note(
    carry_short: float, broker_debit: float = 0.0
) -> str | None:
    """Name the timing item, on whichever side of the pay date this session sits.

    Only the SHORT leg diverges between the two books: paper credits a long position's
    distribution as it should. A session with nothing on either side has nothing to
    explain and must say nothing.

    The earlier version of this note asserted that paper never debits a short its
    distributions. That was measured false on 2026-11-05, when the broker booked four
    CDIV debits: paper does debit them, on the pay date. A note that states an
    unmeasured broker behaviour is worse than no note, because it names a permanent
    cause for what is a three-day timing difference.
    """
    carry_short = float(carry_short or 0.0)
    broker_debit = float(broker_debit or 0.0)
    if abs(broker_debit) >= 0.005:
        return (
            f"paper debited {-broker_debit:,.2f} for short distributions on the pay "
            "date: the ex-date add-back is reversed here, so the timing item closes"
        )
    if abs(carry_short) >= 0.005:
        return (
            "paper has not debited the short distribution yet: equity is higher than "
            f"the economic book by {-carry_short:,.2f} dollars until the pay date"
        )
    return None


# ---------------------------------------------------------------- the daily job's hook


def record_prior_reconciliation(
    *,
    session: str,
    close: Mapping[str, float],
    sessions: list[str],
    book_total: float,
    carry_short: float,
    log=None,
    client: object | None = None,
) -> dict:
    """Reconcile the session that ended before the one the run just measured.

    The equity history stamps each close with the FOLLOWING calendar day. At 17:30 ET
    on session D the point labelled D+1 does not exist, so D's own residual cannot be
    measured by that run -- but the point labelled D is the close of D-1, and it is
    there. So tonight's residual belongs to D-1: the last session whose close has been
    published. That is what makes the number appear daily instead of never.

    The decomposition being reconciled is the one that was PUBLISHED for D-1: its
    book total is read from the stored sleeve row rather than recomputed, so a later
    close revision cannot silently change the number being checked. Its traded part is
    read from the broker's fills and priced at D-1's close, and paper's missing short
    distributions are added back exactly as on the current session.

    Never raises. Every way it can fail to measure is named in the reason field,
    because an absent residual must never read as a quiet day.
    """
    import logging

    from signals.etf_universe import ASSET_CLASS

    log = log or logging.getLogger("execution_pnl.prior")
    report: dict = {
        "session": session,
        "reconciliation": None,
        "reason": None,
        "book_total": float(book_total),
        "execution_total": None,
        "fill_count": 0,
        "carry_short": float(carry_short or 0.0),
        "distribution_debit": 0.0,
        "distribution_by_sleeve": {},
        "distribution_reason": None,
    }

    try:
        from execution.alpaca_paper import (
            EquityHistoryReadError,
            connect,
            fetch_equity_history,
            fetch_session_dividends,
            fetch_session_fills,
        )

        if client is None:
            client = connect(dry_run=False)
        if client is None:
            report["reason"] = "no Alpaca client"
            return report

        try:
            fills = fetch_session_fills(client, session)
        except Exception as exc:
            # A read failure is not a quiet session; the residual is unavailable.
            report["reason"] = str(exc)
            log.warning("prior reconciliation: fills for %s unavailable: %s", session, exc)
            return report

        pnl = execution_pnl(fills, close, sleeve_of=ASSET_CLASS, session=session)
        report["execution_total"] = pnl.total
        report["fill_count"] = pnl.fill_count

        # The pay date of a distribution is only observable at the broker. A failed read
        # is named rather than read as "no distributions": the silent zero is the
        # assumption that produced a permanent gap in the first place.
        debit_total, debit_by_sleeve = 0.0, {}
        try:
            debit_total, debit_by_sleeve = broker_distribution_debit(
                fetch_session_dividends(client, session), sleeve_of=ASSET_CLASS
            )
            report["distribution_debit"] = debit_total
            report["distribution_by_sleeve"] = debit_by_sleeve
        except Exception as exc:
            report["distribution_reason"] = f"{type(exc).__name__}: {exc}"
            log.warning(
                "prior reconciliation: distributions for %s unavailable: %s", session, exc
            )

        try:
            equity = fetch_equity_history(client, session)
        except EquityHistoryReadError as exc:
            report["reason"] = str(exc)
            log.warning("prior reconciliation: equity for %s unavailable: %s", session, exc)
            return report

        change = session_equity_change(equity, list(sessions or []), session)
        if change is None:
            report["reason"] = (
                equity_pair_reason(equity, list(sessions or []), session)
                or "no end-of-day equity on both sides of this session"
            )
            return report

        expected = (
            float(book_total) + pnl.total + distribution_add_back(carry_short, debit_total)
        )
        report["reconciliation"] = reconcile(expected, change)
        if report["reconciliation"]["warn"]:
            log.warning(
                "prior-session reconciliation for %s does not reconcile: pipeline "
                "%.2f + execution %.2f + paper add-back %.2f (ex-date accrual %.2f, "
                "pay-date debit %.2f) vs equity change %.2f (gap %.2f)",
                session, book_total, pnl.total,
                distribution_add_back(carry_short, debit_total),
                -float(carry_short or 0.0), debit_total, change,
                report["reconciliation"]["gap"],
            )
        return report

    except Exception as exc:  # bookkeeping: name it, never fail the run
        report["reason"] = f"{type(exc).__name__}: {exc}"
        log.warning("prior reconciliation unavailable for %s: %s", session, report["reason"])
        return report


def record_execution_component(
    *,
    session: str,
    close_now: Mapping[str, float],
    sessions: list[str] | None = None,
    carry_total: float,
    book_total: float,
    carry_short: float | None = None,
    log=None,
    client: object | None = None,
) -> dict:
    """Read the session's fills, price them, and reconcile the total against equity.

    Never raises and never blocks the decomposition: this is the newest and least certain
    part of step 8, and a broker hiccup must not cost the day its sleeve rows. Everything
    it could not do is named in the report instead.
    """
    import logging

    from signals.etf_universe import ASSET_CLASS

    log = log or logging.getLogger("sleeve_pnl.execution")

    # Paper credits a long position's dividends and does not debit a short position's,
    # so only the short leg needs adding back. The caller measures that leg exactly
    # (every position in the prior snapshot with negative shares). When it cannot, the
    # conservative reading is the negative part of the book's carry: a long-only payout
    # needs no adjustment, because the broker already paid it.
    short_carry = (
        float(carry_short)
        if carry_short is not None
        else min(float(carry_total or 0.0), 0.0)
    )

    report: dict = {
        "status": "unavailable",
        "reason": None,
        "total": None,
        "by_sleeve": None,
        "fill_count": 0,
        "skipped": [],
        "reconciliation": None,
        "reconciliation_reason": None,
        "convention": None,
        "close_equity": {},
        "carry_short": short_carry,
        "paper_add_back": -short_carry,
        "distribution_debit": 0.0,
        "distribution_by_sleeve": {},
        "distribution_reason": None,
        "note": short_distribution_note(short_carry),
    }

    try:
        from execution.alpaca_paper import (
            EquityHistoryReadError,
            connect,
            fetch_equity_history,
            fetch_session_dividends,
            fetch_session_fills,
        )

        # The caller's client when it has one: run_signal builds a single client for
        # step 7 and hands the same object down here. Otherwise build one, and ask for
        # dry_run=False explicitly -- DRY_RUN_DEFAULT is true on the cron, so a bare
        # connect() returned None and every evening reported "no Alpaca client" for a
        # read that never places an order. Step 7 had always done it the other way.
        if client is None:
            client = connect(dry_run=False)
        if client is None:
            report["reason"] = "no Alpaca client"
            return report

        fills = fetch_session_fills(client, session)
        pnl = execution_pnl(fills, close_now, sleeve_of=ASSET_CLASS, session=session)

        report["status"] = "ok"
        report["total"] = pnl.total
        report["by_sleeve"] = pnl.by_sleeve
        report["fill_count"] = pnl.fill_count
        report["skipped"] = pnl.skipped

        # The other half of the timing item. A distribution the broker has already paid
        # is not an add-back any more: the cash is gone from the account, so it belongs
        # in the expectation with a negative sign or the pay date reads as a loss nobody
        # can place. Read here, where the session's ledger is already open.
        debit_total, debit_by_sleeve = 0.0, {}
        try:
            debit_total, debit_by_sleeve = broker_distribution_debit(
                fetch_session_dividends(client, session), sleeve_of=ASSET_CLASS
            )
            report["distribution_debit"] = debit_total
            report["distribution_by_sleeve"] = debit_by_sleeve
        except Exception as exc:
            report["distribution_reason"] = f"{type(exc).__name__}: {exc}"
            log.warning("distributions for %s unavailable: %s", session, exc)
        report["paper_add_back"] = distribution_add_back(short_carry, debit_total)
        report["note"] = short_distribution_note(short_carry, debit_total)

        # Equity comes from the portfolio history, not from a NAV sampled at the 10:31
        # execution run: a decomposition measured close to close cannot be reconciled
        # against a number taken in the middle of the morning, and the difference between
        # the two clocks was larger than every real discrepancy it was meant to find.
        try:
            equity = fetch_equity_history(client, session)
        except EquityHistoryReadError as exc:
            # The read failed; this is not the same as "no stamp for this session yet".
            # The execution total above is still measured, so it stays on the email and
            # only the reconciliation is named unavailable.
            report["reconciliation_reason"] = str(exc)
            log.warning("equity history unavailable for %s: %s", session, exc)
            return report

        # Every session before the one being measured whose close this read has
        # published. The caller stores these for the dashboard's evening NAV series;
        # this module does not write them, because the reconciliation must stay a
        # read whose only side effect is the sleeve rows it already writes.
        report["close_equity"] = published_closes(
            equity, list(sessions or []), before=session
        )

        change = session_equity_change(equity, list(sessions or []), session)
        if change is None:
            report["reconciliation_reason"] = (
                equity_pair_reason(equity, list(sessions or []), session)
                or "no end-of-day equity on both sides of this session"
            )
            return report

        # Paper and the economic book disagree about WHEN a short's distribution is
        # paid, not whether: the book accrues it on the ex-date and paper debits it on
        # the pay date. The add-back carries both legs of that timing item, so an ex-date
        # session is explained without a pay-date session inheriting it as residual. The
        # long leg is NOT adjusted: paper credits it on the pay date and the carry
        # credits it on the ex-date, which net out on their own.
        add_back = distribution_add_back(short_carry, debit_total)
        report["paper_add_back"] = add_back
        expected = float(book_total) + pnl.total + add_back
        report["reconciliation"] = reconcile(expected, change)

        # Re-test the stamp convention on every run. It is one read of the rows this run
        # already compares against, and it is the only thing that would notice the broker
        # changing how it labels a close -- which would otherwise show up as a permanent
        # "unexplained" gap rather than as a changed assumption.
        try:
            from dashboard.supabase_client import fetch_daily_sleeve_pnl

            # raise_on_error: a DB read failure used to arrive here as an empty table and
            # be reported as "not enough sessions to compare", which is a data statement
            # about the book when it is really a statement about the connection.
            book_by_session = {
                str(row.get("trade_date")): float(row["book_total"])
                for row in fetch_daily_sleeve_pnl(limit=400, raise_on_error=True)
                if row.get("book_total") is not None
            }
            report["convention"] = convention_check(
                book_by_session, equity, list(sessions or [])
            )
        except Exception as exc:  # never let the self-check cost the reconciliation
            report["convention"] = {"verdict": "unknown", "reason": str(exc)}
        if report["reconciliation"]["warn"]:
            log.warning(
                "execution P&L does not reconcile for %s: pipeline %.2f + execution "
                "%.2f + paper add-back %.2f (ex-date accrual %.2f, pay-date debit "
                "%.2f) vs equity change %.2f (gap %.2f)",
                session, book_total, pnl.total, add_back, -short_carry, debit_total,
                change,
                report["reconciliation"]["gap"],
            )
        return report

    except Exception as exc:  # bookkeeping: report it, never fail the session
        report["status"] = "error"
        report["reason"] = f"{type(exc).__name__}: {exc}"
        log.warning("execution component unavailable for %s: %s", session, report["reason"])
        return report
