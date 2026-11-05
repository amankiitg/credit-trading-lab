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


def test_a_short_sold_below_the_prior_close_is_a_gain() -> None:
    """Selling 100 at 81.00 into a book that closed at 81.84 earns the 0.84 difference."""
    pnl = ep.execution_pnl(
        [_fill("TLT", 100, "sell_short", 81.00)],
        {"TLT": 81.84},
        sleeve_of={"TLT": "rates"},
        session="2026-11-02",
    )

    assert pnl.fill_count == 1
    assert pnl.total == pytest.approx(-100 * (81.00 - 81.84))
    assert pnl.total > 0, "a short sale below the prior close is a gain"
    assert pnl.by_sleeve == {"rates": pytest.approx(84.0)}


def test_a_buy_above_the_prior_close_costs_the_difference() -> None:
    pnl = ep.execution_pnl(
        [_fill("HYG", 50, "buy", 77.20)],
        {"HYG": 77.43},
        sleeve_of={"HYG": "credit"},
        session="2026-11-02",
    )

    assert pnl.total == pytest.approx(50 * (77.20 - 77.43))
    assert pnl.total < 0


def test_a_fill_without_a_prior_close_is_named_not_guessed() -> None:
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
    assert pnl.total == pytest.approx(-10 * (81.0 - 81.84))


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
    """Paper never debits a short its distributions; the note has to say so."""
    note = ep.short_distribution_note(-436.18)

    assert "does not debit short distributions" in note
    assert "436.18" in note

    assert "no adjustment" in ep.short_distribution_note(0.0)


# ------------------------------------------------------------------ the job's hook


def test_the_hook_reports_an_error_instead_of_raising(monkeypatch) -> None:
    """A broker hiccup must not cost the session its sleeve rows."""

    def _boom(*args, **kwargs):
        raise RuntimeError("alpaca down")

    import execution.alpaca_paper as ap

    monkeypatch.setattr(ap, "connect", _boom)

    report = ep.record_execution_component(
        session="2026-11-02", close_prev={"TLT": 81.84}, carry_total=-436.18,
        book_total=-358.15,
    )

    assert report["status"] == "error"
    assert "alpaca down" in report["reason"]
    assert report["note"], "the short-distribution note is still supplied"


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
