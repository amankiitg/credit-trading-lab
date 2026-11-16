"""The period bridge: NAV change decomposed into the terms that explain it.

The identity under the Book P&L charts is only useful if a missing term shows as
missing. Execution P&L was not stored before 2026-11-09, so a period that starts
before it must say "not stored" for those sessions rather than print a zero and
leave the reader to find the traded part in the residual.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from dashboard.views import operational

SLEEVES = ("equity", "rates", "credit", "commodity")


def _nav(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"trade_date": pd.Timestamp(session), "nav": value} for session, value in rows]
    )


def _session(
    session: str,
    *,
    price: float,
    carry: float = 0.0,
    execution: float | None = None,
) -> list[dict]:
    return [
        {
            "trade_date": session,
            "sleeve": sleeve,
            "price_pnl": price / 4,
            "carry_pnl": carry / 4,
            "total": (price + carry) / 4,
            "book_total": price + carry,
            "execution_pnl": None if execution is None else execution / 4,
        }
        for sleeve in SLEEVES
    ]


def _sleeve(*sessions: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([row for rows in sessions for row in rows])


def _pnl(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["trade_date", "turnover_cost"])


def test_a_fully_stored_period_closes_to_the_nav_change() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-11-02", 100_000.0), ("2026-11-03", 100_500.0)]),
        _sleeve(_session("2026-11-03", price=480.0, execution=25.0)),
        _pnl([("2026-11-03", 5.0)]),
        start=date(2026, 11, 2),
    )

    assert summary["base"] == "2026-11-02"
    assert summary["last"] == "2026-11-03"
    assert summary["nav_change"] == pytest.approx(500.0)
    # 480 + 25 - 5 = 500: the residual is zero when every term is stored.
    assert summary["residual"] == pytest.approx(0.0)
    terms = {row["term"]: row for row in rows}
    assert terms["NAV change (official closes)"]["amount"] == "$+500.00"
    assert terms["Turnover cost (subtracted)"]["amount"] == "$5.00"


def test_execution_pnl_before_it_was_stored_is_not_a_zero() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-10-01", 100_000.0), ("2026-10-02", 101_000.0)]),
        _sleeve(_session("2026-10-02", price=900.0)),
        _pnl([("2026-10-02", 1.0)]),
        start=date(2026, 10, 1),
    )

    assert summary["execution"] is None
    # No stored execution P&L means the residual is not claimed either: a number
    # here would hide the missing term inside "residual".
    assert summary["residual"] is None
    assert {row["term"]: row for row in rows}[
        "Cumulative execution P&L"
    ]["amount"] == "not stored"


def test_a_partly_stored_period_reports_how_many_sessions_it_has() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-11-08", 100_000.0), ("2026-11-09", 100_100.0), ("2026-11-10", 100_400.0)]),
        _sleeve(
            _session("2026-11-09", price=100.0),
            _session("2026-11-10", price=295.0, execution=5.0),
        ),
        _pnl([("2026-11-09", 0.0), ("2026-11-10", 0.0)]),
        start=date(2026, 11, 8),
    )

    assert summary["sessions"] == 2
    assert summary["execution_sessions"] == 1
    assert summary["execution"] == pytest.approx(5.0)
    execution_row = {row["term"]: row for row in rows}["Cumulative execution P&L"]
    assert execution_row["amount"] == "$+5.00"
    assert "1 of 2 session(s) stored" in execution_row["note"]
    assert "not stored for 1 session(s) before 2026-11-09" in execution_row["note"]


def test_the_short_distribution_is_added_back_and_nets_against_carry() -> None:
    """A short is unaffected by a distribution: carry and add-back cancel."""
    rows, summary = operational._period_bridge(
        _nav([("2026-11-01", 100_000.0), ("2026-11-02", 100_000.0)]),
        _sleeve(_session("2026-11-02", price=0.0, carry=-436.18, execution=0.0)),
        _pnl([("2026-11-02", 0.0)]),
        start=date(2026, 11, 1),
    )

    assert summary["carry"] == pytest.approx(-436.18)
    assert summary["add_back"] == pytest.approx(436.18)
    assert summary["residual"] == pytest.approx(0.0)


def test_a_session_without_a_stored_close_is_left_out_of_every_term() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-11-02", 100_000.0), ("2026-11-03", 100_500.0)]),
        _sleeve(
            _session("2026-11-03", price=480.0, execution=25.0),
            _session("2026-11-04", price=999.0, execution=99.0),
        ),
        _pnl([("2026-11-03", 5.0), ("2026-11-04", 50.0)]),
        start=date(2026, 11, 2),
    )

    assert summary["last"] == "2026-11-03"
    assert summary["sessions"] == 1
    assert summary["book"] == pytest.approx(480.0)
    assert summary["cost"] == pytest.approx(5.0)


def test_a_period_with_no_close_before_it_has_no_bridge() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-11-03", 100_500.0)]),
        _sleeve(_session("2026-11-03", price=480.0)),
        _pnl([]),
        start=date(2026, 11, 3),
    )

    assert rows == []
    assert summary["residual"] is None


# ------------------------------------------------------------------ display rules


def test_a_cost_is_shown_without_a_plus_sign() -> None:
    assert operational._fmt_cost(3.3658) == "$3.37"
    assert operational._fmt_cost(-3.37) == "$3.37"
    assert operational._fmt_cost(None) == "—"
    assert "+" not in operational._fmt_cost(12.0)


def test_orders_outside_the_band_are_named_with_their_delta() -> None:
    summary = operational._order_summary(
        [
            {"ticker": "EEM", "delta ($)": -253.0, "action": "sell / short"},
            {"ticker": "SPY", "delta ($)": 1_200.0, "action": "buy"},
        ]
    )

    assert summary == (
        "2 orders for the next morning run: EEM $-253 (sell / short), "
        "SPY $+1,200 (buy)"
    )
    assert operational._order_summary([]) is None


def test_the_approval_line_answers_whether_a_decision_is_waiting() -> None:
    open_window = dict(state=operational.DECISION_STATE_OPEN, auto_approve=False)

    assert "needed before the next morning run" in operational._approval_status(
        **open_window, decision=None
    )
    assert "auto-approve is ON" in operational._approval_status(
        state=operational.DECISION_STATE_OPEN, auto_approve=True, decision=None
    )
    assert "already approved" in operational._approval_status(
        **open_window, decision="approve"
    )
    assert "today is rejected" in operational._approval_status(
        **open_window, decision="reject"
    )
    assert "already happened" in operational._approval_status(
        state=operational.DECISION_STATE_LOCKED, auto_approve=False, decision=None
    )
    assert "waiting for the next signal" in operational._approval_status(
        state=operational.DECISION_STATE_NO_SIGNAL, auto_approve=False, decision=None
    )
