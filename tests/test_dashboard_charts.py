"""The dashboard's chart contract: fixed axes, one official NAV, one period.

These are unit tests around the helpers the page uses, so they stay fast and pin
the rules rather than the pixels: no scroll zoom, the stored close is the one
official series (with the morning NAV only a secondary line), and every history
chart shares the period control whose default is 1 October.
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


def test_the_official_nav_series_is_the_evening_close() -> None:
    frame, reason = operational._official_nav_frame(
        PNL,
        SLEEVE,
        SNAPSHOT,
        close_navs={"2026-10-01": 100_400.0, "2026-10-02": 100_700.0},
    )

    assert reason is None
    assert [d.strftime("%Y-%m-%d") for d in frame["trade_date"]] == [
        "2026-10-01",
        "2026-10-02",
    ]
    # 10-01 is the stored close. 10-02 is the stored setting of the run that
    # measured it: its own close is stored only after the session.
    assert frame["nav"].tolist() == [100_400.0, 100_900.0]
    assert set(frame["source"]) == {"close_equity", "evening signal"}
    assert frame["timestamp"].iloc[0] == "2026-10-01T21:30:00+00:00"


def test_the_close_series_is_a_table_read_and_needs_no_broker() -> None:
    """The dashboard holds no Alpaca credential: the close comes from Supabase."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "dashboard" / "equity_history.py")
    text = source.read_text().lower()

    # The word may appear in the explanation; the import and the client must not.
    assert "alpaca_paper" not in text
    assert "execution.alpaca" not in text
    assert "connect(" not in text


def test_the_close_series_maps_only_the_sessions_asked_for(monkeypatch) -> None:
    from dashboard import equity_history
    import dashboard.supabase_client as sb

    equity_history.cached_close_equity_by_session.clear()
    monkeypatch.setattr(
        sb,
        "fetch_close_equity",
        lambda **kwargs: {
            "2026-10-01": 100_400.0,
            "2026-10-02": 100_700.0,
            "2026-10-05": 100_950.0,
        },
        raising=True,
    )

    assert equity_history.close_equity_by_session(
        ["2026-09-30", "2026-10-01", "2026-10-02"]
    ) == {"2026-10-01": 100_400.0, "2026-10-02": 100_700.0}


def test_an_unreadable_close_table_shows_only_the_stored_nav(monkeypatch) -> None:
    from dashboard import equity_history
    import dashboard.supabase_client as sb

    equity_history.cached_close_equity_by_session.clear()

    def _broken(**kwargs):
        raise RuntimeError("relation public.close_equity does not exist")

    monkeypatch.setattr(sb, "fetch_close_equity", _broken)

    frame, reason = operational._official_nav_frame(PNL, pd.DataFrame(), SNAPSHOT)

    assert reason is not None and "close_equity" in reason
    assert frame["nav"].tolist() == [100_900.0]
    assert frame["source"].tolist() == ["evening signal"]


def test_an_empty_table_is_not_an_error(monkeypatch) -> None:
    """Before the first evening run the table is empty, not broken."""
    from dashboard import equity_history
    import dashboard.supabase_client as sb

    equity_history.cached_close_equity_by_session.clear()
    monkeypatch.setattr(sb, "fetch_close_equity", lambda **kwargs: {})

    frame, reason = operational._official_nav_frame(PNL, pd.DataFrame(), SNAPSHOT)

    assert reason is None
    assert frame["nav"].tolist() == [100_900.0]


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
