"""The dashboard's chart contract: fixed axes, one official NAV, one period.

These are unit tests around the helpers the page uses, so they stay fast and pin
the rules rather than the pixels: no scroll zoom, the evening-signal NAV is the
one official series (with the morning NAV only a secondary line), and every
history chart shares the period control whose default is 1 October.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

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
    # 10-01 is the published close. 10-02 is the stored setting of the run that
    # measured it: its own close is not published until the next calendar day.
    assert frame["nav"].tolist() == [100_400.0, 100_900.0]
    assert set(frame["source"]) == {"portfolio history", "evening signal"}
    assert frame["timestamp"].iloc[0] == "2026-10-01T21:30:00+00:00"


# --------------------------------------------------- close NAV, read from the broker
#
# The three shapes below are what the portfolio-history endpoint returns: one
# end-of-day point per session, stamped 01:00 UTC on the FOLLOWING calendar day.
# The values are the real closes around 2026-11-09; the morning NAV is set to a
# nonsense number so any arithmetic on it would be obvious.

CLOSES = {
    "2026-11-05": 98_181.21,
    "2026-11-06": 97_813.04,
    "2026-11-09": 98_003.56,
    "2026-11-10": 99_348.84,
    "2026-11-11": 99_359.37,
}
RECENT_SESSIONS = ["2026-11-05", "2026-11-06", "2026-11-09",
                   "2026-11-10", "2026-11-11", "2026-11-12"]
PNL_RECENT = pd.DataFrame([
    {
        "trade_date": session,
        "live_nav": 1_000_000.0,
        "book_pnl": 123_456.0,
        "turnover_cost": 0.0,
        "created_at": f"{session}T15:32:00+00:00",
    }
    for session in RECENT_SESSIONS
])


class _PortfolioHistory:
    def __init__(self, timestamps, equity):
        self.timestamp = timestamps
        self.equity = equity


class _HistoryClient:
    """A stand-in for TradingClient that answers portfolio-history GETs."""

    def __init__(self, closes: dict[str, float]):
        self.calls = 0
        self._rows = [
            (
                int(
                    datetime.combine(
                        date.fromisoformat(session) + timedelta(days=1),
                        time(1, 0),
                        tzinfo=timezone.utc,
                    ).timestamp()
                ),
                value,
            )
            for session, value in sorted(closes.items())
        ]

    def get_portfolio_history(self, request):  # noqa: ANN001 - the SDK request
        self.calls += 1
        return _PortfolioHistory(
            [row[0] for row in self._rows], [row[1] for row in self._rows]
        )


def _stamped_equity(closes: dict[str, float]) -> dict[str, float]:
    """The endpoint's labelling: a close is stamped with the next calendar day."""
    return {
        (date.fromisoformat(session) + timedelta(days=1)).isoformat(): value
        for session, value in closes.items()
    }


def test_the_close_nav_is_the_reconciliations_close_equity(monkeypatch) -> None:
    """For the last three sessions the dashboard plots exactly the close the
    reconciliation measures, not an arithmetic reconstruction of it."""
    from dashboard import equity_history
    from execution import alpaca_paper as ap
    from risk.execution_pnl import close_equity

    closes = dict(CLOSES)
    client = _HistoryClient(closes)
    monkeypatch.setattr(ap, "connect", lambda dry_run=False: client)

    sessions = sorted(closes)
    equity = _stamped_equity(closes)

    dashboard_values = equity_history.close_equity_by_session(sessions)

    assert dashboard_values == {s: close_equity(equity, s) for s in sessions}
    for session in sessions[-3:]:
        assert dashboard_values[session] == closes[session]

    frame, reason = operational._official_nav_frame(
        PNL_RECENT,
        pd.DataFrame(),
        {
            "value": 100_964.45,
            "source": "evening signal",
            "session": "2026-11-12",
            "timestamp": "2026-11-12T22:31:00+00:00",
        },
        close_navs=dashboard_values,
    )

    assert reason is None
    plotted = {
        stamp.date().isoformat(): value
        for stamp, value in zip(frame["trade_date"], frame["nav"])
    }
    for session in sessions[-3:]:
        assert plotted[session] == close_equity(equity, session)
    # The session whose close is not published yet keeps the stored setting.
    assert plotted["2026-11-12"] == 100_964.45


def test_an_unreadable_history_shows_only_the_stored_nav(monkeypatch) -> None:
    from dashboard import equity_history
    from execution import alpaca_paper as ap

    equity_history.cached_close_equity_by_session.clear()

    def _no_client(dry_run=False):  # noqa: ANN001
        raise EnvironmentError("ALPACA_PAPER_API_KEY must be set")

    monkeypatch.setattr(ap, "connect", _no_client)

    frame, reason = operational._official_nav_frame(PNL, pd.DataFrame(), SNAPSHOT)

    assert reason is not None and "ALPACA_PAPER_API_KEY" in reason
    assert frame["nav"].tolist() == [100_900.0]
    assert frame["source"].tolist() == ["evening signal"]


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
