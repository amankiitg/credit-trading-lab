"""The traded part of a session, and the reconciliation that keeps it honest.

The sleeve decomposition measures the book held at the prior close, so it cannot see a
trade: shares bought at 10:00 earn the market only from the fill, and shares sold at noon
never appear in the closing snapshot at all. Both are real P&L the account had and the
decomposition did not. These tests pin the arithmetic, the sign, the session filter, and
the fact that a reconciliation gap is a warning rather than a refusal.
"""

from __future__ import annotations

from datetime import date

import pytest

import risk.execution_pnl as ep
import risk.sleeve_pnl as sp


def _fill(symbol, qty, side, price, when="2026-11-02T15:32:43.019284Z"):
    return {
        "activity_type": "FILL",
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "price": str(price),
        "transaction_time": when,
    }


# ------------------------------------------------------------------ the sign


def test_the_side_decides_the_sign_and_short_sides_are_not_guessed() -> None:
    """sell_short signs like sell, buy_to_cover like buy: the sign is the trade's."""
    assert ep.signed_shares(_fill("TLT", "10", "buy", 81.0)) == 10.0
    assert ep.signed_shares(_fill("TLT", "10", "buy_to_cover", 81.0)) == 10.0
    assert ep.signed_shares(_fill("TLT", "10", "sell", 81.0)) == -10.0
    assert ep.signed_shares(_fill("TLT", "10", "sell_short", 81.0)) == -10.0
    assert ep.signed_shares({"symbol": "TLT", "qty": "10", "side": "mystery"}) == 0.0


# ------------------------------------------------------------------ the arithmetic


def test_a_buy_that_settles_above_the_fill_earns_the_difference() -> None:
    """Hand-computed: 100 bought at 50.00 into a 50.30 close is +30.00."""
    pnl = ep.execution_pnl(
        [{"symbol": "TLT", "qty": "100", "side": "buy", "price": "50.00"}],
        {"TLT": 50.30},
        sleeve_of={"TLT": "rates"},
    )

    assert pnl.fill_count == 1
    assert pnl.total == pytest.approx(30.00)
    assert pnl.by_sleeve == {"rates": pytest.approx(30.00)}


def test_a_sell_that_settles_above_the_fill_loses_the_difference() -> None:
    """Hand-computed: 100 sold at 50.00 into a 50.30 close is -30.00.

    The shares left the book at 50.00 and finished at 50.30, so the account gave up the
    move it would have earned had it held them.
    """
    pnl = ep.execution_pnl(
        [{"symbol": "TLT", "qty": "100", "side": "sell", "price": "50.00"}],
        {"TLT": 50.30},
        sleeve_of={"TLT": "rates"},
    )

    assert pnl.fill_count == 1
    assert pnl.total == pytest.approx(-30.00)


def test_a_buy_above_the_close_costs_the_difference() -> None:
    pnl = ep.execution_pnl(
        [_fill("HYG", 50, "buy", 77.60)],
        {"HYG": 77.43},
        sleeve_of={"HYG": "credit"},
        session="2026-11-02",
    )

    assert pnl.total == pytest.approx(50 * (77.43 - 77.60))
    assert pnl.total < 0


def test_a_fill_without_a_close_is_named_not_guessed() -> None:
    """A ticker the cache has no close for contributes nothing and says so."""
    pnl = ep.execution_pnl(
        [_fill("NEW", 10, "buy", 50.0)], {}, session="2026-11-02"
    )

    assert pnl.fill_count == 0
    assert pnl.total == 0.0
    assert any("NEW" in reason for reason in pnl.skipped)


def test_only_the_session_s_own_fills_are_counted() -> None:
    """The window read from the broker is deliberately wider than the session."""
    fills = [
        _fill("TLT", 10, "sell", 81.0, when="2026-11-02T15:32:43Z"),
        _fill("TLT", 99, "sell", 81.0, when="2026-10-30T15:32:43Z"),
    ]

    pnl = ep.execution_pnl(fills, {"TLT": 81.84}, session="2026-11-02")

    assert pnl.fill_count == 1
    assert pnl.total == pytest.approx(-10 * (81.84 - 81.0))


def test_a_five_digit_fraction_timestamp_still_parses() -> None:
    """Alpaca sends five fractional digits, and Python <3.11 raises on those.

    A parse that returns None instead of raising would drop every fill silently, which is
    the worst of both worlds: no error, no execution line, and a reconciliation that says
    the trades never happened.
    """
    assert ep.session_of({"transaction_time": "2026-10-01T14:31:31.35178+00:00"}) == "2026-10-01"
    assert ep.session_of({"transaction_time": "2026-11-02T15:32:43.019284Z"}) == "2026-11-02"
    assert ep.session_of({"transaction_time": "nonsense"}) is None


# ------------------------------------------------------------------ the reconciliation


def test_a_small_gap_is_not_a_warning() -> None:
    result = ep.reconcile(1000.0, 1010.0)

    assert result["gap"] == pytest.approx(10.0)
    assert result["warn"] is False


def test_a_large_gap_warns_and_never_raises() -> None:
    result = ep.reconcile(1000.0, 1300.0)

    assert result["gap"] == pytest.approx(300.0)
    assert result["warn"] is True
    assert result["warn_dollars"] == pytest.approx(50.0)


def test_the_warning_threshold_is_exclusive_at_exactly_the_boundary() -> None:
    assert ep.reconcile(1000.0, 1050.0)["warn"] is False
    assert ep.reconcile(1000.0, 1050.01)["warn"] is True


def test_the_short_distribution_adjustment_is_named_not_absorbed() -> None:
    """Paper never debits a short its distributions; the note has to say so.

    A session with no short distribution has nothing to explain and must say NOTHING.
    The old note compared the book's carry against a hard-coded zero and so printed
    "paper already carries the distributions" on every non-ex-date -- on 2026-11-09,
    where there was no distribution at all. That sentence claims the broker was
    measured, which it was not, and contradicts the measured fact that paper does not
    debit a short's distributions.
    """
    note = ep.short_distribution_note(-436.18)

    assert "does not debit short distributions" in note
    assert "436.18" in note

    assert ep.short_distribution_note(0.0) is None
    assert "paper already carries" not in (ep.short_distribution_note(0.0) or "")


def test_a_monday_with_no_stamp_of_its_own_still_has_a_close() -> None:
    """The endpoint has no point on most Mondays and a stray one on most Saturdays.

    Each close is stamped with the following calendar day, so the close of Monday
    2026-11-02 is stamped 2026-11-03. Reading a close as "the stamp on the session" finds
    14 of 24 sessions and loses every Monday; reading it as "the first stamp after the
    session" finds all 24.
    """
    equity = {"2026-10-31": 900.0, "2026-11-03": 1000.0, "2026-11-04": 1010.0}
    sessions = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]

    assert ep.close_equity(equity, "2026-10-30") == pytest.approx(900.0)
    assert ep.close_equity(equity, "2026-11-02") == pytest.approx(1000.0), "stamped the day after"
    assert ep.session_equity_change(equity, sessions, "2026-11-02") == pytest.approx(100.0)


def test_the_equity_change_spans_the_session_not_the_morning() -> None:
    """Both sides close to close: the closes either side of the session, nothing else."""
    sessions = ["2026-10-29", "2026-10-30", "2026-11-02"]
    equity = {"2026-10-30": 1000.0, "2026-11-01": 1040.0, "2026-11-03": 990.0}

    assert ep.session_equity_change(equity, sessions, "2026-10-30") == pytest.approx(40.0)
    assert ep.session_equity_change(equity, sessions, "2026-11-02") == pytest.approx(-50.0)


def test_a_missing_equity_day_is_not_back_filled() -> None:
    """A nearest-earlier lookup would silently compare two different windows."""
    sessions = ["2026-10-29", "2026-10-30", "2026-11-02"]

    assert ep.session_equity_change({}, sessions, "2026-10-30") is None
    assert ep.session_equity_change({"2026-10-30": 1000.0}, sessions, "2026-10-30") is None
    assert ep.session_equity_change({"2026-10-30": 1000.0}, sessions, "2026-12-24") is None
    assert ep.session_equity_change({"2026-10-30": 1.0}, sessions, "2026-10-29") is None, "first session"


def test_the_convention_check_notices_when_the_naive_reading_wins() -> None:
    """If the endpoint starts stamping the session it belongs to, this run says so."""
    sessions = [f"2026-10-{d:02d}" for d in range(1, 11)]
    equity = {s: float(1000 + i * i) for i, s in enumerate(sessions)}
    # book = the naive delta, so the naive pairing explains it exactly and the shifted one
    # does not.
    book = {s: equity[s] - equity[sessions[i - 1]] for i, s in enumerate(sessions) if i}

    result = ep.convention_check(book, equity, sessions, window=10)

    assert result["verdict"] == "naive_better"
    assert result["warning"] == "EQUITY_LAG convention may have changed"
    assert result["naive_mean_abs"] < result["current_mean_abs"]


def test_the_convention_check_stays_quiet_when_the_shifted_reading_wins() -> None:
    sessions = [f"2026-10-{d:02d}" for d in range(1, 11)]
    equity = {s: float(1000 + i * i) for i, s in enumerate(sessions)}
    # book = the shifted delta: close(s_i) - close(s_{i-1}) under "first stamp after".
    book = {}
    for i, s in enumerate(sessions):
        if i == 0 or i == len(sessions) - 1:
            continue
        book[s] = equity[sessions[i + 1]] - equity[s]

    result = ep.convention_check(book, equity, sessions, window=10)

    assert result["verdict"] == "current_better"
    assert result["warning"] is None


def test_the_reconciliation_adds_back_what_paper_never_debits(monkeypatch) -> None:
    """pipeline + execution + paper add-back, against the change in equity.

    The add-back is the whole reason the reconciliation can be compared at all: paper
    equity never sees the $436.18 this book owed on 2026-11-02, so without it every short
    month reads as an unexplained loss of exactly the carry.
    """
    import execution.alpaca_paper as ap

    monkeypatch.setattr(ap, "connect", lambda dry_run=True: object())
    monkeypatch.setattr(
        ap, "fetch_session_fills",
        lambda client, day: [
            {"symbol": "TLT", "qty": "100", "side": "sell", "price": "81.00",
             "transaction_time": "2026-10-30T15:00:00Z"}
        ],
    )
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, day: {"2026-10-30": 1300.0, "2026-11-02": 1000.0},
    )

    report = ep.record_execution_component(
        session="2026-10-30",
        close_now={"TLT": 81.84},
        sessions=["2026-10-29", "2026-10-30", "2026-11-02"],
        carry_total=-436.1833,
        book_total=-358.1542,
    )

    assert report["status"] == "ok"
    assert report["total"] == pytest.approx(-84.0), "100 sold at 81.00 into an 81.84 close"
    assert report["paper_add_back"] == pytest.approx(436.1833)

    expected = -358.1542 - 84.0 + 436.1833
    reconciliation = report["reconciliation"]
    assert reconciliation["expected"] == pytest.approx(expected)
    assert reconciliation["nav_change"] == pytest.approx(-300.0), "equity 1300 -> 1000"
    assert reconciliation["gap"] == pytest.approx(-300.0 - expected)


# ------------------------------------------------------------------ the job's hook


def test_the_hook_reports_an_error_instead_of_raising(monkeypatch) -> None:
    """A broker hiccup must not cost the session its sleeve rows."""

    def _boom(*args, **kwargs):
        raise RuntimeError("alpaca down")

    import execution.alpaca_paper as ap

    monkeypatch.setattr(ap, "connect", _boom)

    report = ep.record_execution_component(
        session="2026-11-02", close_now={"TLT": 81.84}, carry_total=-436.18,
        book_total=-358.15,
    )

    assert report["status"] == "error"
    assert "alpaca down" in report["reason"]
    assert report["note"], "the short-distribution note is still supplied"


def test_a_session_with_no_equity_point_is_reported_not_guessed(monkeypatch) -> None:
    """Missing equity is a named reason, never a zero and never a guess."""
    import execution.alpaca_paper as ap

    monkeypatch.setattr(ap, "connect", lambda dry_run=True: object())
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, day: [])
    monkeypatch.setattr(ap, "fetch_equity_history", lambda client, day: {"2026-10-30": 1300.0})

    report = ep.record_execution_component(
        session="2026-10-30",
        close_now={"TLT": 81.84},
        sessions=["2026-10-30", "2026-11-02"],
        carry_total=0.0,
        book_total=0.0,
    )

    assert report["status"] == "ok"
    assert report["reconciliation"] is None
    assert "equity" in report["reason"]


def test_step_eight_attaches_the_execution_block(monkeypatch) -> None:
    """The decomposition writes its four rows whether or not the broker answers."""
    import dashboard.supabase_client as sb
    import pandas as pd
    import signals.dividends as div_mod
    import signals.etf_universe as etf

    trade_date, prior_date = "2026-11-02", "2026-10-30"
    index = pd.DatetimeIndex([prior_date, trade_date])
    close = pd.DataFrame({"TLT": [81.84, 81.73]}, index=index)
    written: list[dict] = []

    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(
        div_mod, "load_dividend_matrix",
        lambda tickers, close_index, **k: pd.DataFrame({"TLT": [0.0, 0.3432]}, index=index),
    )
    monkeypatch.setattr(div_mod, "dividends_as_of", lambda raw_dir=None: trade_date)
    monkeypatch.setattr(sb, "fetch_position_snapshot", lambda d: [{"ticker": "TLT", "shares": -330.0}])
    monkeypatch.setattr(sb, "write_daily_sleeve_pnl", lambda rows: written.extend(rows) or True)

    canned = {"status": "ok", "total": 12.5, "by_sleeve": {"rates": 12.5},
              "fill_count": 2, "reconciliation": {"gap": 80.0, "warn": True,
                                                  "expected": 1.0, "nav_change": 81.0,
                                                  "warn_dollars": 50.0},
              "note": "paper does not debit short distributions"}
    monkeypatch.setattr(sp, "record_execution_component", lambda **kw: canned)

    report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["written"] is True
    assert len(written) == 4, "the sleeve rows are written regardless"
    assert report["execution"] is canned
    assert report["execution"]["reconciliation"]["warn"] is True


def _step_eight_fixture(monkeypatch):
    """Step 8 with stubbed readers, and a spy on what it writes."""
    import dashboard.supabase_client as sb
    import pandas as pd
    import signals.dividends as div_mod
    import signals.etf_universe as etf

    trade_date, prior_date = "2026-11-02", "2026-10-30"
    index = pd.DatetimeIndex([prior_date, trade_date])
    close = pd.DataFrame({"TLT": [81.84, 81.73]}, index=index)
    written: list[list[dict]] = []

    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(
        div_mod, "load_dividend_matrix",
        lambda tickers, close_index, **k: pd.DataFrame({"TLT": [0.0, 0.3432]}, index=index),
    )
    monkeypatch.setattr(div_mod, "dividends_as_of", lambda raw_dir=None: trade_date)
    monkeypatch.setattr(sb, "fetch_position_snapshot", lambda d: [{"ticker": "TLT", "shares": -330.0}])
    monkeypatch.setattr(sb, "write_daily_sleeve_pnl", lambda rows: written.append(rows) or True)
    return sb, written, trade_date


def test_a_schema_without_the_execution_column_still_writes_the_sleeve_rows(
    monkeypatch, caplog
) -> None:
    """The decomposition is the product; the execution split is an extra.

    A missing column must cost the day nothing but a log line: carry, price and total are
    written exactly as before, and the execution figure still reaches the email.
    """
    import logging

    sb, written, trade_date = _step_eight_fixture(monkeypatch)
    monkeypatch.setattr(sb, "execution_column_exists", lambda: False)
    monkeypatch.setattr(
        sp, "record_execution_component",
        lambda **kw: {"status": "ok", "total": -12.5, "by_sleeve": {"rates": -12.5},
                      "fill_count": 1, "reconciliation": None,
                      "note": "paper does not debit short distributions"},
    )

    with caplog.at_level(logging.WARNING):
        report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["written"] is True, "the write must succeed without the column"
    assert report["execution_column"] == "missing"
    assert len(written) == 1 and len(written[0]) == 4
    assert all("execution_pnl" not in row for row in written[0]), "no column, no key"
    assert all(row["carry_pnl"] is not None for row in written[0])

    assert "execution column missing, run migration" in caplog.text
    assert report["execution"]["total"] == pytest.approx(-12.5), "still in the report"


def test_a_schema_with_the_column_writes_the_execution_split(monkeypatch) -> None:
    """With the column applied, the per-sleeve split goes into the row."""
    sb, written, trade_date = _step_eight_fixture(monkeypatch)
    monkeypatch.setattr(sb, "execution_column_exists", lambda: True)
    monkeypatch.setattr(
        sp, "record_execution_component",
        lambda **kw: {"status": "ok", "total": 12.5, "by_sleeve": {"rates": 12.5},
                      "fill_count": 2, "reconciliation": None, "note": None},
    )

    report = sp.record_daily_sleeve_pnl(as_of_date=trade_date)

    assert report["written"] is True
    rows = {row["sleeve"]: row for row in written[0]}
    assert rows["rates"]["execution_pnl"] == pytest.approx(12.5)
    assert rows["credit"]["execution_pnl"] == pytest.approx(0.0), "no fills, no execution P&L"
    assert rows["rates"]["carry_pnl"] == pytest.approx(-113.256, abs=0.01), "TLT only in this fixture"


def test_the_email_says_the_gap_out_loud(monkeypatch) -> None:
    """The warning has to reach a human, not just the log."""
    from execution.daily_summary import RunSummary

    summary = RunSummary(job="run_signal", run_date="2026-11-02")
    summary.execution_pnl = {"total": -120.0, "fill_count": 3, "by_sleeve": {"rates": -120.0}}
    summary.record_execution_pnl(
        {
            "status": "ok",
            "total": -120.0,
            "by_sleeve": {"rates": -120.0},
            "fill_count": 3,
            "note": "paper does not debit short distributions",
            "reconciliation": {"expected": -478.0, "nav_change": -100.0, "gap": 378.0,
                               "warn": True, "warn_dollars": 50.0},
        }
    )

    assert summary.execution_note.startswith("WARNING")
    assert "378.00" in summary.execution_note
    assert "does not debit short distributions" in summary.execution_note
