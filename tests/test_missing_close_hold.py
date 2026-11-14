"""Hold the previous targets when a ticker has no close for the latest session.

On 2026-07-28 four names -- EFA, HYG, IEF and LQD -- had no close for that session
while the other four did. The signal was still computed. The four became NaN,
`apply_rebalance_control` carried their previous weights forward, the joint gross
cap renormalised the four names that did have a close to their uncapped v/sigma,
and the final re-cap scaled the whole row down. SPY's target weight for the
2026-07-29 book came out at 0.3530 against 0.2727 the session before and 0.2744
the session after, and nothing said so.

These tests pin the two halves of the fix:

  - the failure reproduces exactly through the real pipeline, so the hold is not
    guarding against a hypothetical;
  - when any ticker lacks the latest close, `run_signal` sizes nothing, leaves the
    stored target weights untouched and mails "[HOLD] missing closes: ...".

The all-present case is here too, because a guard that also held a normal session
would be worse than the bug.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from signals.data_quality import missing_latest_close
from signals.etf_universe import UNIVERSE
from signals.trend_signal import (
    G_MAX_DEFAULT,
    MissingSizingInput,
    TRADING_DAYS,
    V_DEFAULT,
    W_MAX_DEFAULT,
    _hysteresis_signal,
    apply_rebalance_control,
    compute_dead_zone,
    compute_trend,
    shift_to_next_day,
    to_position_matrix,
)

# The session the four names were missing, the session before it, and the dates
# the stored signal carried (the 2026-07-28 targets, written on 2026-07-27).
GAP_SESSION = "2026-07-28"
HELD_AS_OF = "2026-07-27"
GAPPED = ("EFA", "HYG", "IEF", "LQD")

# The weights that were actually stored and sized on 2026-07-28, read from the
# positions table. SPY 0.2727 is the weight the hold has to preserve.
STORED_WEIGHTS = {
    "SPY": 0.2727,
    "EFA": 0.2611,
    "EEM": 0.1513,
    "TLT": -0.2759,
    "IEF": -0.2709,
    "HYG": 0.2804,
    "LQD": 0.2803,
    "GLD": -0.2072,
}
STORED_PRICES = {
    "SPY": 727.65,
    "EFA": 103.37,
    "EEM": 61.07,
    "TLT": 81.87,
    "IEF": 92.20,
    "HYG": 78.08,
    "LQD": 104.87,
    "GLD": 371.08,
}

# Annualised vols of the real book that week (dashboard, 2026-07-28 read). The
# reproduction is only meaningful if the weights it lands on are the ones the live
# run landed on, and those are set by the vols.
VOLS = {
    "SPY": 0.150,
    "EFA": 0.150,
    "EEM": 0.250,
    "TLT": 0.110,
    "IEF": 0.062,
    "HYG": 0.040,
    "LQD": 0.066,
    "GLD": 0.250,
}

SEED = 31


def _close_frame(*, gapped: bool) -> pd.DataFrame:
    """A deterministic close matrix ending on the gapped session.

    Long enough for the 120 session trend lookback and the 63 session vol window,
    with each name's own vol, so the weights land where the live ones did.
    """
    index = pd.date_range(end=GAP_SESSION, periods=320, freq="B")
    rng = np.random.default_rng(SEED)
    columns = {
        ticker: 100.0
        * np.exp(
            np.cumsum(
                rng.normal(0.0004, VOLS[ticker] / np.sqrt(252.0), len(index))
            )
        )
        for ticker in UNIVERSE
    }
    close = pd.DataFrame(columns, index=index)[list(UNIVERSE)]
    if gapped:
        # What load_universe_close's outer join produces when four parquets have
        # no row for the session the other four do.
        close.loc[index[-1], list(GAPPED)] = np.nan
    return close


def _held_weights(close: pd.DataFrame) -> pd.Series:
    """The weights the pipeline produces on the last session of `close`.

    This is the row `run_signal` writes as the next session's target, before
    `shift_to_next_day` labels it.
    """
    tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    desired = to_position_matrix(tidy)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    return held.iloc[-1]


def _held_weights_before_the_sizing_guard(close: pd.DataFrame) -> pd.Series:
    """The sizes the pre-fix cap produced for `close`, kept for the record.

    `compute_trend` now refuses a frame in which a name that should have a close
    has none, so the gapped frame cannot be run through it at all. This is the
    old construction -- identical to compute_trend except for the gross cap,
    which is what the fix changed -- so the historic number stays pinned as the
    outcome the guard exists to prevent.
    """
    L, W = 120, 63
    log_ret = np.log(close).diff()
    trail_ret = close / close.shift(L) - 1.0
    sigma = log_ret.rolling(W, min_periods=W).std() * np.sqrt(TRADING_DAYS)
    defined = trail_ret.notna() & sigma.notna()
    signal = _hysteresis_signal(
        trail_ret, compute_dead_zone(sigma, L, 0.5), defined, True
    )
    raw_weight = signal * (V_DEFAULT / sigma).clip(upper=W_MAX_DEFAULT)
    gross = raw_weight.abs().sum(axis=1, skipna=True)  # the pre-fix cap: NaN counted as 0
    scale = (G_MAX_DEFAULT / gross).clip(upper=1.0).where(gross > 0, 1.0)
    desired = raw_weight.mul(scale, axis=0).where(defined)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    return held.iloc[-1]


# --------------------------------------------------------------- the guard itself


def test_a_missing_latest_close_is_named() -> None:
    close = _close_frame(gapped=True)
    # Column order, which is universe order: EFA, IEF, HYG, LQD.
    assert missing_latest_close(close) == ["EFA", "IEF", "HYG", "LQD"]


def test_a_clean_session_has_nothing_missing() -> None:
    assert missing_latest_close(_close_frame(gapped=False)) == []


def test_a_leading_nan_is_not_a_missing_latest_close() -> None:
    """Staggered inception is legitimate: only the last row matters here."""
    close = _close_frame(gapped=False)
    close.loc[close.index[0], "GLD"] = np.nan
    assert missing_latest_close(close) == []


def test_a_zero_close_counts_as_missing() -> None:
    """A zero cannot be logged from, so it is not a close the signal can use."""
    close = _close_frame(gapped=False)
    close.loc[close.index[-1], "HYG"] = 0.0
    assert missing_latest_close(close) == ["HYG"]


# ------------------------------------------------------------- the 2026-07-29 case


def test_the_2026_07_29_renormalisation_reproduces_without_the_hold() -> None:
    """The bug, in miniature: one missing session moves SPY from 0.27 to 0.35.

    This is the pipeline the fix stops before it runs. It is asserted rather than
    described because a hold that guards against nothing would look identical.
    """
    clean = _held_weights(_close_frame(gapped=False))
    gapped = _held_weights_before_the_sizing_guard(_close_frame(gapped=True))

    # The live book's SPY weight that week was 0.2727; the reproduction lands at
    # 0.2676, which is the same number for the purposes of this test.
    assert 0.24 < clean["SPY"] < 0.30, clean["SPY"]

    # With four names NaN, the joint gross cap no longer binds on the four that
    # remain, so they jump to their uncapped v/sigma and are then re-capped.
    assert gapped["SPY"] > 1.2 * clean["SPY"], (clean["SPY"], gapped["SPY"])
    assert gapped["SPY"] == pytest.approx(0.3458, abs=0.01)

    # The renormalisation is not confined to SPY: the four names that kept their
    # close were all inflated, and the four that were carried were all scaled down.
    assert abs(gapped["TLT"]) == pytest.approx(0.3458, abs=0.01)
    for ticker in GAPPED:
        assert abs(gapped[ticker]) < abs(clean[ticker]), ticker

    # And it is a pure rescale of a still-2.0 gross book: nothing about the gross
    # cap looks wrong from the outside, which is why nothing flagged it.
    assert gapped.abs().sum() == pytest.approx(2.0, abs=0.01)


# ----------------------------------------------------------------- the job itself


def _drive_signal_job(monkeypatch, no_real_email, *, close: pd.DataFrame, stored: bool = True):
    """Run the real `run_signal.main()` with only I/O replaced.

    Returns what the job wrote, what it recorded, the email it sent, and whether
    the sizing pipeline was ever reached.
    """
    import execution.calendar_utils as cal
    import execution.snapshot as snapshot
    import risk.sleeve_pnl as sleeve_pnl
    import scripts.run_signal as run_signal
    import signals.dividends as div
    import signals.etf_universe as etf
    import signals.trend_signal as trend
    from dashboard import supabase_client as sb

    written: list[tuple[str, str]] = []
    decisions: list[tuple[str, str]] = []
    recorded: list[tuple[str, str]] = []
    sized: list[int] = []

    def _compute_trend(*args, **kwargs):
        sized.append(1)
        return trend.__dict__["_real_compute_trend"](*args, **kwargs)

    if "_real_compute_trend" not in trend.__dict__:
        trend._real_compute_trend = trend.compute_trend
    monkeypatch.setattr(trend, "compute_trend", _compute_trend)

    monkeypatch.setattr(cal, "is_trading_day", lambda day: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, day: False)
    monkeypatch.setattr(cal, "record_run", lambda job, day: recorded.append((job, day)))
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(div, "ingest", lambda *a, **k: {
        "ok": True, "tickers": 8, "distributions": 0, "as_of": GAP_SESSION,
    })

    settings = {
        "signal_as_of_date": HELD_AS_OF if stored else None,
        "signal_target_weights": json.dumps(STORED_WEIGHTS) if stored else None,
        "signal_close_prices": json.dumps(STORED_PRICES) if stored else None,
    }
    monkeypatch.setattr(sb, "get_setting", lambda key: settings.get(key))
    monkeypatch.setattr(
        sb, "set_setting", lambda key, value: written.append((key, value)) or True
    )
    monkeypatch.setattr(
        sb, "write_decision", lambda day, decision: decisions.append((day, decision)) or True
    )
    monkeypatch.setattr(sb, "get_supabase_client", lambda: None)
    monkeypatch.setattr(sb, "write_close_equity", lambda closes, *, source: True)

    # Bookkeeping: a hold must not need a broker to be a hold.
    monkeypatch.setattr(snapshot, "open_paper_client", lambda **k: None)
    monkeypatch.setattr(
        snapshot, "refresh_snapshot",
        lambda **k: {"tickers": 0, "nav": None, "skipped": "no client in this test"},
    )
    monkeypatch.setattr(
        sleeve_pnl, "record_daily_sleeve_pnl",
        lambda **k: {"status": "skipped", "reason": "no snapshot in this test"},
    )
    monkeypatch.setattr(run_signal, "RETRY_INTERVAL_SECS", 0)

    code = run_signal.main(max_secs=60)

    return {
        "code": code,
        "written": written,
        "decisions": decisions,
        "recorded": recorded,
        "sized": sized,
        "emails": list(no_real_email),
    }


def test_a_missing_close_holds_the_previous_targets(monkeypatch, no_real_email) -> None:
    """2026-07-29: SPY must stay at about 0.273, and the email must say [HOLD]."""
    out = _drive_signal_job(
        monkeypatch, no_real_email, close=_close_frame(gapped=True)
    )

    assert out["code"] == 0, "a hold is a deliberate outcome, not a failure"
    assert len(out["emails"]) == 1
    email = out["emails"][0]
    assert email["subject"] == "[HOLD] missing closes: EFA, HYG, IEF, LQD", email["subject"]
    assert "Targets held unchanged from 2026-07-27" in email["body"], email["body"]

    # Nothing about the signal was rewritten: not the weights, not the date, not
    # the prices, and no proposal the morning job could approve.
    assert out["written"] == [], f"nothing may be written, got {out['written']}"
    assert out["decisions"] == [], out["decisions"]

    # The sizing pipeline was never reached, which is what makes the weights
    # unchanged structurally rather than by a later comparison.
    assert out["sized"] == [], "no target may be sized from an incomplete session"

    # Not recorded, so a re-run after the closes land can still size normally.
    assert out["recorded"] == []


def test_the_held_targets_are_not_renormalised(monkeypatch, no_real_email) -> None:
    """The held case keeps every stored weight, and the gross, exactly as they were.

    The alternative the fix exists to prevent is on the record: the pipeline would
    have produced SPY 0.3458 (0.3530 live) by renormalising the four names that had
    a close across the four that did not.
    """
    out = _drive_signal_job(
        monkeypatch, no_real_email, close=_close_frame(gapped=True)
    )

    body = out["emails"][0]["body"]

    # The email shows the book that is actually in force: every stored weight, at
    # the stored size. SPY stays at 0.2727 and the four names that had no close are
    # not scaled down to make room for the four that did.
    assert "no weights were renormalised" in body, body
    for ticker, weight in STORED_WEIGHTS.items():
        assert f"{ticker} {weight:+.4f}" in body, (ticker, body)
    assert "SPY +0.2727" in body

    # ... and the renormalised row is nowhere in the run's output.
    assert out["written"] == []
    assert "0.3458" not in body and "0.353" not in body, body

    # The gross the book keeps is the gross it had, not a re-cap of a partial row.
    assert sum(abs(v) for v in STORED_WEIGHTS.values()) == pytest.approx(2.0, abs=0.01)

    # The pre-fix pipeline's own answer for this session, for contrast: it does
    # renormalise, and every one of the four names that kept a close is inflated.
    # (The pipeline itself now refuses the frame -- see the test below.)
    renormalised = _held_weights_before_the_sizing_guard(_close_frame(gapped=True))
    assert renormalised["SPY"] == pytest.approx(0.3458, abs=0.01)
    for ticker in ("SPY", "EEM", "TLT", "GLD"):
        assert abs(renormalised[ticker]) > abs(STORED_WEIGHTS[ticker]), ticker


def test_the_sizing_layer_refuses_the_gapped_frame_outright() -> None:
    """Defence in depth: the hold is no longer the only thing standing in the way.

    Even with `run_signal` bypassed, `compute_trend` refuses to size the session
    the four names are missing, and names them. That is what stops the gross cap
    from renormalising the four names that did have a close.
    """
    with pytest.raises(MissingSizingInput) as excinfo:
        compute_trend(_close_frame(gapped=True), L=120, long_short=True, k_dead_zone=0.5)

    message = str(excinfo.value)
    assert message.startswith("cannot size with missing values: ")
    for ticker in GAPPED:
        assert ticker in message, (ticker, message)
    assert GAP_SESSION in message, message


def test_a_complete_session_still_sizes_normally(monkeypatch, no_real_email) -> None:
    """The guard must not turn a normal evening into a hold."""
    out = _drive_signal_job(
        monkeypatch, no_real_email, close=_close_frame(gapped=False)
    )

    assert out["code"] == 0
    assert out["emails"][0]["subject"].startswith("[OK]"), out["emails"][0]["subject"]
    assert out["sized"] == [1], "the pipeline must run on a complete session"

    written = dict(out["written"])
    assert "signal_target_weights" in written
    weights = json.loads(written["signal_target_weights"])
    assert set(weights) == set(UNIVERSE)
    assert weights["SPY"] == pytest.approx(0.2676, abs=0.01)
    assert written["signal_as_of_date"] == GAP_SESSION
    assert out["decisions"] == [(GAP_SESSION, "proposed")]
    assert out["recorded"] == [("run_signal", out["recorded"][0][1])]


def test_a_first_run_with_no_previous_targets_fails_instead_of_holding(
    monkeypatch, no_real_email
) -> None:
    """Nothing to hold is not a hold: with no stored target the run must fail."""
    out = _drive_signal_job(
        monkeypatch, no_real_email, close=_close_frame(gapped=True), stored=False
    )

    assert out["code"] == 1
    assert out["written"] == []
    assert out["emails"][0]["subject"].startswith("[FAIL]")
    assert "no previous target weights to hold" in out["emails"][0]["body"]
