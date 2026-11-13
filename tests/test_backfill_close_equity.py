"""The one-off close_equity backfill reads only, and writes only close_equity.

The script is not scheduled and is not run by these tests: they drive its two pieces
-- the backwards walk over the portfolio history and the write -- with a fake read, so
the convention and the "writes nothing in dry-run" promise are pinned without a
broker or a database.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import execution.alpaca_paper as ap
import scripts.backfill_close_equity as backfill
from execution.calendar_utils import nyse_sessions


def _history(skip_session: str | None = None) -> tuple[list[str], dict[str, float]]:
    """Sessions and their stamps, labelled with the following calendar day."""
    sessions = nyse_sessions("2026-10-01", "2026-11-12")
    equity: dict[str, float] = {}
    for index, session in enumerate(sessions):
        if session == skip_session:
            continue
        label = (date.fromisoformat(session) + timedelta(days=1)).isoformat()
        equity[label] = 100_000.0 + index
    return sessions, equity


def _install_history(monkeypatch, equity: dict[str, float], calls: list[str]) -> None:
    """fetch_equity_history as the broker serves it: a window around the anchor."""

    def _fetch(client, session_date):
        calls.append(session_date)
        anchor = date.fromisoformat(session_date)
        return {
            label: value
            for label, value in equity.items()
            if anchor - timedelta(days=10) <= date.fromisoformat(label) <= anchor + timedelta(days=2)
        }

    monkeypatch.setattr(ap, "fetch_equity_history", _fetch)


def test_the_backfill_covers_every_session_the_history_published(monkeypatch) -> None:
    sessions, equity = _history()
    calls: list[str] = []
    _install_history(monkeypatch, equity, calls)

    closes = backfill.collect_closes(object(), start="2026-10-01", end="2026-11-13")

    assert sorted(closes) == sessions, "every session, in the same set the calendar names"
    for session in sessions:
        assert closes[session] == equity[
            (date.fromisoformat(session) + timedelta(days=1)).isoformat()
        ]
    assert closes["2026-10-01"] == equity["2026-10-02"], "the first session the chart draws"
    assert "2026-10-31" not in closes, "a Saturday is not a session"
    assert len(calls) > 1, "one window cannot reach from today back to October"


def test_a_session_with_no_stamp_of_its_own_is_skipped_not_filled(monkeypatch) -> None:
    sessions, equity = _history(skip_session="2026-10-14")
    calls: list[str] = []
    _install_history(monkeypatch, equity, calls)

    closes = backfill.collect_closes(object(), start="2026-10-01", end="2026-11-13")

    assert "2026-10-14" not in closes
    assert "2026-10-15" in closes, "the following session is unaffected"
    assert len(closes) == len(sessions) - 1


def test_a_dry_run_writes_nothing(monkeypatch) -> None:
    _sessions, equity = _history()
    _install_history(monkeypatch, equity, [])
    monkeypatch.setattr(ap, "connect", lambda dry_run=False: object())

    import dashboard.supabase_client as sb

    def _must_not_write(*args, **kwargs):
        raise AssertionError("dry run must not touch close_equity")

    monkeypatch.setattr(sb, "write_close_equity", _must_not_write)

    assert backfill.main(["--dry-run", "--end", "2026-11-13"]) == 0


def test_the_write_path_sends_only_the_close_rows(monkeypatch) -> None:
    _sessions, equity = _history()
    _install_history(monkeypatch, equity, [])
    monkeypatch.setattr(ap, "connect", lambda dry_run=False: object())

    import dashboard.supabase_client as sb

    written: list[dict] = []
    monkeypatch.setattr(
        sb,
        "write_close_equity",
        lambda closes, *, source: written.append(
            {"closes": dict(closes), "source": source}
        )
        or True,
    )

    assert backfill.main(["--end", "2026-11-13"]) == 0
    assert len(written) == 1
    assert written[0]["source"] == "alpaca_portfolio_history"
    assert "2026-10-01" in written[0]["closes"]
