"""Daily book P&L by sleeve, split into carry and price -- sprint v9.8.

The daily email reports the book's result as the move in live account equity. That is
the right headline and it says nothing about where the money came from. This module
decomposes one session's move into the four sleeves and, inside each sleeve, into the
distributions the book received (carry) and the price move on the book it held (price).

Convention: the session's P&L is measured on the book as it stood at the previous
close.

    price_i(t) = shares_i(t-1) * (close_i(t) - close_i(t-1))
    carry_i(t) = shares_i(t-1) * distribution_i(t)

so a trade made during session t contributes from t+1. That is what daily data can
support: the positions snapshot is one row per session and no intraday book is kept.
It also means this number is NOT the email's Book P&L, which is the raw equity move
and includes intraday marks and the trades themselves. They are different quantities
on purpose, and the dashboard says which is which.

Prices are the UNADJUSTED close. Adj_close is back-adjusted, so the difference between
two adjusted closes already contains the distributions paid in between; adding a carry
leg to that difference would count the same distribution twice, once inside the price
move and once as carry. signals.etf_universe.load_universe_close(column="close")
supplies the raw series for that reason.

The reconciliation gate is not optional. The sleeve rows are a regrouping of the
per-ticker vector, so the sum of the four sleeves must equal the flat per-ticker total
for the same session within RECONCILE_TOL, the tolerance the v8.3 engine uses. A
residual outside it means the grouping lost or duplicated an instrument -- an
instrument with no sleeve mapping is the realistic case -- and the day is not written.

What the gate does not catch is worth stating plainly: it cannot see an error inside
the per-ticker formula itself, because the only second price series available is
adj_close, which is a different quantity by construction rather than a second view of
the same one. What it does catch is in tests/test_sleeve_pnl.py.

A day that cannot be computed is refused rather than zero-filled: no position snapshot
for the previous session (the first day, and any day after a run that never wrote one),
an instrument with no share count in that snapshot, no sleeve mapping, or no close on
either side. A missing input is not a flat day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from risk.attribution import RECONCILE_TOL, SLEEVES
from signals.etf_universe import ASSET_CLASS

STATUS_OK = "ok"
STATUS_UNCOMPARABLE = "uncomparable"
STATUS_MISMATCH = "mismatch"

# The gate's floor, in dollars. The tolerance is scaled by the day's book P&L because
# the residual is floating-point noise on a regrouping; on a quiet day the gross is
# small and an absolute floor keeps the gate from becoming vacuously tight.
MIN_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SleevePnlResult:
    """One session's decomposition, or the reason there is not one.

    `rows` is populated only when `status` is ok: four rows, one per sleeve, each
    carrying the session's book total. `residual` and `tolerance` are reported in
    every case so the log line can always show what was compared.
    """

    status: str
    reason: str | None = None
    rows: list[dict] = field(default_factory=list)
    per_ticker: list[dict] = field(default_factory=list)
    book_gross_pnl: float = 0.0
    grouped_total: float = 0.0
    residual: float = 0.0
    tolerance: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _price(value: object) -> float | None:
    """A usable price, or None. NaN and non-positive prices are not usable."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out or out <= 0.0:
        return None
    return out


def _distribution(value: object) -> float:
    """A per-share distribution, or 0.0.

    The dividend matrix is zero on a non-pay date, so an absent or unreadable value
    means "paid nothing today", not "unknown". NaN has to be caught explicitly: `nan
    or 0.0` returns the NaN, which would put a NaN into every sleeve row and straight
    through the gate, since a NaN comparison is neither greater nor less than the
    tolerance.
    """
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if out != out else out


def per_ticker_pnl(
    holdings: list[tuple[str, float]],
    close_prev: Mapping[str, float],
    close_now: Mapping[str, float],
    dividends: Mapping[str, float],
) -> list[dict]:
    """The day's carry and price for each holding, before any grouping."""
    rows = []
    for ticker, shares in holdings:
        prev = float(close_prev[ticker])
        now = float(close_now[ticker])
        price = shares * (now - prev)
        carry = shares * _distribution(dividends.get(ticker))
        rows.append({
            "ticker": ticker,
            "shares": shares,
            "sleeve": ASSET_CLASS.get(ticker),
            "price_pnl": price,
            "carry_pnl": carry,
            "total": price + carry,
        })
    return rows


def group_by_sleeve(
    per_ticker: list[dict], trade_date: str, book_total: float
) -> list[dict]:
    """Regroup the per-ticker vector into one row per sleeve, all four always present.

    A row whose sleeve is not one of the four is skipped here rather than raising,
    because dropping an instrument is exactly the failure the reconciliation gate
    exists to catch. compute_sleeve_pnl refuses the day before the grouping when an
    instrument has no mapping at all.
    """
    grouped: dict[str, dict[str, float]] = {
        sleeve: {"price_pnl": 0.0, "carry_pnl": 0.0} for sleeve in SLEEVES
    }
    for row in per_ticker:
        sleeve = row["sleeve"]
        if sleeve not in grouped:
            continue
        grouped[sleeve]["price_pnl"] += float(row["price_pnl"])
        grouped[sleeve]["carry_pnl"] += float(row["carry_pnl"])

    rows = []
    for sleeve in SLEEVES:
        price = grouped[sleeve]["price_pnl"]
        carry = grouped[sleeve]["carry_pnl"]
        rows.append({
            "trade_date": trade_date,
            "sleeve": sleeve,
            "price_pnl": price,
            "carry_pnl": carry,
            "total": price + carry,
            "book_total": book_total,
        })
    return rows


def reconcile(per_ticker: list[dict], sleeve_rows: list[dict]) -> tuple[float, float]:
    """(residual, tolerance) for the sleeve grouping against the flat per-ticker total.

    The flat total is computed before any grouping, so this compares the decomposition
    against the quantity it claims to decompose. Both are the same arithmetic; what the
    comparison tests is that nothing was lost or double-counted on the way through.
    """
    book = sum(float(row["total"]) for row in per_ticker)
    grouped = sum(float(row["total"]) for row in sleeve_rows)
    return grouped - book, max(MIN_TOLERANCE, abs(book) * RECONCILE_TOL)


def compute_sleeve_pnl(
    *,
    trade_date: str,
    prior_date: str,
    positions: list[dict],
    close_prev: Mapping[str, float],
    close_now: Mapping[str, float],
    dividends: Mapping[str, float] | None = None,
) -> SleevePnlResult:
    """Attribute one session's book P&L to the four sleeves and to carry vs price.

    `positions` is the snapshot for `prior_date`, i.e. the book held into the session.
    `close_prev` and `close_now` are unadjusted closes for the two sessions, and
    `dividends` is the per-share distribution on `trade_date`; a ticker absent from it
    paid nothing, which is what the dividend matrix means by a zero.

    Pure: no I/O, so the arithmetic, the grouping and the gate are all testable without
    a database or a broker.
    """
    dividends = dividends or {}

    holdings: list[tuple[str, float]] = []
    for row in positions:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            return SleevePnlResult(STATUS_UNCOMPARABLE, "a position row has no ticker")

        raw_shares = row.get("shares")
        if raw_shares is None:
            return SleevePnlResult(
                STATUS_UNCOMPARABLE,
                f"{ticker} has no share count in the {prior_date} snapshot, so the "
                f"session's P&L for it is unknown",
            )
        shares = float(raw_shares)
        if shares == 0.0:
            continue

        if ticker not in ASSET_CLASS:
            return SleevePnlResult(
                STATUS_UNCOMPARABLE,
                f"{ticker} is held but belongs to no sleeve, so its P&L has nowhere "
                f"to go",
            )
        if _price(close_prev.get(ticker)) is None:
            return SleevePnlResult(
                STATUS_UNCOMPARABLE, f"{ticker} has no close for {prior_date}"
            )
        if _price(close_now.get(ticker)) is None:
            return SleevePnlResult(
                STATUS_UNCOMPARABLE, f"{ticker} has no close for {trade_date}"
            )

        holdings.append((ticker, shares))

    if not holdings:
        return SleevePnlResult(
            STATUS_UNCOMPARABLE,
            f"the {prior_date} snapshot carries no share counts, so there is no book "
            f"to attribute",
        )

    rows_by_ticker = per_ticker_pnl(holdings, close_prev, close_now, dividends)
    book_gross = sum(row["total"] for row in rows_by_ticker)
    sleeve_rows = group_by_sleeve(rows_by_ticker, trade_date, book_gross)
    residual, tolerance = reconcile(rows_by_ticker, sleeve_rows)

    if abs(residual) > tolerance:
        return SleevePnlResult(
            STATUS_MISMATCH,
            f"the sleeves do not reconcile to the book for {trade_date}: residual "
            f"${residual:,.6f} against a tolerance of ${tolerance:,.6f}",
            per_ticker=rows_by_ticker,
            book_gross_pnl=book_gross,
            grouped_total=book_gross + residual,
            residual=residual,
            tolerance=tolerance,
        )

    return SleevePnlResult(
        STATUS_OK,
        rows=sleeve_rows,
        per_ticker=rows_by_ticker,
        book_gross_pnl=book_gross,
        grouped_total=book_gross + residual,
        residual=residual,
        tolerance=tolerance,
    )


# ---------------------------------------------------------------- the daily job's entry point


def record_daily_sleeve_pnl(*, as_of_date: str, log=None) -> dict:
    """Compute one session's sleeve P&L from the stored snapshot and the local cache.

    Mirrors execution.snapshot.refresh_snapshot: bookkeeping that reports what it did
    and never raises, because a failure here must not be able to stop a run whose
    signal has already been written.

    The session whose closing book is measured is derived from the close cache rather
    than taken from the caller, so the two dates can never disagree about which book
    was held. Reads that snapshot from Supabase and the UNADJUSTED closes and the
    distribution cache from disk; writes nothing at all unless the day reconciles.
    """
    import logging

    log = log or logging.getLogger("sleeve_pnl")
    report: dict = {
        "written": False,
        "status": None,
        "reason": None,
        "trade_date": as_of_date,
        "prior_date": None,
        "book_gross_pnl": None,
        "residual": None,
        "tolerance": None,
        "carry_total": None,
        "price_total": None,
        "sleeves": None,
    }

    try:
        from dashboard.supabase_client import (
            fetch_position_snapshot,
            write_daily_sleeve_pnl,
        )
        from signals.dividends import load_dividend_matrix
        from signals.etf_universe import UNIVERSE, load_universe_close

        close = load_universe_close(UNIVERSE, column="close")
        sessions = {str(ts.date()): ts for ts in close.index}
        if as_of_date not in sessions:
            report["status"] = STATUS_UNCOMPARABLE
            report["reason"] = f"the close cache has no row for {as_of_date}"
            log.info("sleeve P&L not computed: %s", report["reason"])
            return report

        prior_date = previous_session(close.index)
        if prior_date is None:
            report["status"] = STATUS_UNCOMPARABLE
            report["reason"] = "the close cache holds a single session, so there is no prior book"
            log.info("sleeve P&L not computed: %s", report["reason"])
            return report
        report["prior_date"] = prior_date

        positions = fetch_position_snapshot(prior_date)
        if not positions:
            report["status"] = STATUS_UNCOMPARABLE
            report["reason"] = (
                f"no position snapshot for {prior_date}, so the session's book is "
                f"unknown"
            )
            log.info("sleeve P&L not computed: %s", report["reason"])
            return report

        close_prev = close.loc[sessions[prior_date]].dropna().to_dict()
        close_now = close.loc[sessions[as_of_date]].dropna().to_dict()
        dividends = load_dividend_matrix(UNIVERSE, close.index).loc[
            sessions[as_of_date]
        ].to_dict()

        result = compute_sleeve_pnl(
            trade_date=as_of_date,
            prior_date=prior_date,
            positions=positions,
            close_prev=close_prev,
            close_now=close_now,
            dividends=dividends,
        )

        report["status"] = result.status
        report["reason"] = result.reason
        report["book_gross_pnl"] = result.book_gross_pnl
        report["residual"] = result.residual
        report["tolerance"] = result.tolerance

        if result.status == STATUS_MISMATCH:
            log.error("sleeve P&L NOT WRITTEN for %s: %s", as_of_date, result.reason)
            return report

        if result.status != STATUS_OK:
            log.info("sleeve P&L not computed for %s: %s", as_of_date, result.reason)
            return report

        report["sleeves"] = {r["sleeve"]: r["total"] for r in result.rows}
        report["carry_total"] = sum(r["carry_pnl"] for r in result.rows)
        report["price_total"] = sum(r["price_pnl"] for r in result.rows)

        # The residual is logged whether or not it passes, so a growing residual is
        # visible in the run log long before it ever blocks a write.
        log.info(
            "sleeve P&L for %s on the %s book: book=$%.2f, sleeves=%s, "
            "residual=$%.9f (tol $%.9f), carry=$%.2f, price=$%.2f",
            as_of_date,
            prior_date,
            result.book_gross_pnl,
            {k: round(v, 2) for k, v in report["sleeves"].items()},
            result.residual,
            result.tolerance,
            report["carry_total"],
            report["price_total"],
        )

        if not write_daily_sleeve_pnl(result.rows):
            report["status"] = "write_failed"
            report["reason"] = "Supabase rejected the daily_sleeve_pnl write"
            log.error("sleeve P&L write FAILED for %s: %s", as_of_date, report["reason"])
            return report

        report["written"] = True
        return report

    except Exception as exc:
        # Broad on purpose, including the imports: this is bookkeeping.
        report["status"] = "error"
        report["reason"] = f"{type(exc).__name__}: {exc}"
        log.warning("sleeve P&L skipped for %s: %s", as_of_date, report["reason"])
        return report


def previous_session(close_index) -> str | None:
    """The session before the last one in a close index, as YYYY-MM-DD, or None.

    The decomposition needs two sessions: the one being measured and the one whose
    closing book was held into it.
    """
    if len(close_index) < 2:
        return None
    return str(close_index[-2].date())
