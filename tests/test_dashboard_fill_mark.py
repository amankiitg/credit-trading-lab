"""The execution-run card: the fill-to-close mark is the email's number.

The page used to sum live_attribution.net_pnl, which has the day's turnover cost
subtracted, so on 2026-11-13 it printed -$8.74 while the daily email's Execution
P&L line said +$0.87. These tests pin the email's definition -- shares traded x
(close - fill), sells negative -- and the stored column that carries it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

# 2026-11-13, the session that disagreed: the per-sleeve execution split the
# evening job stored and the email printed.
SLEEVE_ROWS = [
    {
        "trade_date": "2026-11-13",
        "sleeve": "equity",
        "price_pnl": 27.2574278880237,
        "carry_pnl": 0.0,
        "total": 27.2574278880237,
        "book_total": 227.799977021324,
        "execution_pnl": -1.86929992228536,
    },
    {
        "trade_date": "2026-11-13",
        "sleeve": "rates",
        "price_pnl": 159.46076965332,
        "carry_pnl": 0.0,
        "total": 159.46076965332,
        "book_total": 227.799977021324,
        "execution_pnl": 2.29002380371094,
    },
    {
        "trade_date": "2026-11-13",
        "sleeve": "credit",
        "price_pnl": 71.8816452026367,
        "carry_pnl": 0.0,
        "total": 71.8816452026367,
        "book_total": 227.799977021324,
        "execution_pnl": 1.20000000000002,
    },
    {
        "trade_date": "2026-11-13",
        "sleeve": "commodity",
        "price_pnl": -30.7998657226562,
        "carry_pnl": 0.0,
        "total": -30.7998657226562,
        "book_total": 227.799977021324,
        "execution_pnl": -0.750036621093727,
    },
]

# The same session's live_attribution rows, as stored. net_pnl is gross minus the
# leg's turnover cost, which is the -8.74 the page used to show.
ATTR = [
    {"run_date": "2026-11-13", "ticker": "SPY", "asset_class": "equity", "gross_pnl": 4.897960670905023, "net_pnl": 4.794714671012628, "turnover_cost": 0.10324599989239601},
    {"run_date": "2026-11-13", "ticker": "EFA", "asset_class": "equity", "gross_pnl": 0.8274206250867776, "net_pnl": 0.7251166251666994, "turnover_cost": 0.10230399992007816},
    {"run_date": "2026-11-13", "ticker": "EEM", "asset_class": "equity", "gross_pnl": -8.909989000000053, "net_pnl": -9.413315634920687, "turnover_cost": 0.5033266349206349},
    {"run_date": "2026-11-13", "ticker": "TLT", "asset_class": "rates", "gross_pnl": -2.640010999999916, "net_pnl": -3.2270241428570587, "turnover_cost": 0.5870131428571429},
    {"run_date": "2026-11-13", "ticker": "IEF", "asset_class": "rates", "gross_pnl": 0.000006999999982326699, "net_pnl": -0.5359025555555732, "turnover_cost": 0.5359095555555555},
    {"run_date": "2026-11-13", "ticker": "HYG", "asset_class": "credit", "gross_pnl": -0.00001799999995455437, "net_pnl": -0.48477828571424025, "turnover_cost": 0.4847602857142857},
    {"run_date": "2026-11-13", "ticker": "LQD", "asset_class": "credit", "gross_pnl": -0.48001800000002953, "net_pnl": -1.0059677142857437, "turnover_cost": 0.5259497142857142},
    {"run_date": "2026-11-13", "ticker": "GLD", "asset_class": "commodity", "gross_pnl": 0.9299549999999499, "net_pnl": 0.4066649444443944, "turnover_cost": 0.5232900555555555},
]


def test_the_mark_is_the_emails_number_not_the_cost_inclusive_one() -> None:
    mark = operational._fill_to_close_mark(ATTR, SLEEVE_ROWS)

    assert mark is not None
    assert mark["session"] == "2026-11-13"
    assert mark["source"] == "evening signal"
    assert mark["fill_count"] == 8
    assert mark["total"] == pytest.approx(0.870687260331872, abs=1e-9)
    # The number this replaces: the same rows net of their turnover cost.
    assert sum(float(row["net_pnl"]) for row in ATTR) == pytest.approx(-8.7404, abs=1e-3)


def test_each_sleeve_keeps_the_split_the_email_prints() -> None:
    mark = operational._fill_to_close_mark(ATTR, SLEEVE_ROWS)

    assert mark["by_sleeve"] == pytest.approx(
        {
            "equity": -1.86929992228536,
            "rates": 2.29002380371094,
            "credit": 1.20000000000002,
            "commodity": -0.750036621093727,
        },
        abs=1e-9,
    )


def test_a_session_without_the_execution_column_falls_back_to_gross() -> None:
    """Older sessions have no execution_pnl. Gross attribution, never net_pnl."""
    rows = [{k: v for k, v in row.items() if k != "execution_pnl"} for row in SLEEVE_ROWS]

    mark = operational._fill_to_close_mark(ATTR, rows)

    assert mark is not None
    assert mark["source"] == "live attribution"
    assert mark["total"] == pytest.approx(-5.374692704008220, abs=1e-9)
    assert mark["total"] != pytest.approx(-8.7404, abs=1e-3)


def test_a_null_execution_column_is_treated_as_missing() -> None:
    rows = [dict(row, execution_pnl=None) for row in SLEEVE_ROWS]

    mark = operational._fill_to_close_mark(ATTR, rows)

    assert mark["source"] == "live attribution"


def test_no_rows_means_no_mark() -> None:
    assert operational._fill_to_close_mark([], []) is None


PNL_ROW = {
    "trade_date": "2026-11-13",
    "book_pnl": 91.11,
    "live_nav": 101_055.56,
    "turnover_cost": 3.3658,
    "created_at": "2026-11-13T15:32:12+00:00",
}
SIGNAL_RUN = {
    "run_date": "2026-11-13",
    "job_name": "run_signal",
    "completed_at": "2026-11-13T22:31:00+00:00",
}
EXECUTION_RUN = {
    "run_date": "2026-11-13",
    "job_name": "run_execution",
    "completed_at": "2026-11-13T15:32:12+00:00",
}


def _render(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    monkeypatch.setattr(st, "secrets", {})

    monkeypatch.setattr(operational, "get_setting", lambda key: None)
    monkeypatch.setattr(operational, "get_auto_approve", lambda: True)
    monkeypatch.setattr(operational, "set_auto_approve", lambda enabled: True)
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: None)
    monkeypatch.setattr(operational, "_get_drift_alert", lambda: None)
    monkeypatch.setattr(operational, "_get_stop_states", lambda: [])
    monkeypatch.setattr(operational, "_get_positions", lambda: [])
    monkeypatch.setattr(operational, "_get_pnl_log", lambda: [PNL_ROW])
    monkeypatch.setattr(operational, "_get_live_attribution", lambda limit: list(ATTR))
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: list(SLEEVE_ROWS))
    monkeypatch.setattr(
        operational, "_get_proposed_trade", lambda: ([], "2026-11-13", 101_113.02)
    )
    monkeypatch.setattr(operational, "_get_last_signal_run", lambda: SIGNAL_RUN)
    monkeypatch.setattr(operational, "_get_last_execution_run", lambda: EXECUTION_RUN)
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


def test_the_card_is_named_for_the_morning_run_and_has_no_second_book_pnl(monkeypatch) -> None:
    at = _render(monkeypatch)
    text = _text(at)

    assert "### Morning execution run" in text
    assert "Today's result" not in text
    assert "Book P&L (equity move since previous run)" not in text
    labels = {metric.label for metric in at.metric}
    assert "Turnover cost today" in labels
    assert "Daily P&L (close-to-close)" in labels


def test_the_card_quotes_the_emails_execution_figure(monkeypatch) -> None:
    at = _render(monkeypatch)
    text = _text(at)

    assert "$+0.87 across 8 filled leg(s) on 2026-11-13" in text
    assert "-$8.74" not in text
    assert "shares traded x (close - fill), sells negative" in text
