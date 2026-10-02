"""Panel L as the page renders it: the stacked sleeve chart, its copy, and its palette.

`tests/test_sleeve_pnl.py` pins the computation. This pins what the dashboard does with
it: that the chart appears only when there is a stored session, that the caption
restates the reconciliation instead of assuming it, that the four bands are the shared
sleeve colours, and that an all-zero carry is named rather than left to look like a
missing band.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

matplotlib.use("Agg", force=True)

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

# Two sessions, both paying carry somewhere, so the chart has both kinds of band.
# Each session's four sleeves sum to its book_total of 10.0, which is what the gate
# guarantees in the table this reads: the panel is given consistent rows.
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


def _render(monkeypatch, *, sleeve_rows):
    """Render the real app with every read stubbed, so panel L is what varies."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    monkeypatch.setattr(st, "secrets", {})

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: True)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: True)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: [])
    monkeypatch.setattr(operational, "_get_positions", lambda: [])
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: [])
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: [])
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: list(sleeve_rows))
    monkeypatch.setattr(operational, "_get_proposed_trade", lambda: ([], "—", 100_000.0))
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: None)
    monkeypatch.setattr(operational, "fetch_last_cron_run", lambda job: None)
    operational._get_last_execution_run.clear()

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


def test_the_chart_is_drawn_when_a_session_is_stored(monkeypatch) -> None:
    """Panel L is the only chart on this stub, so one image means L drew."""
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert len(at.get("image")) == 1
    text = _text(at)
    assert "### L - Book P&L by sleeve (carry vs price)" in text
    assert "each band is one sleeve's contribution" in text
    assert "Solid band = price move, hatched band = distributions received" in text


def test_no_stored_session_means_no_chart_and_a_reason(monkeypatch) -> None:
    """The empty state has to say where the panel fills from, not draw an empty stack."""
    at = _render(monkeypatch, sleeve_rows=[])

    assert len(at.get("image")) == 0
    text = _text(at)
    assert "No sleeve P&L recorded yet" in text
    assert "each band is one sleeve's contribution" not in text


def test_the_caption_restates_the_reconciliation_from_the_stored_rows(monkeypatch) -> None:
    """The four sleeved totals here do sum to book_total, so the gap reads zero."""
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert "sum to the book total to within $0.000000" in _text(at)


def test_a_gap_between_the_sleeves_and_the_book_is_reported_not_hidden(monkeypatch) -> None:
    """If the stored rows ever disagree, the panel says so.

    The gate should make this impossible, which is exactly why the chart states the gap
    it actually computed rather than printing a reassurance.
    """
    # Each session's rows are $2 short of the book it claims, and the gap is
    # cumulative over the two sessions, so the chart reports $4.
    broken = [dict(row, book_total=12.0) for row in ROWS]

    at = _render(monkeypatch, sleeve_rows=broken)

    assert "sum to the book total to within $4.000000" in _text(at)


def test_an_all_zero_carry_is_named(monkeypatch) -> None:
    no_carry = [dict(row, carry_pnl=0.0, total=row["price_pnl"]) for row in ROWS]

    at = _render(monkeypatch, sleeve_rows=no_carry)

    assert "Carry is $0.00 in every recorded session" in _text(at)


def test_the_carry_note_is_absent_when_carry_is_recorded(monkeypatch) -> None:
    """Negative control for the note above: it must not fire when carry exists."""
    at = _render(monkeypatch, sleeve_rows=ROWS)

    assert "Carry is $0.00 in every recorded session" not in _text(at)


def test_the_bands_use_the_shared_sleeve_palette(monkeypatch) -> None:
    """Eight bands, four colours, and they are the same four the other panels use."""
    from matplotlib import colors as mcolors
    import matplotlib.pyplot as plt

    figures = []
    real_subplots = plt.subplots

    def _capture(*args, **kwargs):
        fig, ax = real_subplots(*args, **kwargs)
        figures.append(fig)
        return fig, ax

    monkeypatch.setattr(plt, "subplots", _capture)
    _render(monkeypatch, sleeve_rows=ROWS)

    chart = next(
        fig for fig in figures
        if fig.axes and fig.axes[0].get_title().startswith("Book P&L by sleeve")
    )
    used = {mcolors.to_hex(coll.get_facecolor()[0]) for coll in chart.axes[0].collections}

    assert used == set(operational.SLEEVE_COLORS.values()), (
        "every band must be one of the four sleeve colours, with no fifth colour"
    )
    assert len(chart.axes[0].collections) == 8, "price and carry band for each of four"
