"""The Book P&L by sleeve block as the page renders it.

This pins what the dashboard does with the stored sleeve P&L: the chart appears only
when there is a stored session, the caption restates the reconciliation instead of
assuming it, the bands use the two-colour long/short scheme, and an all-zero carry is
named rather than left to look like a missing band.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"


def _session(session: str, sleeves: dict[str, tuple[float, float]]) -> list[dict]:
    return [
        {
            "trade_date": session,
            "sleeve": sleeve,
            "price_pnl": price,
            "carry_pnl": carry,
            "total": price + carry,
            "book_total": 10.0,
        }
        for sleeve, (price, carry) in sleeves.items()
    ]


ROWS = _session(
    "2026-10-01",
    {"equity": (4.0, 0.0), "rates": (0.5, 2.0), "credit": (1.0, 0.5), "commodity": (2.0, 0.0)},
) + _session(
    "2026-10-02",
    {"equity": (5.0, 0.0), "rates": (-1.0, 1.0), "credit": (3.0, 0.0), "commodity": (2.0, 0.0)},
)

POSITIONS = [
    {"trade_date": "2026-10-02", "ticker": "SPY", "signed_notional": 30_000.0},
    {"trade_date": "2026-10-02", "ticker": "EFA", "signed_notional": 20_000.0},
    {"trade_date": "2026-10-02", "ticker": "TLT", "signed_notional": -25_000.0},
    {"trade_date": "2026-10-02", "ticker": "HYG", "signed_notional": -25_000.0},
    {"trade_date": "2026-10-02", "ticker": "GLD", "signed_notional": -20_000.0},
]


def _render(monkeypatch, *, sleeve_rows, positions=()):
    """Render the real app with every read stubbed, so this block is what varies."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    monkeypatch.setattr(st, "secrets", {})

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: True)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: True)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: [])
    monkeypatch.setattr(operational, "_get_positions", lambda: list(positions))
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: [])
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: [])
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: list(sleeve_rows))
    monkeypatch.setattr(operational, "_get_proposed_trade", lambda: ([], "—", 100_000.0))
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: None)
    monkeypatch.setattr(operational, "fetch_last_cron_run", lambda job: None)
    monkeypatch.setattr(operational, "_risk_snapshot", lambda nav, pos: None)
    operational._get_last_execution_run.clear()
    operational._get_last_signal_run.clear()

    at = AppTest.from_file(str(APP), default_timeout=600)
    at.run()

    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _text(at) -> str:
    chunks = [str(m.value) for m in at.markdown]
    for kind in ("info", "success", "error", "warning"):
        chunks += [str(e.value) for e in at.get(kind)]
    chunks += [str(c.value) for c in at.caption]
    return " ".join(chunks)


def _capture_plots(monkeypatch):
    figures = []

    def _capture(fig, **kwargs):
        figures.append(fig)

    monkeypatch.setattr(operational, "_plot", _capture)
    return figures


def test_the_chart_is_drawn_when_a_session_is_stored(monkeypatch) -> None:
    figures = _capture_plots(monkeypatch)
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert len(figures) == 1
    assert figures[0].layout.title.text.startswith("Book P&L by sleeve")
    text = _text(at)
    assert "### Book P&L by sleeve" in text
    assert "each band is one sleeve's contribution" in text
    assert "Solid band = price move, lighter band = distributions received" in text


def test_no_stored_session_means_no_chart_and_a_reason(monkeypatch) -> None:
    figures = _capture_plots(monkeypatch)
    at = _render(monkeypatch, sleeve_rows=[])

    assert figures == []
    text = _text(at)
    assert "No sleeve P&L recorded yet" in text
    assert "each band is one sleeve's contribution" not in text


def test_the_caption_restates_the_reconciliation_from_the_stored_rows(monkeypatch) -> None:
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert "sum to the book total to within $0.000000" in _text(at)


def test_a_gap_between_the_sleeves_and_the_book_is_reported_not_hidden(monkeypatch) -> None:
    broken = [dict(row, book_total=12.0) for row in ROWS]

    at = _render(monkeypatch, sleeve_rows=broken)

    assert "sum to the book total to within $4.000000" in _text(at)


def test_an_all_zero_carry_is_named(monkeypatch) -> None:
    no_carry = [dict(row, carry_pnl=0.0, total=row["price_pnl"]) for row in ROWS]

    at = _render(monkeypatch, sleeve_rows=no_carry)

    assert "Carry is 0.00 in every recorded session" in _text(at)


def test_the_carry_note_is_absent_when_carry_is_recorded(monkeypatch) -> None:
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert "Carry is 0.00 in every recorded session" not in _text(at)


def test_the_bands_use_the_two_colour_long_short_scheme(monkeypatch) -> None:
    figures = _capture_plots(monkeypatch)
    _render(monkeypatch, sleeve_rows=ROWS, positions=POSITIONS)

    chart = next(fig for fig in figures if fig.layout.title.text.startswith("Book P&L by sleeve"))
    bands = [trace for trace in chart.data if trace.stackgroup]
    colours = {trace.line.color for trace in bands}

    assert len(bands) == 8, "price and carry band for each of four sleeves"
    assert colours <= {operational.LONG_COLOR, operational.SHORT_COLOR}
    assert colours == {operational.LONG_COLOR, operational.SHORT_COLOR}, (
        "the equity sleeve is long and the other three are short, so both colours appear"
    )
