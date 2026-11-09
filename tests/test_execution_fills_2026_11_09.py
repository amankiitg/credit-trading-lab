"""Regression: the 2026-11-09 evening email reported "0 fills" for four real orders.

run_execution filled four position-reducing legs that morning, gross $1,619.32:

    SPY  sell 0.525339018  (long  -> 35.343065334 from 35.868404352)
    EEM  buy  11           (short -> -394 from -405)
    TLT  buy  3            (short -> -318 from -321)
    HYG  buy  4            (short -> -317 from -321)

Step 8 reported "Execution P&L: 0 fills, +0.00 total" anyway. The read never happened:
`fetch_session_fills` imported `GetActivitiesRequest` from `alpaca.trading.requests`,
which alpaca-py does not have (0.45.0 checked; TradingClient has no activity method
either). The ImportError was caught by a broad `except` that returned `[]`, so an
unreadable broker was rendered as a quiet trading day.

These tests use Alpaca's real timestamp shape (UTC, five fractional digits), today's
share counts, and today's unadjusted closes. Fill prices are the tape around 10:31-10:33
ET, calibrated so the four legs gross $1,619.32 -- the split between legs is not in the
position snapshots, but gross and share counts are, and the execution total is pinned to
about a cent by them.
"""

from __future__ import annotations

import pytest

import execution.alpaca_paper as ap
import risk.execution_pnl as ep
from execution.daily_summary import RunSummary, body_for
from signals.etf_universe import ASSET_CLASS

# Alpaca stamps a fill as UTC with five fractional digits, which Python < 3.11 cannot
# parse without normalising. These are today's four fills, sanitized of account ids.
TODAY_FILLS = [
    {
        "id": "00000000-0000-4000-8000-000000000001",
        "activity_type": "FILL",
        "symbol": "SPY",
        "qty": "0.525339018",
        "side": "sell",
        "price": "749.9005",
        "transaction_time": "2026-11-09T15:31:52.412345Z",
    },
    {
        "id": "00000000-0000-4000-8000-000000000002",
        "activity_type": "FILL",
        "symbol": "EEM",
        "qty": "11",
        "side": "buy",
        "price": "60.88",
        "transaction_time": "2026-11-09T15:31:53.087621Z",
    },
    {
        "id": "00000000-0000-4000-8000-000000000003",
        "activity_type": "FILL",
        "symbol": "TLT",
        "qty": "3",
        "side": "buy",
        "price": "81.956",
        "transaction_time": "2026-11-09T15:31:53.612345Z",
    },
    {
        "id": "00000000-0000-4000-8000-000000000004",
        "activity_type": "FILL",
        "symbol": "HYG",
        "qty": "4",
        "side": "buy",
        "price": "77.455",
        "transaction_time": "2026-11-09T15:31:54.203456Z",
    },
]

# Today's unadjusted closes, as written to settings.signal_close_prices for 2026-11-09.
TODAY_CLOSES = {
    "SPY": 752.690002,
    "EEM": 61.029999,
    "TLT": 81.959999,
    "HYG": 77.510002,
}

TODAY_GROSS = sum(
    abs(float(f["qty"])) * float(f["price"]) for f in TODAY_FILLS
)


class _ReadingClient:
    """A stand-in for TradingClient that records the raw GET it was asked for."""

    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path, data=None, **kwargs):
        self.calls.append((path, data))
        return self.response


class _BrokenClient:
    """The 2026-11-09 failure: the read raises and the caller must not call it 0 fills."""

    def get(self, path, data=None, **kwargs):
        raise ImportError(
            "cannot import name 'GetActivitiesRequest' from 'alpaca.trading.requests'"
        )


# ------------------------------------------------------------------ the read itself


def test_the_read_is_a_raw_get_on_the_activities_endpoint() -> None:
    """No SDK request class and no order endpoint: one GET on /v2/account/activities."""
    client = _ReadingClient(TODAY_FILLS)

    fills = ap.fetch_session_fills(client, "2026-11-09")

    assert len(fills) == 4
    assert client.calls == [
        (
            "/account/activities",
            {
                "activity_types": "FILL",
                "after": "2026-11-08T00:00:00Z",
                "until": "2026-11-11T00:00:00Z",
                "direction": "asc",
                "page_size": 100,
            },
        )
    ], "one read, a day either side, fills only"
    assert [f["symbol"] for f in fills] == ["SPY", "EEM", "TLT", "HYG"]


def test_a_full_page_is_followed_to_the_next_one() -> None:
    """A busier session must not be silently cut at the page size.

    The endpoint accepts the last activity's id as page_token when sorting ascending.
    Paging is the difference between a complete fill history and the first 100 rows of
    one, and the old code could not page at all because it never reached the endpoint.
    """
    page_one = [
        {**TODAY_FILLS[0], "id": f"00000000-0000-4000-8000-{i:012d}"}
        for i in range(100)
    ]
    page_two = [{**TODAY_FILLS[1], "id": f"00000000-0000-4000-8000-{100:012d}"}]

    class _Paged(_ReadingClient):
        def get(self, path, data=None, **kwargs):
            self.calls.append((path, data))
            return page_one if len(self.calls) == 1 else page_two

    client = _Paged(None)

    fills = ap.fetch_session_fills(client, "2026-11-09")

    assert len(fills) == 101
    assert client.calls[1][1]["page_token"] == page_one[-1]["id"]


def test_a_failed_read_raises_instead_of_returning_an_empty_day() -> None:
    """The exact 2026-11-09 path: ImportError must not become [].

    An empty list means "this session traded nothing". A failed read means "I do not
    know". Collapsing the second into the first is what put "0 fills, +0.00 total" on
    a morning with four filled orders.
    """
    with pytest.raises(ap.ActivitiesReadError) as excinfo:
        ap.fetch_session_fills(_BrokenClient(), "2026-11-09")

    assert "activity history read failed" in str(excinfo.value)
    assert "GetActivitiesRequest" in str(excinfo.value)


def test_the_email_says_not_available_when_the_read_fails(monkeypatch) -> None:
    """A broker that will not answer must never render as 0 fills."""
    monkeypatch.setattr(ap, "connect", lambda dry_run=False: _BrokenClient())

    report = ep.record_execution_component(
        session="2026-11-09",
        close_now=TODAY_CLOSES,
        sessions=["2026-11-06", "2026-11-09"],
        carry_total=0.0,
        carry_short=0.0,
        book_total=190.529532893064,
    )

    summary = RunSummary(job="run_signal", run_date="2026-11-09")
    summary.record_sleeve_pnl({
        "written": True,
        "trade_date": "2026-11-09",
        "prior_date": "2026-11-06",
        "sleeves": {"equity": 688.89, "rates": 4.08, "credit": -123.90, "commodity": -378.54},
        "carry_total": 0.0,
        "price_total": 190.529532893064,
        "book_gross_pnl": 190.529532893064,
        "execution": report,
    })

    body = body_for(summary)
    assert report["status"] == "error"
    assert "Execution P&L: not available" in body
    assert "0 fills" not in body, "an unreadable broker is not a quiet trading day"


# ------------------------------------------------------------------ the session mapping


def test_todays_utc_timestamps_map_to_the_new_york_session() -> None:
    """All four fills are 2026-11-09 in both readings -- this is not where it broke."""
    assert [ep.session_of(f) for f in TODAY_FILLS] == ["2026-11-09"] * 4


def test_an_after_hours_fill_does_not_roll_into_the_next_session() -> None:
    """20:30 ET on 11-09 is 01:30 UTC on 11-10; the session is still 11-09."""
    late = {"transaction_time": "2026-11-10T01:30:00.123456Z"}

    assert ep.session_of(late) == "2026-11-09"


# ------------------------------------------------------------------ the arithmetic


def test_todays_four_fills_price_to_about_flat_against_todays_closes() -> None:
    """The traded leg is small, but it is not zero and it is four fills.

    Hand arithmetic from the fixtures: the SPY sale at 749.90 into a 752.69 close gives
    back 1.47, the EEM cover at 60.88 into 61.03 earns 1.65, TLT is flat, HYG earns 0.22.
    """
    pnl = ep.execution_pnl(TODAY_FILLS, TODAY_CLOSES, sleeve_of=ASSET_CLASS)

    assert pnl.fill_count == 4
    assert pnl.skipped == []
    assert pnl.total == pytest.approx(0.4166, abs=0.001)
    assert pnl.by_sleeve == {
        "equity": pytest.approx(0.1846, abs=0.001),
        "rates": pytest.approx(0.0120, abs=0.001),
        "credit": pytest.approx(0.2200, abs=0.001),
    }


def test_the_fixture_is_todays_gross_notional() -> None:
    """The four legs must still be the $1,619.32 the execution job reported."""
    assert TODAY_GROSS == pytest.approx(1619.32, abs=0.005)


# ------------------------------------------------------------------ the email


def _summary_with_execution(report: dict) -> RunSummary:
    summary = RunSummary(job="run_signal", run_date="2026-11-09")
    summary.record_sleeve_pnl({
        "written": True,
        "trade_date": "2026-11-09",
        "prior_date": "2026-11-06",
        "sleeves": {"equity": 688.89, "rates": 4.08, "credit": -123.90, "commodity": -378.54},
        "carry_total": 0.0,
        "price_total": 190.529532893064,
        "book_gross_pnl": 190.529532893064,
        "execution": report,
    })
    return summary


def test_todays_email_has_four_fills_no_distribution_claim_and_a_residual(monkeypatch) -> None:
    """The three wrong lines of 2026-11-09, asserted together.

    - "0 fills" was a swallowed ImportError (fixed by fetch_session_fills raising).
    - "paper already carries the distributions" was carry_total == 0 being read as a
      measured fact about the broker; there was no distribution in the session.
    - the residual was computed and then never rendered unless it warned.
    """
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, day: {"2026-11-07": 98000.0, "2026-11-10": 98184.95},
    )
    client = _ReadingClient(TODAY_FILLS)

    report = ep.record_execution_component(
        session="2026-11-09",
        close_now=TODAY_CLOSES,
        sessions=["2026-11-06", "2026-11-09"],
        carry_total=0.0,
        carry_short=0.0,
        book_total=190.529532893064,
        client=client,
    )

    summary = _summary_with_execution(report)
    body = body_for(summary)

    assert "Execution P&L: 4 fills, +0.42 total" in body
    assert "(equity +0.18, rates +0.01, credit +0.22)" in body
    assert "paper already carries the distributions" not in body
    assert "does not debit short distributions" not in body, "no distribution today"
    assert "Reconciliation: pipeline + execution +190.95" in body
    assert "equity change +184.95" in body
    assert "(residual -6.00)" in body


def test_only_the_short_leg_is_added_back(monkeypatch) -> None:
    """Paper pays a long distribution, so adding the whole book's carry back is wrong."""
    monkeypatch.setattr(ap, "fetch_session_fills", lambda client, day: [])
    monkeypatch.setattr(
        ap, "fetch_equity_history",
        lambda client, day: {"2026-11-07": 98000.0, "2026-11-10": 98000.0},
    )

    long_paid = ep.record_execution_component(
        session="2026-11-09",
        close_now=TODAY_CLOSES,
        sessions=["2026-11-06", "2026-11-09"],
        carry_total=120.0,
        carry_short=0.0,
        book_total=0.0,
    )

    assert long_paid["paper_add_back"] == pytest.approx(0.0)
    assert long_paid["note"] is None

    short_owed = ep.record_execution_component(
        session="2026-11-09",
        close_now=TODAY_CLOSES,
        sessions=["2026-11-06", "2026-11-09"],
        carry_total=-436.1833,
        carry_short=-436.1833,
        book_total=0.0,
    )

    assert short_owed["paper_add_back"] == pytest.approx(436.1833)
    assert "does not debit short distributions" in short_owed["note"]
