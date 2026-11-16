"""The signed-out approval panel: a prompt only when a trade is actually staged.

The page renders publicly; approval needs Google sign-in. The prompt used to appear
whenever a stored proposal had any non-skip delta row, including on a settled
session and on a day the standing auto-approve instruction had already decided, so
it asked the reader to sign in for trades that were not waiting on them. The
advisory stop note must stay readable either way, as information.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

TODAY = operational._today_utc()
YESTERDAY = (date.fromisoformat(TODAY) - timedelta(days=1)).isoformat()

PROPOSAL = [
    {
        "ticker": "SPY",
        "current ($)": 0.0,
        "current wt": 0.0,
        "target wt": 0.35,
        "delta ($)": 35_000.0,
        "action": "buy",
    }
]
SKIPS = [
    {
        "ticker": "SPY",
        "current ($)": 26_663.0,
        "current wt": 0.2637,
        "target wt": 0.2659,
        "delta ($)": 225.0,
        "action": "skip — within band",
    }
]
STOP_STATES = [
    {"ticker": "EFA", "state": "REDUCED", "z": -1.35, "multiplier": 0.5, "advisory": True}
]


def _render(
    monkeypatch,
    *,
    rows=PROPOSAL,
    as_of=YESTERDAY,
    last_run=YESTERDAY,
    auto_approve=False,
    decision="proposed",
):
    """Render the real app signed out, with OIDC configured and every read stubbed."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    # OIDC configured, and nobody is logged in: this is the live public page.
    monkeypatch.setattr(st, "secrets", {"auth": {"google": {"client_id": "x"}}})
    monkeypatch.setattr(
        st, "user", SimpleNamespace(is_logged_in=False, email=None)
    )

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: auto_approve)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: True)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: list(STOP_STATES))
    monkeypatch.setattr(operational, "_get_positions", lambda: [])
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: [])
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: [])
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: [])
    monkeypatch.setattr(
        operational, "_get_proposed_trade", lambda: (list(rows), as_of, 100_000.0)
    )
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: decision)
    monkeypatch.setattr(
        operational,
        "write_decision",
        lambda d, v: pytest.fail("rendering the page must never write a decision"),
    )
    monkeypatch.setattr(
        operational,
        "fetch_last_cron_run",
        lambda job: {"run_date": last_run} if last_run else None,
    )
    operational._get_last_execution_run.clear()
    operational._get_last_signal_run.clear()
    monkeypatch.setattr(operational, "_risk_snapshot", lambda nav, pos: None)
    monkeypatch.setattr(operational, "render_signal_history", lambda: None)

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


def test_a_staged_trade_still_asks_for_sign_in(monkeypatch) -> None:
    at = _render(monkeypatch, last_run=YESTERDAY)

    text = _text(at)
    assert "Sign in to approve or reject today's trades." in text
    assert "No trades pending" not in text
    assert {b.label for b in at.button} == {"Sign in with Google"}


def test_a_settled_proposal_names_the_orders_and_needs_no_approval(monkeypatch) -> None:
    """The execution run already acted: the orders are history, not pending.

    The table still lists them, so the panel names them and says why no decision
    is waiting, instead of claiming there are no trades.
    """
    at = _render(monkeypatch, last_run=TODAY)

    text = _text(at)
    assert "1 order for the next morning run: SPY $+35,000 (buy)" in text
    assert (
        "Approval: not needed — the morning run for this signal has already happened."
        in text
    )
    assert "No trades pending" not in text
    assert "Sign in to approve or reject" not in text
    assert {b.label for b in at.button} == set()
    # The advisory stop note is information, not a call to action.
    assert "ADVISORY (not traded)" in text
    assert "EFA" in text


def test_a_proposal_with_only_band_noise_shows_no_trades_pending(monkeypatch) -> None:
    at = _render(monkeypatch, rows=SKIPS, last_run=YESTERDAY)

    text = _text(at)
    assert (
        f"No trades pending for {YESTERDAY} — every delta is inside the ±$250 band."
        in text
    )
    assert "Sign in to approve or reject" not in text
    assert {b.label for b in at.button} == set()


def test_a_lagged_signal_still_names_its_orders(monkeypatch) -> None:
    lagged = (date.fromisoformat(YESTERDAY) - timedelta(days=2)).isoformat()
    at = _render(monkeypatch, as_of=lagged, last_run=YESTERDAY)

    text = _text(at)
    assert "1 order for the next morning run: SPY $+35,000 (buy)" in text
    assert "Approval:" in text
    assert "Sign in to approve or reject" not in text


def test_auto_approve_means_nothing_is_waiting_for_a_decision(monkeypatch) -> None:
    """The live setting: the cron executes unless rejected, so no approval is pending.

    Asking a public visitor to sign in would imply a decision that the standing
    instruction has already made for them.
    """
    at = _render(monkeypatch, auto_approve=True)

    text = _text(at)
    assert "1 order for the next morning run: SPY $+35,000 (buy)" in text
    assert (
        "Approval: not needed — auto-approve is ON, so the run executes unless "
        "you reject."
    ) in text
    assert "Sign in to approve or reject" not in text
    assert {b.label for b in at.button} == set()
    assert "ADVISORY (not traded)" in text


def test_a_rejected_day_has_nothing_pending(monkeypatch) -> None:
    at = _render(monkeypatch, decision="reject")

    text = _text(at)
    assert "1 order for the next morning run: SPY $+35,000 (buy)" in text
    assert (
        "Approval: not needed — today is rejected, so no trades will execute."
        in text
    )
    assert "Sign in to approve or reject" not in text
