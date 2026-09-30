"""The three decision-window states as the page actually renders them.

`test_decision_window.py` pins the state machine and the cron_runs reader. This pins
the wiring end to end: that the Approve and Reject buttons exist only in the open
state, that the other two states say what they are instead, and that the gate is read
from cron_runs for the execution job.

Every read the page makes is stubbed, so no test here touches Supabase or the network
and the window is the only thing that varies. The data readers are the same ones the
panels use, so they are replaced rather than mocked in detail.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

TODAY = operational._today_utc()
YESTERDAY = (date.fromisoformat(TODAY) - timedelta(days=1)).isoformat()
THREE_DAYS_AGO = (date.fromisoformat(TODAY) - timedelta(days=3)).isoformat()

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

APPROVE_LABEL = "Approve all trades"
REJECT_LABEL = "Reject / skip today"


def _render(
    monkeypatch,
    *,
    rows=PROPOSAL,
    as_of=YESTERDAY,
    decision="proposed",
    last_run=YESTERDAY,
    attr_rows=(),
    pnl_rows=(),
    auto_approve=True,
    set_ok=True,
):
    """Render the real app with every read stubbed. Returns (app, gate calls)."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")

    # A developer box has .streamlit/secrets.toml with an [auth] section, and app.py
    # reads that as "OIDC is configured", so the page renders its sign-in prompt and
    # never reaches the decision block. Emptying the mapping is what a deployment
    # without OIDC looks like, and it is the branch these tests are about. The file
    # itself is never touched.
    monkeypatch.setattr(st, "secrets", {})

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: auto_approve)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: set_ok)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: [])
    monkeypatch.setattr(operational, "_get_positions", lambda: [])
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: list(pnl_rows))
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: list(attr_rows))
    monkeypatch.setattr(
        operational, "_get_proposed_trade", lambda: (list(rows), as_of, 100_000.0)
    )
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: decision)
    monkeypatch.setattr(
        operational,
        "write_decision",
        lambda d, v: pytest.fail("rendering the page must never write a decision"),
    )

    gate_calls: list[str] = []

    def _last_run(job_name):
        gate_calls.append(job_name)
        return {"run_date": last_run} if last_run else None

    monkeypatch.setattr(operational, "fetch_last_cron_run", _last_run)
    # The cached reader has no arguments, so one entry would otherwise leak from the
    # first test into every later one in this process.
    operational._get_last_execution_run.clear()

    at = AppTest.from_file(str(APP), default_timeout=600)
    at.run()

    assert not at.exception, [str(e.value) for e in at.exception]
    return at, gate_calls


def _labels(at) -> set[str]:
    return {b.label for b in at.button}


def _text(at) -> str:
    chunks = [str(m.value) for m in at.markdown]
    for kind in ("info", "success", "error", "warning"):
        chunks += [str(e.value) for e in at.get(kind)]
    chunks += [str(c.value) for c in at.caption]
    return " ".join(chunks)


# ------------------------------------------------------------------ state 2: open


def test_a_fresh_proposal_shows_the_live_approve_and_reject_buttons(monkeypatch) -> None:
    at, _ = _render(monkeypatch, last_run=YESTERDAY)

    assert _labels(at) == {APPROVE_LABEL, REJECT_LABEL}
    assert "has not been acted on yet" in _text(at)
    assert "Locked for today" not in _text(at)


def test_an_already_approved_decision_offers_the_switch_not_both_buttons(monkeypatch) -> None:
    """Still open, so the operator can change their mind before the run."""
    at, _ = _render(monkeypatch, decision="approve", last_run=YESTERDAY)

    assert _labels(at) == {"Change to: Reject / skip today"}
    assert "will execute at next cron run" in _text(at)


# ------------------------------------------------------------------ state 3: locked


def test_a_recorded_run_hides_the_buttons_and_reports_the_outcome(monkeypatch) -> None:
    """The finding itself: after the run, an approval must not be offered as if live."""
    at, _ = _render(monkeypatch, last_run=TODAY)

    assert _labels(at) == set(), "no decision control once the run has happened"
    assert "Locked for today" in _text(at)
    assert "Ran with no fills" in _text(at)


def test_a_locked_rejection_says_what_happened(monkeypatch) -> None:
    at, _ = _render(monkeypatch, decision="reject", last_run=TODAY)

    assert _labels(at) == set()
    assert "Skipped, you rejected" in _text(at)
    assert "Traded:" not in _text(at)


def test_a_locked_run_reports_the_legs_it_filled(monkeypatch) -> None:
    at, _ = _render(
        monkeypatch,
        last_run=TODAY,
        attr_rows=[
            {"run_date": TODAY, "ticker": "SPY", "asset_class": "equity", "net_pnl": 5.0},
            {"run_date": TODAY, "ticker": "LQD", "asset_class": "credit", "net_pnl": -1.5},
        ],
    )

    text = _text(at)
    assert _labels(at) == set()
    assert "Traded: 2 leg(s) filled today" in text
    assert "LQD, SPY" in text
    assert "$+3.50" in text


def test_a_locked_run_falls_back_to_the_notional_when_the_legs_are_missing(monkeypatch) -> None:
    """The attribution write can fail while the run still traded.

    pnl_log.gross_pnl is the day's traded notional, so the outcome can still say what
    traded rather than claiming nothing did.
    """
    at, _ = _render(
        monkeypatch,
        last_run=TODAY,
        pnl_rows=[
            {
                "trade_date": TODAY,
                "gross_pnl": 12_345.67,
                "turnover_cost": 2.5,
                "book_pnl": 42.0,
                "live_nav": 100_042.0,
            }
        ],
    )

    text = _text(at)
    assert _labels(at) == set()
    assert "$12,345.67 of notional today" in text
    assert "leg-level detail was not recorded" in text.lower()


# ------------------------------------------------------------------ state 1: no fresh signal


def test_a_lagged_signal_waits_for_the_next_one(monkeypatch) -> None:
    at, _ = _render(monkeypatch, as_of=THREE_DAYS_AGO, last_run=YESTERDAY)

    assert _labels(at) == set()
    assert "Waiting for the next signal" in _text(at)
    assert "already acted on" in _text(at)


def test_no_proposal_at_all_waits_for_the_next_signal(monkeypatch) -> None:
    at, _ = _render(monkeypatch, rows=[], as_of="—", last_run=None)

    assert _labels(at) == set()
    assert "Waiting for the next signal" in _text(at)
    assert "no proposal is on file yet" in _text(at)


# ------------------------------------------------------------------ the gate


@pytest.mark.parametrize(
    "last_run,expected_state_text",
    [
        (TODAY, "Locked for today"),
        (YESTERDAY, "has not been acted on yet"),
    ],
)
def test_the_panel_asks_cron_runs_for_the_execution_job(
    monkeypatch, last_run, expected_state_text
) -> None:
    """The gate is the recorded run, so the panel must ask for exactly that row.

    Asserted through the real cached reader rather than a stub of it, so this covers
    the reader, the job name it filters on, and the state it produces.
    """
    at, gate_calls = _render(monkeypatch, last_run=last_run)

    assert gate_calls == ["run_execution"]
    assert expected_state_text in _text(at)


def test_the_scheduled_time_is_shown_as_context(monkeypatch) -> None:
    """The cutoff is on the panel, and named as context rather than as the gate."""
    at, _ = _render(monkeypatch, last_run=YESTERDAY)

    text = _text(at)
    assert "14:30 UTC" in text
    assert "recorded run, not the clock" in text


# ------------------------------------------------------------------ the auto-approve write


def test_a_successful_auto_approve_write_reports_the_new_value(monkeypatch) -> None:
    """Negative control for the failure case below: a write that works says so."""
    at, _ = _render(monkeypatch, auto_approve=True, set_ok=True)

    at.toggle[0].set_value(False).run()
    assert not at.exception, [str(e.value) for e in at.exception]

    text = _text(at)
    assert "Auto-approve OFF" in text
    assert "execute only if `decision = approve`" in text
    assert "Could not save" not in text


def test_a_failed_auto_approve_write_reports_an_error_not_success(monkeypatch) -> None:
    """The toggle used to report success whatever the write did.

    set_auto_approve returns a bool and the panel discarded it, so a failed settings
    write read as "Auto-approve ON" while the cron kept running the old behaviour. The
    stored value is what the cron reads, so a failed write has to say so, and the copy
    below it has to keep describing the stored value rather than the intended one.
    """
    at, _ = _render(monkeypatch, auto_approve=True, set_ok=False)

    at.toggle[0].set_value(False).run()
    assert not at.exception, [str(e.value) for e in at.exception]

    text = _text(at)
    assert "Could not save the auto-approve setting" in text
    assert "Auto-approve OFF" not in text, "a failed write must not report the new state"
    assert "execute unless `decision = reject`" in text, (
        "the cron-logic copy must describe the stored value, which is still ON"
    )
