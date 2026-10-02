"""The daily sleeve P&L: per-ticker arithmetic, grouping, gate, and refusal paths.

Three things this file is here to pin:

  - the two formulas, on a book with a long, a short, a payer and a short payer, so
    that a sign error in either leg shows up as a wrong number rather than as a
    plausible one;
  - the reconciliation gate, including a deliberately broken grouping that must stop
    the write;
  - the refusal paths. A day with no prior snapshot, a missing share count, an
    unmapped instrument or a missing close is NOT a flat day, and it must not be
    stored as one.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from risk.sleeve_pnl import (
    MIN_TOLERANCE,
    STATUS_MISMATCH,
    STATUS_OK,
    STATUS_UNCOMPARABLE,
    compute_sleeve_pnl,
    group_by_sleeve,
    per_ticker_pnl,
    previous_session,
    reconcile,
    record_daily_sleeve_pnl,
)

TRADE_DATE = "2026-10-02"
PRIOR_DATE = "2026-10-01"

# A book with one of each thing that can go wrong in the arithmetic:
#   SPY  long, price rises      -> price +20
#   LQD  short, price falls     -> price +20 (a short gains when the price falls)
#   HYG  long, flat, pays 0.40  -> carry +20, price 0
#   IEF  short, flat, pays 0.10 -> carry -3  (the holder pays the distribution)
#
# book = 20 + 20 + 20 - 3 = 57, of which carry is +17 and price +40.
POSITIONS = [
    {"ticker": "SPY", "shares": 10, "signed_notional": 1000.0},
    {"ticker": "LQD", "shares": -20, "signed_notional": -2000.0},
    {"ticker": "HYG", "shares": 50, "signed_notional": 4000.0},
    {"ticker": "IEF", "shares": -30, "signed_notional": -2700.0},
]
CLOSE_PREV = {"SPY": 100.0, "LQD": 100.0, "HYG": 80.0, "IEF": 90.0}
CLOSE_NOW = {"SPY": 102.0, "LQD": 99.0, "HYG": 80.0, "IEF": 90.0}
DIVIDENDS = {"SPY": 0.0, "LQD": 0.0, "HYG": 0.40, "IEF": 0.10}

BOOK_TOTAL = 57.0
CARRY_TOTAL = 17.0
PRICE_TOTAL = 40.0


def _compute(**overrides):
    kwargs = dict(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=POSITIONS,
        close_prev=CLOSE_PREV,
        close_now=CLOSE_NOW,
        dividends=DIVIDENDS,
    )
    kwargs.update(overrides)
    return compute_sleeve_pnl(**kwargs)


# ------------------------------------------------------------------ the per-ticker arithmetic


def test_price_is_yesterdays_shares_times_the_close_difference() -> None:
    rows = per_ticker_pnl(
        [("SPY", 10.0), ("LQD", -20.0)], CLOSE_PREV, CLOSE_NOW, {}
    )
    by_ticker = {r["ticker"]: r for r in rows}

    assert by_ticker["SPY"]["price_pnl"] == pytest.approx(20.0)
    assert by_ticker["SPY"]["carry_pnl"] == 0.0
    # A short position whose price falls gains: the sign of the shares does the work.
    assert by_ticker["LQD"]["price_pnl"] == pytest.approx(20.0)


def test_carry_is_yesterdays_shares_times_the_distribution() -> None:
    rows = per_ticker_pnl(
        [("HYG", 50.0), ("IEF", -30.0)], CLOSE_PREV, CLOSE_NOW, DIVIDENDS
    )
    by_ticker = {r["ticker"]: r for r in rows}

    assert by_ticker["HYG"]["carry_pnl"] == pytest.approx(20.0)
    # A short pays the distribution rather than receiving it.
    assert by_ticker["IEF"]["carry_pnl"] == pytest.approx(-3.0)


def test_a_ticker_with_no_distribution_has_no_carry() -> None:
    rows = per_ticker_pnl(
        [("GLD", 10.0)], {"GLD": 100.0}, {"GLD": 101.0}, DIVIDENDS
    )
    assert rows[0]["carry_pnl"] == 0.0
    assert rows[0]["price_pnl"] == pytest.approx(10.0)


def test_a_nan_distribution_is_treated_as_no_distribution() -> None:
    """`nan or 0.0` returns the NaN, which would poison every sleeve row.

    A NaN compares false against the tolerance, so a poisoned row would slip through
    the gate rather than trip it. The dividend matrix is zero on a non-pay date, so an
    unreadable value means nothing was paid.
    """
    rows = per_ticker_pnl(
        [("HYG", 50.0)], CLOSE_PREV, CLOSE_NOW, {"HYG": float("nan")}
    )
    assert rows[0]["carry_pnl"] == 0.0
    assert rows[0]["total"] == 0.0

    result = _compute(dividends={"HYG": float("nan")})
    assert result.ok, result.reason
    assert all(row["total"] == row["total"] for row in result.rows)


def test_only_holdings_are_carried_into_the_arithmetic() -> None:
    """A snapshot reports what is held, so a ticker absent from it is a zero position."""
    result = _compute(positions=[{"ticker": "SPY", "shares": 10}])
    assert result.book_gross_pnl == pytest.approx(20.0)


# ------------------------------------------------------------------ the grouping


def test_the_four_sleeves_carry_the_right_totals() -> None:
    result = _compute()

    assert result.status == STATUS_OK, result.reason
    by_sleeve = {row["sleeve"]: row for row in result.rows}

    assert set(by_sleeve) == {"equity", "rates", "credit", "commodity"}
    assert by_sleeve["equity"]["price_pnl"] == pytest.approx(20.0)
    assert by_sleeve["equity"]["carry_pnl"] == 0.0
    assert by_sleeve["credit"]["price_pnl"] == pytest.approx(20.0)
    assert by_sleeve["credit"]["carry_pnl"] == pytest.approx(20.0)
    assert by_sleeve["rates"]["price_pnl"] == 0.0
    assert by_sleeve["rates"]["carry_pnl"] == pytest.approx(-3.0)
    # A sleeve with nothing held is present and zero, not missing: the table is
    # rectangular so a chart never has to guess what a gap means.
    assert by_sleeve["commodity"]["total"] == 0.0


def test_every_sleeve_row_carries_the_session_book_total() -> None:
    result = _compute()
    assert all(row["book_total"] == pytest.approx(BOOK_TOTAL) for row in result.rows)
    assert {row["trade_date"] for row in result.rows} == {TRADE_DATE}


def test_the_sleeve_totals_sum_to_the_book_total() -> None:
    """The identity the table is meant to be auditable by."""
    result = _compute()
    assert sum(row["total"] for row in result.rows) == pytest.approx(BOOK_TOTAL)


def test_the_carry_price_split_sums_to_the_book() -> None:
    result = _compute()
    carry = sum(row["carry_pnl"] for row in result.rows)
    price = sum(row["price_pnl"] for row in result.rows)
    assert carry == pytest.approx(CARRY_TOTAL)
    assert price == pytest.approx(PRICE_TOTAL)
    assert carry + price == pytest.approx(BOOK_TOTAL)


# ------------------------------------------------------------------ the reconciliation gate


def test_a_reconciled_day_reports_a_residual_inside_the_tolerance() -> None:
    result = _compute()
    assert result.residual == 0.0
    assert result.tolerance == pytest.approx(BOOK_TOTAL * 1e-6)
    assert abs(result.residual) <= result.tolerance


def test_a_dropped_sleeve_shows_up_as_a_residual_and_trips_the_gate() -> None:
    """The realistic grouping failure: an instrument that never reached a sleeve.

    The sleeve rows are a regrouping of the per-ticker vector, so anything the
    grouping loses has to appear as a residual. Nothing else in the pipeline compares
    the two, which is why this is the check that decides whether a day is stored.
    """
    tickers = per_ticker_pnl(
        [("SPY", 10.0), ("HYG", 50.0)], CLOSE_PREV, CLOSE_NOW, DIVIDENDS
    )
    rows = group_by_sleeve(tickers, TRADE_DATE, book_total=40.0)
    broken = [row for row in rows if row["sleeve"] != "credit"]

    residual, tolerance = reconcile(tickers, broken)

    assert residual == pytest.approx(-20.0), "the dropped sleeve's whole contribution"
    assert abs(residual) > tolerance


def test_the_tolerance_scales_with_the_day_and_has_a_floor() -> None:
    """A tiny book must not get a vacuously tight gate, and a big one must not get slack."""
    tickers = per_ticker_pnl([("SPY", 10.0)], CLOSE_PREV, CLOSE_NOW, {})
    rows = group_by_sleeve(tickers, TRADE_DATE, book_total=20.0)

    _, small = reconcile(tickers, rows)
    assert small >= MIN_TOLERANCE

    big = [dict(row, total=row["total"] * 1_000.0) for row in rows]
    big_tickers = [dict(row, total=row["total"] * 1_000.0) for row in tickers]
    _, large = reconcile(big_tickers, big)
    assert large == pytest.approx(20_000.0 * 1e-6)


# ------------------------------------------------------------------ refusal, not zero


@pytest.mark.parametrize(
    "overrides,expected_in_reason",
    [
        ({"positions": []}, "no share counts"),
        ({"positions": [{"ticker": "SPY", "shares": None}]}, "no share count"),
        ({"positions": [{"ticker": "ZZZ", "shares": 10}]}, "no sleeve"),
        ({"positions": [{"ticker": "SPY", "shares": 10}], "close_prev": {}}, "no close for"),
        ({"positions": [{"ticker": "SPY", "shares": 10}], "close_now": {}}, "no close for"),
        (
            {"positions": [{"ticker": "SPY", "shares": 10}], "close_now": {"SPY": 0.0}},
            "no close for",
        ),
    ],
)
def test_a_day_that_cannot_be_computed_is_refused_with_a_reason(
    overrides: dict, expected_in_reason: str
) -> None:
    """Every one of these is a missing input, and a missing input is not a flat day."""
    result = _compute(**overrides)

    assert result.status == STATUS_UNCOMPARABLE, result
    assert not result.ok
    assert result.rows == [], "a refused day has no rows to write"
    assert result.reason and expected_in_reason in result.reason


def test_a_flat_book_is_refused_rather_than_written_as_zero() -> None:
    """Zero shares held means the snapshot says nothing about a book.

    The positions table only carries rows for instruments that are held, so a
    snapshot with no share counts is indistinguishable from one that was never
    written. Refusing is the only reading that cannot invent a zero.
    """
    result = _compute(positions=[{"ticker": "SPY", "shares": 0}])
    assert result.status == STATUS_UNCOMPARABLE
    assert result.rows == []


def test_the_reason_names_the_instrument_and_the_snapshot_date() -> None:
    result = _compute(positions=[{"ticker": "HYG", "shares": None}])
    assert "HYG" in (result.reason or "")
    assert PRIOR_DATE in (result.reason or "")


# ------------------------------------------------------------------ the session pair


def test_previous_session_is_the_one_before_the_last() -> None:
    index = pd.DatetimeIndex(["2026-09-30", "2026-10-01", "2026-10-02"])
    assert previous_session(index) == "2026-10-01"


def test_a_single_session_has_no_previous_one() -> None:
    assert previous_session(pd.DatetimeIndex(["2026-10-02"])) is None


# ------------------------------------------------------------------ the write path


def _stub_session(monkeypatch, *, positions, dividends_as_of_value=TRADE_DATE):
    """Stub the readers and capture writes. No database, no data files.

    `dividends_as_of_value` is the refresh date the cache reports; the default is the
    session's own date, which is fresh.
    """
    import dashboard.supabase_client as sb
    import signals.dividends as div_mod
    import signals.etf_universe as etf

    index = pd.DatetimeIndex([PRIOR_DATE, TRADE_DATE])
    close = pd.DataFrame(
        {t: [CLOSE_PREV[t], CLOSE_NOW[t]] for t in CLOSE_PREV}, index=index
    )
    written: list[dict] = []

    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(
        div_mod,
        "load_dividend_matrix",
        lambda tickers, close_index, **k: pd.DataFrame(
            {t: [0.0, DIVIDENDS[t]] for t in DIVIDENDS}, index=index
        ),
    )
    monkeypatch.setattr(
        div_mod, "dividends_as_of", lambda raw_dir=None: dividends_as_of_value
    )
    monkeypatch.setattr(sb, "fetch_position_snapshot", lambda d: list(positions))
    monkeypatch.setattr(
        sb, "write_daily_sleeve_pnl", lambda rows: written.extend(rows) or True
    )
    return written


def test_the_session_is_written_as_four_sleeve_rows(monkeypatch) -> None:
    written = _stub_session(monkeypatch, positions=POSITIONS)

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is True, report
    assert report["prior_date"] == PRIOR_DATE
    assert report["book_gross_pnl"] == pytest.approx(BOOK_TOTAL)
    assert report["carry_total"] == pytest.approx(CARRY_TOTAL)
    assert report["price_total"] == pytest.approx(PRICE_TOTAL)
    assert report["residual"] == 0.0

    assert len(written) == 4
    assert {row["sleeve"] for row in written} == {"equity", "rates", "credit", "commodity"}
    assert {row["trade_date"] for row in written} == {TRADE_DATE}
    assert sum(row["total"] for row in written) == pytest.approx(BOOK_TOTAL)
    assert all(row["book_total"] == pytest.approx(BOOK_TOTAL) for row in written)


def test_a_session_with_no_prior_snapshot_is_not_computed_at_all(monkeypatch) -> None:
    """The first day, and any day after a run that never wrote a snapshot."""
    written = _stub_session(monkeypatch, positions=[])

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == STATUS_UNCOMPARABLE
    assert PRIOR_DATE in report["reason"]
    assert written == [], "a day that cannot be computed writes nothing"


def test_a_mismatching_day_blocks_the_write(monkeypatch) -> None:
    """The gate's whole point: no reconciliation, no row.

    Injected at the computation boundary rather than by corrupting the inputs, because
    what has to be proven is the writer's guard: given a result that did not reconcile,
    it must not reach Supabase.
    """
    import risk.sleeve_pnl as sp

    written = _stub_session(monkeypatch, positions=POSITIONS)
    result = compute_sleeve_pnl(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=POSITIONS,
        close_prev=CLOSE_PREV,
        close_now=CLOSE_NOW,
        dividends=DIVIDENDS,
    )
    mismatched = sp.SleevePnlResult(
        STATUS_MISMATCH,
        "the sleeves do not reconcile: residual $20.00",
        book_gross_pnl=result.book_gross_pnl,
        residual=20.0,
        tolerance=result.tolerance,
    )
    monkeypatch.setattr(sp, "compute_sleeve_pnl", lambda **kwargs: mismatched)

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == STATUS_MISMATCH
    assert "do not reconcile" in report["reason"]
    assert written == [], "a session that does not reconcile must not be stored"


def test_a_rejected_write_is_reported_rather_than_swallowed(monkeypatch) -> None:
    """Supabase refusing the write is not the same as a reconciled day on file."""
    import dashboard.supabase_client as sb

    _stub_session(monkeypatch, positions=POSITIONS)
    monkeypatch.setattr(sb, "write_daily_sleeve_pnl", lambda rows: False)

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == "write_failed"
    assert "rejected" in report["reason"]


def test_the_log_carries_the_residual_whether_or_not_it_passes(
    monkeypatch, caplog
) -> None:
    """A growing residual has to be visible before it ever blocks a write."""
    import logging

    _stub_session(monkeypatch, positions=POSITIONS)

    with caplog.at_level(logging.INFO):
        record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert "residual=" in caplog.text
    assert "sleeve P&L for " in caplog.text


def test_bookkeeping_failures_never_escape(monkeypatch) -> None:
    """A raise in here must not be able to fail a run whose signal is already written."""
    import dashboard.supabase_client as sb

    # Stub the session first, or the call returns early on the missing close and never
    # reaches the reader that is meant to raise.
    _stub_session(monkeypatch, positions=POSITIONS)

    def _boom(trade_date):
        raise ConnectionError("supabase unreachable")

    monkeypatch.setattr(sb, "fetch_position_snapshot", _boom)

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == "error"
    assert "ConnectionError" in report["reason"]


# ------------------------------------------------------------------ the email line


def _summary():
    from execution.daily_summary import RunSummary

    return RunSummary(job="run_signal", run_date=TRADE_DATE)


def test_a_written_day_reaches_the_email_with_its_numbers() -> None:
    from execution.daily_summary import body_for

    summary = _summary()
    summary.record_sleeve_pnl({
        "written": True,
        "trade_date": TRADE_DATE,
        "prior_date": PRIOR_DATE,
        "sleeves": {"equity": 20.0, "rates": -3.0, "credit": 40.0, "commodity": 0.0},
        "carry_total": CARRY_TOTAL,
        "price_total": PRICE_TOTAL,
        "book_gross_pnl": BOOK_TOTAL,
    })

    body = body_for(summary)
    assert f"Sleeve P&L {TRADE_DATE} on the {PRIOR_DATE} book:" in body
    assert "equity $+20.00" in body
    assert "carry $+17.00" in body
    assert "price $+40.00" in body
    assert "book $+57.00" in body


def test_a_mismatch_reaches_the_email_as_a_failure() -> None:
    from execution.daily_summary import body_for

    summary = _summary()
    summary.record_sleeve_pnl({
        "written": False,
        "status": STATUS_MISMATCH,
        "reason": "residual $20.00 over tolerance",
    })

    assert "Sleeve P&L FAILED (mismatch): residual $20.00 over tolerance" in body_for(summary)


def test_an_uncomparable_day_reaches_the_email_without_alarm() -> None:
    """The first day is expected, so it must not read like a failure."""
    from execution.daily_summary import body_for

    summary = _summary()
    summary.record_sleeve_pnl({
        "written": False,
        "status": STATUS_UNCOMPARABLE,
        "reason": f"no position snapshot for {PRIOR_DATE}",
    })

    body = body_for(summary)
    assert "Sleeve P&L: not computed (" in body
    assert "FAILED" not in body


def test_a_failed_sleeve_pnl_does_not_change_the_subject() -> None:
    """It is a reporting failure, not a trading one.

    A basis-refresh failure lifts the subject to [SKIP] because it leaves the sizing
    basis stale. A sleeve decomposition that does not reconcile leaves the book
    untouched, and letting it read as [SKIP] on a day the book traded would be worse
    than saying nothing.
    """
    from execution.daily_summary import OK, subject_for

    summary = _summary()
    summary.record_sleeve_pnl({
        "written": False,
        "status": STATUS_MISMATCH,
        "reason": "residual over tolerance",
    })

    assert subject_for(summary) == f"{OK} run_signal {TRADE_DATE}"


def test_the_session_date_is_the_close_date_not_the_wall_clock() -> None:
    """The row is dated by the session its closes describe.

    The job runs on the evening of that session, but when the price cache lags, the
    last session available is the previous one and the row must be dated that way
    rather than stamped with today.
    """
    assert isinstance(date.fromisoformat(TRADE_DATE), date)
    result = _compute()
    assert {row["trade_date"] for row in result.rows} == {TRADE_DATE}
    assert TRADE_DATE != PRIOR_DATE


# ------------------------------------------------------------------ the ex-date offset


def test_an_ex_date_reads_as_roughly_zero_for_a_long() -> None:
    """Why the price leg uses the unadjusted close and why a carry leg exists at all.

    On an ex-date of $1.00 the unadjusted close drops by about $1.00. On 10 shares that
    is -$10 of price and +$10 of carry, so the position's session is flat: the holder
    was paid, not damaged. A decomposition that saw only the price leg would book a loss
    that never happened, which is exactly what a stale distribution cache produced.
    """
    result = compute_sleeve_pnl(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=[{"ticker": "HYG", "shares": 10}],
        close_prev={"HYG": 100.0},
        close_now={"HYG": 99.0},
        dividends={"HYG": 1.0},
    )

    assert result.status == STATUS_OK, result.reason
    credit = next(row for row in result.rows if row["sleeve"] == "credit")
    assert credit["price_pnl"] == pytest.approx(-10.0)
    assert credit["carry_pnl"] == pytest.approx(10.0)
    assert credit["total"] == pytest.approx(0.0)
    assert result.book_gross_pnl == pytest.approx(0.0)


def test_an_ex_date_reads_as_roughly_zero_for_a_short() -> None:
    """The same day from the other side: the short pays the distribution.

    A short in a name that goes ex is not a windfall. The price leg gains the amount
    the close dropped and the carry leg gives it back, and both signs matter.
    """
    result = compute_sleeve_pnl(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=[{"ticker": "HYG", "shares": -10}],
        close_prev={"HYG": 100.0},
        close_now={"HYG": 99.0},
        dividends={"HYG": 1.0},
    )

    credit = next(row for row in result.rows if row["sleeve"] == "credit")
    assert credit["price_pnl"] == pytest.approx(10.0)
    assert credit["carry_pnl"] == pytest.approx(-10.0)
    assert credit["total"] == pytest.approx(0.0)


def test_a_realistic_ex_date_offsets_rather_than_cancels() -> None:
    """The close does not drop by exactly the distribution, so the legs roughly offset.

    A $1.00 distribution on a $100 close that drops $0.98 leaves $0.20 on 10 shares: 2%
    of the distribution, which is the carry leg explaining nearly all of the move.
    """
    result = compute_sleeve_pnl(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=[{"ticker": "HYG", "shares": 10}],
        close_prev={"HYG": 100.0},
        close_now={"HYG": 99.02},
        dividends={"HYG": 1.0},
    )

    credit = next(row for row in result.rows if row["sleeve"] == "credit")
    distribution = 10.0
    assert abs(credit["total"]) <= 0.05 * distribution, (
        "the residual must be small against the distribution, not the same size as it"
    )


def test_without_the_distribution_the_same_day_reads_as_a_fake_price_loss() -> None:
    """The negative control for the two tests above.

    This is the bug: the close drops, no carry is recorded, and a flat session is
    reported as a $10 loss. It is why a cache that cannot vouch for the session refuses
    it instead of computing it.
    """
    result = compute_sleeve_pnl(
        trade_date=TRADE_DATE,
        prior_date=PRIOR_DATE,
        positions=[{"ticker": "HYG", "shares": 10}],
        close_prev={"HYG": 100.0},
        close_now={"HYG": 99.0},
        dividends={},
    )

    credit = next(row for row in result.rows if row["sleeve"] == "credit")
    assert credit["carry_pnl"] == 0.0
    assert credit["total"] == pytest.approx(-10.0)


# ------------------------------------------------------------------ distribution freshness


def test_a_distribution_cache_older_than_the_session_refuses_it(monkeypatch) -> None:
    """A cache that predates the session cannot vouch for its payouts."""
    written = _stub_session(
        monkeypatch, positions=POSITIONS, dividends_as_of_value=PRIOR_DATE
    )

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == STATUS_UNCOMPARABLE
    assert f"last refreshed on {PRIOR_DATE}" in report["reason"]
    assert "read as a price loss" in report["reason"]
    assert written == []


def test_an_unrecorded_refresh_date_refuses_the_session(monkeypatch) -> None:
    """Never refreshed is not fresh: a fresh container has the committed cache and no
    marker, and its coverage is unknown."""
    written = _stub_session(
        monkeypatch, positions=POSITIONS, dividends_as_of_value=None
    )

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is False
    assert report["status"] == STATUS_UNCOMPARABLE
    assert "never refreshed" in report["reason"]
    assert written == []


def test_a_cache_refreshed_for_the_session_computes_it(monkeypatch) -> None:
    """Negative control for the two refusals: a current cache computes normally."""
    written = _stub_session(monkeypatch, positions=POSITIONS)

    report = record_daily_sleeve_pnl(as_of_date=TRADE_DATE)

    assert report["written"] is True, report
    assert len(written) == 4
