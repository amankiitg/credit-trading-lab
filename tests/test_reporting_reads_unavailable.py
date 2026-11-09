"""Reporting reads that fail must say so, not return an empty value.

The 2026-11-09 lesson generalises. A broad except around a broker or database read
used to turn a fault into a value the rest of the run could not tell from a fact:

  - fetch_equity_history returned {}  -> "no end-of-day equity on both sides"
  - snapshot.refresh_snapshot used get_live_nav, whose fallback is a $100,000 NAV
    -> a made-up account size written to settings.live_nav and into every weight
  - fetch_position_snapshot returned []  -> "no position snapshot for that date"
  - fetch_daily_sleeve_pnl returned []   -> "not enough sessions to compare"
  - raw_repair's default bar lookup returned {}  -> a repair with nothing to do

Each is now either raised or named. These tests pin the naming, and pin that the
dashboard-facing defaults are unchanged, because the dashboard reads the same
functions and must keep its existing contract.
"""

from __future__ import annotations

import pytest

import dashboard.supabase_client as sb
import execution.alpaca_paper as ap
import execution.snapshot as snapshot
import signals.raw_repair as raw_repair


# ------------------------------------------------------------------ equity history


class _BrokenPortfolioHistory:
    """A client whose portfolio-history read raises, like a real transport fault."""

    def get_portfolio_history(self, request):
        raise ConnectionError("read timed out")


def test_a_failed_equity_read_raises_instead_of_returning_an_empty_mapping() -> None:
    with pytest.raises(ap.EquityHistoryReadError) as raised:
        ap.fetch_equity_history(_BrokenPortfolioHistory(), "2026-11-09")

    assert "equity history read failed: ConnectionError" in str(raised.value)


# ------------------------------------------------------------------ the NAV snapshot


class _BrokenAccount:
    def get_account(self):
        raise ConnectionError("account read timed out")


def test_a_failed_nav_read_writes_nothing_and_names_the_failure(monkeypatch) -> None:
    """The old path wrote PAPER_NAV_DEFAULT=100,000 as the account's NAV."""
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(sb, "set_setting", lambda key, value: written.append((key, value)) or True)
    monkeypatch.setattr(sb, "write_positions", lambda rows: written.append(("positions", str(len(rows)))) or True)

    report = snapshot.refresh_snapshot(
        run_date="2026-11-09", client=_BrokenAccount(), log=None
    )

    assert report["written"] is False
    assert report["failed"] is True
    assert report["nav"] is None
    assert "live NAV unreadable" in report["skipped"]
    assert written == []  # no live_nav, no position rows


def test_get_live_nav_keeps_its_sizing_fallback_but_read_live_nav_does_not() -> None:
    """The order-sizing guard keeps the default; a reported number cannot."""
    assert ap.get_live_nav(_BrokenAccount()) == ap.PAPER_NAV_DEFAULT
    with pytest.raises(ConnectionError):
        ap.read_live_nav(_BrokenAccount())


# ------------------------------------------------------------------ supabase reads


class _BrokenTable:
    def select(self, *args, **kwargs):
        raise RuntimeError("postgrest unavailable")


class _BrokenSupabase:
    def table(self, name):
        return _BrokenTable()


@pytest.fixture
def broken_supabase(monkeypatch):
    monkeypatch.setattr(sb, "get_supabase_client", lambda: _BrokenSupabase())


def test_a_failed_position_snapshot_read_can_raise_or_stay_empty(broken_supabase) -> None:
    """Sleeve P&L asks for the raise; the dashboard keeps the empty list."""
    assert sb.fetch_position_snapshot("2026-11-06") == []
    with pytest.raises(RuntimeError) as raised:
        sb.fetch_position_snapshot("2026-11-06", raise_on_error=True)
    assert "Supabase read failed" in str(raised.value)


def test_a_failed_sleeve_pnl_read_can_raise_or_stay_empty(broken_supabase) -> None:
    assert sb.fetch_daily_sleeve_pnl(limit=10) == []
    with pytest.raises(RuntimeError) as raised:
        sb.fetch_daily_sleeve_pnl(limit=10, raise_on_error=True)
    assert "Supabase read failed" in str(raised.value)


# ------------------------------------------------------------------ gap repair


def test_a_repair_that_cannot_read_the_broker_says_why(monkeypatch) -> None:
    """Credentials absent or transport down: both are unavailable, not "nothing found".

    No broker call is made here: with the credentials unset the lookup returns before
    constructing a client. That is the point -- the reason is recorded either way.
    """
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET_KEY", raising=False)

    assert raw_repair._default_bar_lookup(["SPY"], []) == {}
    assert raw_repair.last_lookup_error() == (
        "ALPACA_PAPER_API_KEY/ALPACA_PAPER_SECRET_KEY not set"
    )
