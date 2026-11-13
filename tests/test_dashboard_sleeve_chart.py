"""The Book P&L by sleeve block as the page renders it.

This pins what the dashboard does with the stored sleeve P&L: the chart appears only
when there is a stored session, the caption restates the reconciliation instead of
assuming it, every line uses its asset class's colour, the four cumulative sleeve
lines sum to the cumulative book line at every point, a NaN tail is dropped, and an
all-zero carry is named rather than left to look like a missing bar.
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
    assert "one cumulative line per sleeve" in text
    assert "Bars: each sleeve's total over the period" in text


def test_no_stored_session_means_no_chart_and_a_reason(monkeypatch) -> None:
    figures = _capture_plots(monkeypatch)
    at = _render(monkeypatch, sleeve_rows=[])

    assert figures == []
    text = _text(at)
    assert "No sleeve P&L recorded yet" in text
    assert "one cumulative line per sleeve" not in text


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


def _pnl_chart(monkeypatch, **kwargs):
    figures = _capture_plots(monkeypatch)
    _render(monkeypatch, **kwargs)
    return next(
        fig for fig in figures
        if fig.layout.title.text.startswith("Book P&L by sleeve")
    )


def test_each_sleeve_line_uses_its_asset_class_colour(monkeypatch) -> None:
    chart = _pnl_chart(monkeypatch, sleeve_rows=ROWS, positions=POSITIONS)

    lines = {
        trace.name: trace for trace in chart.data
        if trace.type == "scatter" and trace.name != "Book total"
    }
    assert set(lines) == {"Equity", "Rates", "Credit", "Commodity"}
    for name, trace in lines.items():
        assert trace.line.color == operational.ASSET_CLASS_COLORS[name.lower()]

    book = next(t for t in chart.data if t.name == "Book total")
    assert book.line.color == operational.BOOK_TOTAL_COLOR
    assert book.line.width > max(t.line.width for t in lines.values()), (
        "the book total is the bold line on the panel"
    )


def test_the_sleeve_lines_sum_to_the_book_line_at_every_point(monkeypatch) -> None:
    chart = _pnl_chart(monkeypatch, sleeve_rows=ROWS)

    sleeve_lines = [
        trace for trace in chart.data
        if trace.type == "scatter" and trace.name != "Book total"
    ]
    book = next(t for t in chart.data if t.name == "Book total")

    assert len(sleeve_lines) == 4, "one line per sleeve, not a price and carry pair"
    xs = list(book.x)
    for i in range(len(xs)):
        total = sum(float(trace.y[i]) for trace in sleeve_lines)
        assert total == pytest.approx(float(book.y[i]), abs=1e-9), (
            f"at {xs[i]} the sleeves sum to {total}, the book line is {book.y[i]}"
        )


def test_the_second_panel_splits_the_period_into_price_and_carry(monkeypatch) -> None:
    chart = _pnl_chart(monkeypatch, sleeve_rows=ROWS)

    bars = {trace.name: trace for trace in chart.data if trace.type == "bar"}
    assert set(bars) == {"Price", "Carry"}
    # Two sessions: equity price 4+5, rates carry 2+1 over the period.
    equity = list(bars["Price"].x).index("Equity")
    rates = list(bars["Carry"].x).index("Rates")
    assert float(bars["Price"].y[equity]) == pytest.approx(9.0)
    assert float(bars["Carry"].y[rates]) == pytest.approx(3.0)


def test_a_nan_tail_session_is_dropped(monkeypatch) -> None:
    tail = ROWS + [
        {
            "trade_date": "2026-10-05",
            "sleeve": sleeve,
            "price_pnl": float("nan"),
            "carry_pnl": float("nan"),
            "total": float("nan"),
            "book_total": float("nan"),
        }
        for sleeve in operational.SLEEVE_ORDER
    ]

    chart = _pnl_chart(monkeypatch, sleeve_rows=tail)

    book = next(t for t in chart.data if t.name == "Book total")
    assert [str(x)[:10] for x in book.x] == ["2026-10-01", "2026-10-02"]
