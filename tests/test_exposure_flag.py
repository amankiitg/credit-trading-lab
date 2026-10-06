"""Net exposure is judged against the signal's target, not against zero.

The exposure panel used to describe this book as market-neutral, with net meant to sit
near zero, and flagged any book more than 5% of NAV away from zero. That premise is
wrong: this is a per-asset trend book, each name takes its own long or short call, and
net is whatever those eight calls add up to. A book at -50.9% net was reported as a
breach on 2026-10-05 when the signal had asked for -52.9%.

`net_flag` is pure so the band can be pinned directly, and the panel tests render the
real page to check the wording and the count line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard.views import operational
from dashboard.views.operational import NET_BAND_PP, gross_flag, net_flag

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"

# The live book on 2026-10-05, as read from positions and signal_target_weights:
# 3 long, 5 short, net -50.9% of NAV against a target of -52.9%, gross 1.975x.
NAV = 100_000.0
SNAPSHOT_DATE = "2026-10-05"
POSITIONS = [
    {"trade_date": SNAPSHOT_DATE, "ticker": "SPY", "signed_notional": 27_100.0, "side": "long"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "EFA", "signed_notional": 25_800.0, "side": "long"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "EEM", "signed_notional": 20_400.0, "side": "long"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "HYG", "signed_notional": -25_200.0, "side": "short"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "LQD", "signed_notional": -25_400.0, "side": "short"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "IEF", "signed_notional": -26_500.0, "side": "short"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "TLT", "signed_notional": -26_700.0, "side": "short"},
    {"trade_date": SNAPSHOT_DATE, "ticker": "GLD", "signed_notional": -20_400.0, "side": "short"},
]
TARGET_WEIGHTS = {
    "SPY": 0.271, "EFA": 0.260, "EEM": 0.205,
    "HYG": -0.255, "LQD": -0.262, "IEF": -0.268, "TLT": -0.271, "GLD": -0.209,
}
LIVE_NET_PCT = -50.9
LIVE_TARGET_NET_PCT = -52.9


def _proposal(weights: dict[str, float]) -> list[dict]:
    return [
        {"ticker": ticker, "target wt": weight, "current wt": 0.0,
         "current ($)": 0.0, "delta ($)": 0.0, "action": "buy" if weight > 0 else "sell / short"}
        for ticker, weight in weights.items()
    ]


# ------------------------------------------------------------------ the net band


def test_todays_book_is_inside_the_band_around_its_target() -> None:
    """The case that exposed the wrong premise: -50.9% net, -52.9% asked for.

    Under the old rule this was a breach, because |net| was compared with zero and 50.9
    is greater than 5. It is 2.0pp from what the signal actually wanted.
    """
    assert net_flag(LIVE_NET_PCT, LIVE_TARGET_NET_PCT) is None


def test_the_band_is_symmetric_around_the_target() -> None:
    """Both directions, and the target itself is not special-cased to zero."""
    assert net_flag(-48.0, -52.9) is None, "4.9pp on the inside"
    assert net_flag(-57.8, -52.9) is None, "4.9pp on the other side"
    assert net_flag(30.0, 25.5) is None, "a long book measured against a long target"


def test_the_band_edge_is_inclusive() -> None:
    """Exactly 5pp away is inside; the message below says "outside", so it must not fire."""
    assert net_flag(5.0, 0.0) is None
    assert net_flag(-5.0, 0.0) is None


def test_a_book_beyond_the_band_is_flagged_with_the_distance() -> None:
    message = net_flag(-50.9, -20.0)
    assert message is not None
    assert "-50.9% of NAV" in message, "the current net is named"
    assert "-20.0%" in message, "and so is the target it was measured against"
    assert "-30.9pp outside the +/-5pp band" in message


@pytest.mark.parametrize("gap_pp", [5.1, 12.0])
def test_the_flag_carries_the_gap_not_the_absolute_bet(gap_pp: float) -> None:
    """Two books the same distance from their targets read the same distance apart.

    The old rule reported |net|, which conflated a book that is 55% short with one that
    is 55% long: both were "outside +/-5%". What matters is the distance from the call,
    and the sign of that distance follows the direction the book has drifted in.
    """
    short_side = net_flag(-55.0, -55.0 + gap_pp)
    long_side = net_flag(55.0, 55.0 - gap_pp)

    assert short_side is not None and long_side is not None
    assert f"-{gap_pp:.1f}pp outside" in short_side, "under its target on the short side"
    assert f"+{gap_pp:.1f}pp outside" in long_side, "over its target on the long side"
    assert "-55.0% of NAV" in short_side and "55.0% of NAV" in long_side


def test_a_neutral_target_still_bands_around_zero() -> None:
    """When the signal does ask for zero, the band degenerates to the old rule."""
    assert net_flag(2.0, 0.0) is None
    assert net_flag(9.0, 0.0) is not None


def test_the_band_is_five_points() -> None:
    """Pinned so a later edit cannot quietly widen or narrow it."""
    assert NET_BAND_PP == 5.0


# ------------------------------------------------------------------ gross is unchanged


@pytest.mark.parametrize("gross_x", [1.80, 1.975, 2.00, 2.05])
def test_gross_inside_its_range_is_not_flagged(gross_x: float) -> None:
    assert gross_flag(gross_x) is None


def test_gross_above_the_overshoot_mark_is_flagged() -> None:
    assert "above the 2.05x overshoot mark" in (gross_flag(2.06) or "")


def test_gross_below_the_floor_is_flagged() -> None:
    assert "below the 1.80x floor" in (gross_flag(1.79) or "")


# ------------------------------------------------------------------ the rendered panel


def _render(monkeypatch, *, positions=POSITIONS, weights=TARGET_WEIGHTS, nav=NAV):
    """Render the real page with every read stubbed, so the exposure block varies."""
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
    monkeypatch.setattr(operational, "_get_daily_sleeve_pnl", lambda: [])
    monkeypatch.setattr(
        operational, "_get_proposed_trade",
        lambda: (_proposal(weights), "2026-10-05", nav),
    )
    monkeypatch.setattr(operational, "fetch_decision_for_date", lambda d: None)
    monkeypatch.setattr(operational, "fetch_last_cron_run", lambda job: None)
    # The risk panel is a different panel and reads the local price cache; stubbed so
    # this file tests the exposure block and nothing else.
    monkeypatch.setattr(operational, "_render_mctr_pctr", lambda nav, positions: None)
    operational._get_last_execution_run.clear()

    at = AppTest.from_file(str(APP), default_timeout=600)
    at.run()

    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _text(at) -> str:
    """Everything a reader sees: markdown, the status boxes, captions, and the metrics.

    Metrics are included because the reference points are in their deltas (the target
    and the hard cap under the gross figure), so a text check that skipped them would
    miss half of what the panel says.
    """
    chunks = [str(m.value) for m in at.markdown]
    for kind in ("info", "success", "error", "warning"):
        chunks += [str(e.value) for e in at.get(kind)]
    chunks += [str(c.value) for c in at.caption]
    chunks += [f"{m.label}: {m.value} {m.delta}" for m in at.metric]
    return " ".join(chunks)


def test_the_panel_says_net_is_not_targeted_at_zero(monkeypatch) -> None:
    text = _text(_render(monkeypatch))

    assert "Net is the sum of each asset's trend call. It is not targeted at zero." in text
    assert "near zero" not in text
    assert "no directional bet" not in text


def test_the_panel_counts_the_longs_and_shorts(monkeypatch) -> None:
    text = _text(_render(monkeypatch))

    assert "3 long, 5 short" in text


def test_the_panel_shows_todays_book_on_target(monkeypatch) -> None:
    """End to end on the live shape: no red box, and the line names the target."""
    text = _text(_render(monkeypatch))

    assert "On target: net -50.9% of NAV against a target of -52.9%" in text
    assert "Off target: net" not in text


def test_the_panel_still_shows_gross_against_two_x(monkeypatch) -> None:
    text = _text(_render(monkeypatch))

    assert "gross 1.98x" in text, "197,500 on 100,000, to two places"
    assert "hard cap 2.00x" in text
    assert "overshoot mark 2.05x" in text


def test_a_net_away_from_its_target_is_flagged(monkeypatch) -> None:
    """A long target against a short book: 60.9pp apart, which must be visible."""
    text = _text(_render(monkeypatch, weights={"SPY": 0.10}))

    assert "Off target: net is -50.9% of NAV against a target of +10.0%" in text
    assert "60.9pp outside the +/-5pp band" in text


def test_a_gross_breach_is_flagged_without_blaming_net(monkeypatch) -> None:
    """A smaller NAV lifts gross over the mark while net stays near its target."""
    text = _text(_render(monkeypatch, nav=90_000.0))

    assert "gross is 2.19x, above the 2.05x overshoot mark" in text
    assert "Off target: net" not in text
