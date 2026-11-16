"""The short-distribution timing item: accrued on the ex-date, reversed on the pay date.

Measured 2026-11-05 (the 2026-11-02 ex-date's pay date), the paper account booked four
CDIV debits -- IEF -91.26, TLT -113.25, LQD -100.39, HYG -131.25, -436.15 total --
against a -413.50 residual on the same session. The reconciliation had been adding the
ex-date accrual back and never removing it, so the pay date carried the same money as an
unexplained loss for as long as the period included it.

These tests pin the two legs separately and together: the ex-date must still be
explained by the accrual, the pay date must be explained by the debit, and a period with
both must net the add-back to zero.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from dashboard.views import operational
from execution import alpaca_paper as ap
from risk import execution_pnl as ep


def _div(symbol: str, day: str, amount: float) -> dict:
    return {
        "activity_type": "DIV",
        "activity_sub_type": "CDIV",
        "date": day,
        "symbol": symbol,
        "net_amount": f"{amount:.2f}",
    }


# The four rows the broker actually booked on the 2026-11-05 pay date.
PAY_DATE_ROWS = [
    _div("IEF", "2026-11-05", -91.26),
    _div("TLT", "2026-11-05", -113.25),
    _div("LQD", "2026-11-05", -100.39),
    _div("HYG", "2026-11-05", -131.25),
]
PAY_DATE_TOTAL = -436.15


class _FakeClient:
    def __init__(self, rows, *, fail: bool = False) -> None:
        self._rows = rows
        self._fail = fail
        self.calls: list[dict] = []

    def get(self, path, data=None):
        self.calls.append({"path": path, "data": data})
        if self._fail:
            raise RuntimeError("ledger unreachable")
        return self._rows


def test_the_dividend_read_keeps_only_the_session_asked_for() -> None:
    """The window is wide; the date filter is what decides.

    The endpoint's after/until are UTC instants and a distribution created late on a
    session lands after midnight UTC, so the window cannot be narrowed to one day. If the
    filter were dropped, every neighbouring session's payout would be charged to this
    one.
    """
    client = _FakeClient(
        [
            _div("HYG", "2026-11-05", -131.25),
            _div("HYG", "2026-11-04", -999.99),
            _div("TLT", "2026-10-05", -113.25),
        ]
    )

    rows = ap.fetch_session_dividends(client, "2026-11-05")

    assert [row["symbol"] for row in rows] == ["HYG"]
    assert client.calls[0]["data"]["activity_types"].startswith("DIV")
    assert "FILL" not in client.calls[0]["data"]["activity_types"]


def test_an_unreadable_ledger_raises_instead_of_reading_as_a_quiet_session() -> None:
    with pytest.raises(ap.ActivitiesReadError):
        ap.fetch_session_dividends(_FakeClient([], fail=True), "2026-11-05")


def test_only_the_short_side_is_adjusted_and_it_is_split_by_sleeve() -> None:
    """A long payout is a credit the book already has: adjusting it would double it."""
    rows = PAY_DATE_ROWS + [_div("GLD", "2026-11-05", 12.5)]

    total, by_sleeve = ep.broker_distribution_debit(
        rows, sleeve_of={"IEF": "rates", "TLT": "rates", "LQD": "credit", "HYG": "credit"}
    )

    assert total == pytest.approx(PAY_DATE_TOTAL)
    assert by_sleeve == {
        "rates": pytest.approx(-91.26 - 113.25),
        "credit": pytest.approx(-100.39 - 131.25),
    }


def test_the_add_back_is_positive_before_the_pay_date_and_negative_on_it() -> None:
    ex_date = ep.distribution_add_back(carry_short=-436.15, broker_debit=0.0)
    pay_date = ep.distribution_add_back(carry_short=0.0, broker_debit=PAY_DATE_TOTAL)
    both = ep.distribution_add_back(carry_short=-436.15, broker_debit=PAY_DATE_TOTAL)

    assert ex_date == pytest.approx(436.15), "paper has not paid it yet"
    assert pay_date == pytest.approx(-436.15), "paper has paid it: reverse the accrual"
    assert both == pytest.approx(0.0), "over the whole stretch it is a date, not a loss"


def test_the_note_says_which_side_of_the_pay_date_this_session_sits() -> None:
    before = ep.short_distribution_note(-436.15, 0.0)
    pay = ep.short_distribution_note(0.0, PAY_DATE_TOTAL)

    assert "has not debited the short distribution yet" in before
    assert "436.15" in before
    assert "debited 436.15 for short distributions on the pay date" in pay
    assert ep.short_distribution_note(0.0, 0.0) is None


def test_the_pay_date_reconciles_when_the_brokers_debit_is_read(monkeypatch) -> None:
    """The measured case: 2026-11-05, book -358.15, no fills, equity down by the debit."""
    monkeypatch.setattr(ap, "connect", lambda dry_run=True: _FakeClient(PAY_DATE_ROWS))
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, day: [])
    monkeypatch.setattr(ap, "fetch_session_dividends", lambda client, day: PAY_DATE_ROWS)
    # Each close is stamped with the FOLLOWING calendar day, so the close held into
    # 2026-11-05 is the row stamped 2026-11-05 and its own close is stamped 2026-11-06.
    monkeypatch.setattr(
        ap,
        "fetch_equity_history",
        lambda client, day: {"2026-11-05": 100_436.15, "2026-11-06": 100_000.00},
    )

    report = ep.record_execution_component(
        session="2026-11-05",
        close_now={"TLT": 81.84},
        sessions=["2026-11-04", "2026-11-05", "2026-11-06"],
        carry_total=0.0,
        carry_short=0.0,
        book_total=-358.1542,
    )

    assert report["status"] == "ok"
    assert report["distribution_debit"] == pytest.approx(PAY_DATE_TOTAL)
    assert report["paper_add_back"] == pytest.approx(PAY_DATE_TOTAL)
    # Book + debit = what the account actually did once the cash left.
    assert report["reconciliation"]["expected"] == pytest.approx(
        -358.1542 + PAY_DATE_TOTAL
    )
    assert report["reconciliation"]["gap"] == pytest.approx(
        -436.15 - (-358.1542 - 436.15)
    )


def test_a_failed_dividend_read_is_named_not_silently_zero(monkeypatch) -> None:
    monkeypatch.setattr(ap, "connect", lambda dry_run=True: _FakeClient([], fail=True))
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, day: [])
    monkeypatch.setattr(
        ap, "fetch_equity_history", lambda client, day: {"2026-11-05": 1000.0}
    )

    report = ep.record_execution_component(
        session="2026-11-05",
        close_now={"TLT": 81.84},
        sessions=["2026-11-04", "2026-11-05"],
        carry_total=0.0,
        carry_short=-436.15,
        book_total=-358.1542,
    )

    assert report["distribution_reason"] is not None
    assert "ledger unreachable" in report["distribution_reason"]
    # Falls back to the accrual-only reading, which is what it did before, and says so.
    assert report["paper_add_back"] == pytest.approx(436.15)


# --------------------------------------------------------------------- the bridge

SLEEVES = ("equity", "rates", "credit", "commodity")


def _nav(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"trade_date": pd.Timestamp(s), "nav": v} for s, v in rows]
    )


def _session(
    session: str,
    *,
    price: float,
    carry: float = 0.0,
    distribution: float | None = 0.0,
) -> list[dict]:
    return [
        {
            "trade_date": session,
            "sleeve": sleeve,
            "price_pnl": price / 4,
            "carry_pnl": carry / 4,
            "total": (price + carry) / 4,
            "book_total": price + carry,
            "execution_pnl": 0.0,
            "distribution_pnl": (
                None if distribution is None else distribution / 4
            ),
        }
        for sleeve in SLEEVES
    ]


def _pnl(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["trade_date", "turnover_cost"])


def test_the_bridge_nets_the_add_back_over_an_ex_to_pay_period() -> None:
    """NAV rises by the accrual on the ex-date and falls by the debit on the pay date.

    Both sessions are in the period, so the net is zero: the add-back term must be zero
    too, and the residual must close. Before this change the term stayed at +436.18 and
    the pay date's -436.15 sat in the residual.
    """
    rows, summary = operational._period_bridge(
        _nav([("2026-11-01", 100_000.00), ("2026-11-02", 100_000.00),
              ("2026-11-05", 99_563.85)]),
        pd.DataFrame(
            _session("2026-11-02", price=0.0, carry=-436.15)
            + _session("2026-11-05", price=0.0, distribution=PAY_DATE_TOTAL)
        ),
        _pnl([("2026-11-02", 0.0), ("2026-11-05", 0.0)]),
        start=date(2026, 11, 1),
    )

    assert summary["add_back_accrued"] == pytest.approx(436.15)
    assert summary["add_back_debited"] == pytest.approx(PAY_DATE_TOTAL)
    assert summary["add_back"] == pytest.approx(0.0)
    assert summary["residual"] == pytest.approx(0.0, abs=1e-6)
    term = {row["term"]: row for row in rows}["Paper short-distribution add-back"]
    assert "nets out" in term["note"]


def test_the_bridge_still_adds_back_a_period_that_ends_before_the_pay_date() -> None:
    rows, summary = operational._period_bridge(
        _nav([("2026-11-01", 100_000.00), ("2026-11-02", 100_000.00)]),
        pd.DataFrame(_session("2026-11-02", price=0.0, carry=-436.15)),
        _pnl([("2026-11-02", 0.0)]),
        start=date(2026, 11, 1),
    )

    assert summary["add_back"] == pytest.approx(436.15)
    assert summary["residual"] == pytest.approx(0.0, abs=1e-6)
    term = {row["term"]: row for row in rows}["Paper short-distribution add-back"]
    assert "not in this period yet" in term["note"]


def test_a_table_without_the_column_keeps_the_old_behaviour() -> None:
    """The probe lets the dashboard deploy before the migration is applied."""
    legacy = pd.DataFrame(
        [
            {k: v for k, v in row.items() if k != "distribution_pnl"}
            for row in _session("2026-11-02", price=0.0, carry=-436.15)
        ]
    )

    _, summary = operational._period_bridge(
        _nav([("2026-11-01", 100_000.00), ("2026-11-02", 100_000.00)]),
        legacy,
        _pnl([("2026-11-02", 0.0)]),
        start=date(2026, 11, 1),
    )

    assert summary["add_back"] == pytest.approx(436.15)
