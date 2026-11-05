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
from datetime import datetime, timezone
from typing import Iterable, Mapping

# Alpaca's fill sides. sell_short and buy_to_cover are the short leg's two directions,
# and they sign the same way as sell and buy respectively: the sign belongs to the
# direction of the trade, not to the instrument.
_BUY_SIDES = frozenset({"buy", "buy_to_cover"})
_SELL_SIDES = frozenset({"sell", "sell_short"})

# A reconciliation gap smaller than this is noise on a book this size: a few cents of
# accrual, a fee, a rounding in a fill price. Above it, something is unaccounted for.
RECONCILE_WARN_DOLLARS = 50.0


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

    Alpaca stamps fills in UTC. A US equity session runs 14:30-21:00 UTC, so no session
    fill lands on a different UTC date from the New York date it traded on; taking the
    UTC date is therefore correct here and avoids inventing a calendar rule.
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
    return parsed.astimezone(timezone.utc).date().isoformat()


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


def short_distribution_note(carry_total: float, carry_paper: float = 0.0) -> str:
    """Why paper equity will not match an economic decomposition on a short book.

    The broker does not debit a paper account for the distributions a short position
    owes. The decomposition does, because the short owes them. The difference is not an
    error in either number and has to be named, or every reconciliation on a short book
    reads as a few hundred dollars of unexplained loss.
    """
    divergence = float(carry_total) - float(carry_paper)
    if abs(divergence) < 0.005:
        return "paper already carries the distributions: no adjustment"
    return (
        "paper does not debit short distributions: equity is higher than the "
        f"economic book by {-divergence:,.2f} dollars"
    )


# ---------------------------------------------------------------- the daily job's hook


def record_execution_component(
    *,
    session: str,
    close_now: Mapping[str, float],
    carry_total: float,
    book_total: float,
    log=None,
) -> dict:
    """Read the session's fills, price them, and reconcile the total against equity.

    Never raises and never blocks the decomposition: this is the newest and least certain
    part of step 8, and a broker hiccup must not cost the day its sleeve rows. Everything
    it could not do is named in the report instead.
    """
    import logging

    from signals.etf_universe import ASSET_CLASS

    log = log or logging.getLogger("sleeve_pnl.execution")
    report: dict = {
        "status": "unavailable",
        "reason": None,
        "total": None,
        "by_sleeve": None,
        "fill_count": 0,
        "skipped": [],
        "reconciliation": None,
        "paper_add_back": -float(carry_total or 0.0),
        "note": short_distribution_note(carry_total),
    }

    try:
        from dashboard.supabase_client import fetch_pnl_log
        from execution.alpaca_paper import connect, fetch_session_fills, get_live_nav

        client = connect()
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

        # Equity is read now, at the run; the anchor is the last NAV the execution job
        # recorded before this session. The two are not on the same clock -- one is the
        # close, the other 10:31 -- which is exactly why the comparison warns instead of
        # refusing.
        nav_now = get_live_nav(client)
        history = {
            str(row.get("trade_date")): row.get("live_nav")
            for row in fetch_pnl_log(limit=60)
        }
        prior_dates = sorted(d for d in history if d < session and history[d] is not None)
        if not prior_dates:
            report["reason"] = "no earlier NAV to reconcile against"
            return report

        nav_prev = float(history[prior_dates[-1]])

        # The paper account never books the distributions a short position owes, so its
        # equity sits above the economic book by exactly that amount. Adding it back is
        # the only way the two sides describe the same thing; without it every short
        # month reads as a few hundred dollars nobody can account for.
        add_back = -float(carry_total or 0.0)
        report["paper_add_back"] = add_back
        expected = float(book_total) + pnl.total + add_back
        report["reconciliation"] = reconcile(expected, nav_now - nav_prev)
        if report["reconciliation"]["warn"]:
            log.warning(
                "execution P&L does not reconcile for %s: pipeline %.2f + execution "
                "%.2f + paper add-back %.2f vs equity change %.2f (gap %.2f)",
                session, book_total, pnl.total, add_back, nav_now - nav_prev,
                report["reconciliation"]["gap"],
            )
        return report

    except Exception as exc:  # bookkeeping: report it, never fail the session
        report["status"] = "error"
        report["reason"] = f"{type(exc).__name__}: {exc}"
        log.warning("execution component unavailable for %s: %s", session, report["reason"])
        return report
