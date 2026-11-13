"""The header's 1-day P&L and Active risk flags cards.

The NAV and 1-day P&L cards describe one run: the evening signal that refreshed
the NAV after the close. These tests pin that the P&L is that session's book
total rather than the morning execution row, that both cards carry the same run
timestamp, and that every flag is listed on the card as a sentence rather than a
category name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

# The live shape on 2026-11-12: the morning execution wrote pnl_log at 15:32 UTC,
# then the evening signal wrote both the sleeve rows and settings.live_nav at 22:31.
PNL_ROW = {
    "trade_date": "2026-11-12",
    "book_pnl": 1308.15,
    "live_nav": 100_683.63,
    "created_at": "2026-11-12T15:32:11+00:00",
}
SLEEVE_ROWS = [
    {
        "trade_date": "2026-11-11",
        "sleeve": sleeve,
        "price_pnl": 1.0,
        "carry_pnl": 0.0,
        "total": 1.0,
        "book_total": 4.0,
        "created_at": "2026-11-11T21:31:37+00:00",
    }
    for sleeve in ("equity", "rates", "credit", "commodity")
] + [
    {
        "trade_date": "2026-11-12",
        "sleeve": sleeve,
        "price_pnl": 401.426,
        "carry_pnl": 0.0,
        "total": 401.426,
        "book_total": 1605.70445141711,
        "created_at": "2026-11-12T22:31:54+00:00",
    }
    for sleeve in ("equity", "rates", "credit", "commodity")
]
SIGNAL_RUN = {
    "run_date": "2026-11-12",
    "job_name": "run_signal",
    "completed_at": "2026-11-12T22:31:53+00:00",
}
EXECUTION_RUN = {
    "run_date": "2026-11-12",
    "job_name": "run_execution",
    "completed_at": "2026-11-12T15:32:11+00:00",
}


def _render(
    monkeypatch,
    *,
    sleeve_rows=SLEEVE_ROWS,
    pnl_rows=(PNL_ROW,),
    positions=(),
    proposed_rows=(),
):
    """Render the real app with every read stubbed."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    monkeypatch.setattr(st, "secrets", {})

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: True)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: True)
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: None)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: [])
    monkeypatch.setattr(operational, "_get_positions", lambda: list(positions))
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: list(pnl_rows))
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: [])
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: list(sleeve_rows))
    monkeypatch.setattr(
        operational,
        "_get_proposed_trade",
        lambda: (list(proposed_rows), "2026-11-12", 100_964.45),
    )
    monkeypatch.setattr(operational, "_get_last_signal_run", lambda: SIGNAL_RUN)
    monkeypatch.setattr(operational, "_get_last_execution_run", lambda: EXECUTION_RUN)
    monkeypatch.setattr(operational, "_risk_snapshot", lambda nav, pos: None)

    at = AppTest.from_file(str(APP), default_timeout=600)
    at.run()

    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _metrics(at) -> dict:
    return {metric.label: metric for metric in at.metric}


def test_the_pnl_card_reads_the_evening_signals_session(monkeypatch) -> None:
    metrics = _metrics(_render(monkeypatch))

    assert metrics["1-day P&L"].value == "$+1,605.70"
    assert metrics["1-day P&L"].delta == "+1.59% · 2026-11-12 22:31 UTC"


def test_the_pnl_card_is_not_the_morning_execution_row(monkeypatch) -> None:
    metrics = _metrics(_render(monkeypatch))

    assert "1,308.15" not in metrics["1-day P&L"].value


def test_both_cards_carry_the_same_run_timestamp(monkeypatch) -> None:
    metrics = _metrics(_render(monkeypatch))

    assert metrics["NAV"].delta == "evening signal 2026-11-12 22:31 UTC"
    # The card is narrow enough to ellipsise a delta, so the P&L label carries
    # the timestamp itself; the two cards still name the same run minute.
    assert metrics["1-day P&L"].delta.endswith("2026-11-12 22:31 UTC")


def test_the_caption_names_the_pnl_run_and_session(monkeypatch) -> None:
    at = _render(monkeypatch)
    captions = " ".join(str(c.value) for c in at.caption)

    assert (
        "1-day P&L is the evening signal's book P&L for the 2026-11-12 session"
        in captions
    )


def test_pnl_falls_back_to_the_morning_row_when_no_session_is_stored(monkeypatch) -> None:
    metrics = _metrics(_render(monkeypatch, sleeve_rows=[]))

    assert metrics["1-day P&L"].value == "$+1,308.15"
    assert metrics["1-day P&L"].delta == "+1.30% · 2026-11-12 15:32 UTC"


def test_the_flags_card_lists_each_flag_as_a_sentence(monkeypatch) -> None:
    positions = [{"trade_date": "2026-11-12", "ticker": "EEM", "signed_notional": 90_000.0}]
    proposed = [{
        "ticker": "EEM",
        "current ($)": 90_000.0,
        "current wt": 0.8914,
        "target wt": 0.0,
        "delta ($)": -90_000.0,
        "action": "sell / short",
    }]
    at = _render(monkeypatch, positions=positions, proposed_rows=proposed)

    listed = " ".join(str(m.value) for m in at.markdown)
    assert "net is +89.1% of NAV against a target of +0.0%" in listed
    assert "+89.1pp outside the +/-5pp band" in listed
    assert "gross is 0.89x, below the 1.80x floor" in listed
    assert _metrics(at)["Active risk flags"].value == "2"


def test_every_flag_is_kept_in_full_by_the_helper() -> None:
    flags = operational._active_flags(
        ["EEM carries 43% of the book's risk (over 40%)"],
        "net is +92.3% of NAV against a target of +100.0%, -7.7pp outside the +/-5pp band",
        None,
        {
            "detected_at": "2026-11-12 15:31",
            "detail": {"SPY": {"cached": 1.0, "live": 2.0}},
        },
        [{"ticker": "GLD", "state": "REDUCED"}],
    )

    assert flags == [
        "EEM carries 43% of the book's risk (over 40%)",
        "net is +92.3% of NAV against a target of +100.0%, -7.7pp outside the +/-5pp band",
        "position drift: the cached snapshot disagreed with live Alpaca on SPY"
        " (detected 2026-11-12 15:31)",
        "1 stop state(s) away from NORMAL: GLD REDUCED",
    ]


def test_no_flags_leaves_the_card_clear() -> None:
    assert operational._active_flags([], None, None, None, []) == []
