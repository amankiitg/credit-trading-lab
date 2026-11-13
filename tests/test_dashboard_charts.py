"""The dashboard's chart contract: fixed axes, one official NAV, one period.

These are unit tests around the helpers the page uses, so they stay fast and pin
the rules rather than the pixels: no scroll zoom, the evening-signal NAV is the
one official series (with the morning NAV only a secondary line), and every
history chart shares the period control whose default is 1 October.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from dashboard.views import operational


PNL = pd.DataFrame([
    {
        "trade_date": "2026-10-01",
        "live_nav": 100_000.0,
        "book_pnl": 100.0,
        "turnover_cost": 1.0,
        "created_at": "2026-10-01T15:30:00+00:00",
    },
    {
        "trade_date": "2026-10-02",
        "live_nav": 100_500.0,
        "book_pnl": 200.0,
        "turnover_cost": 2.0,
        "created_at": "2026-10-02T15:30:00+00:00",
    },
])

SLEEVE = pd.DataFrame([
    {
        "trade_date": session,
        "sleeve": sleeve,
        "price_pnl": 1.0,
        "carry_pnl": 0.0,
        "total": 1.0,
        "book_total": 4.0,
        "created_at": f"{session}T21:30:00+00:00",
    }
    for session in ("2026-10-01", "2026-10-02")
    for sleeve in operational.SLEEVE_ORDER
])

SNAPSHOT = {
    "value": 100_900.0,
    "source": "evening signal",
    "session": "2026-10-02",
    "timestamp": "2026-10-02T21:31:00+00:00",
}


def test_plotly_config_never_scroll_zooms() -> None:
    assert operational.PLOTLY_CONFIG["scrollZoom"] is False
    assert operational.PLOTLY_CONFIG["doubleClick"] is False


def test_plot_fixes_both_axes(monkeypatch) -> None:
    monkeypatch.setattr(operational.st, "plotly_chart", lambda *a, **k: None)
    fig = operational.go.Figure(operational.go.Scatter(x=[1, 2], y=[1, 2]))

    operational._plot(fig, height=200)

    assert fig.layout.xaxis.fixedrange is True
    assert fig.layout.yaxis.fixedrange is True
    assert fig.layout.dragmode is False


def test_the_official_nav_series_is_the_evening_signal(monkeypatch) -> None:
    frame = operational._official_nav_frame(PNL, SLEEVE, SNAPSHOT)

    assert [d.strftime("%Y-%m-%d") for d in frame["trade_date"]] == [
        "2026-10-01",
        "2026-10-02",
    ]
    # The previous evening's NAV is the next morning's NAV minus the move that
    # morning recorded; the last point is the stored setting itself.
    assert frame["nav"].tolist() == [100_500.0 - 200.0, 100_900.0]
    assert set(frame["source"]) == {"evening signal"}
    assert frame["timestamp"].iloc[0] == "2026-10-01T21:30:00+00:00"


def test_nav_snapshot_calls_the_evening_signal_official(monkeypatch) -> None:
    monkeypatch.setattr(operational, "_get_last_signal_run", lambda: {
        "run_date": "2026-10-02",
        "completed_at": "2026-10-02T21:31:00+00:00",
    })
    monkeypatch.setattr(operational, "_get_last_execution_run", lambda: {
        "run_date": "2026-10-02",
        "completed_at": "2026-10-02T15:30:00+00:00",
    })

    snapshot = operational._nav_snapshot(100_900.0, PNL)

    assert snapshot["source"] == "evening signal"
    assert snapshot["session"] == "2026-10-02"
    assert snapshot["value"] == 100_900.0
    assert snapshot["morning_nav"] == 100_500.0


def test_a_stale_signal_run_is_labelled_as_the_morning_nav(monkeypatch) -> None:
    monkeypatch.setattr(operational, "_get_last_signal_run", lambda: {
        "run_date": "2026-10-01",
        "completed_at": "2026-10-01T21:31:00+00:00",
    })
    monkeypatch.setattr(operational, "_get_last_execution_run", lambda: {
        "run_date": "2026-10-02",
        "completed_at": "2026-10-02T15:30:00+00:00",
    })

    snapshot = operational._nav_snapshot(100_500.0, PNL)

    assert snapshot["source"] == "morning execution"
    assert snapshot["session"] == "2026-10-02"


def test_the_history_period_defaults_to_one_october(monkeypatch) -> None:
    captured = {}

    def _date_input(label, value=None, **kwargs):
        captured["value"] = value
        return value

    monkeypatch.setattr(operational.st, "date_input", _date_input)

    chosen = operational._history_period_control(date(2026, 9, 20), date(2026, 11, 12))

    assert chosen == date(2026, 10, 1)
    assert captured["value"] == date(2026, 10, 1)


def test_the_history_period_is_clamped_to_the_data(monkeypatch) -> None:
    monkeypatch.setattr(operational.st, "date_input", lambda label, value=None, **k: value)

    assert operational._history_period_control(
        date(2026, 10, 20), date(2026, 11, 12)
    ) == date(2026, 10, 20)
