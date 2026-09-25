"""Tests for signals/raw_repair.py -- filling vendor gaps in freshly fetched closes.

The repair exists because a hole is silent: it does not fail anything, it just
moves the weights. So the tests care about three things -- that a real hole is
found and a staggered inception is not, that the per-ticker cap refuses rather
than half-patches, and that a repair which cannot run leaves the data untouched
for the gate to block on.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from signals.raw_repair import (
    MAX_HOLE_REPAIR_SESSIONS,
    SOURCE_TAG,
    adjustment_ratio,
    close_matrix,
    repair_frames,
    select_holes_for_repair,
)

DATES = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
HOLE = "2026-09-23"


def _frame(dates: list[str], closes: list[float], adj_ratio: float = 1.0) -> pd.DataFrame:
    """A raw parquet in the shape the pipeline stores: OHLC + adj_close."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="date")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": [c * adj_ratio for c in closes],
            "volume": [1_000] * len(closes),
        },
        index=idx,
    )


class _Bar:
    """Stands in for an Alpaca bar object."""

    def __init__(self, close: float, volume: int = 995_588) -> None:
        self.open = self.high = self.low = self.close = close
        self.volume = volume


def _lookup_returning(close: float, volume: int = 995_588):
    def _lookup(tickers, dates):
        return {(t, d.date()): _Bar(close, volume) for t in tickers for d in dates}
    return _lookup


def _lookup_empty(tickers, dates):
    return {}


def _gapped_frames(*, efa_missing: list[str] = (HOLE,), efa_adj_ratio: float = 1.0) -> dict:
    """SPY full, EFA missing `efa_missing`. This is the 2026-09-24 shape."""
    efa_dates = [d for d in DATES if d not in efa_missing]
    return {
        "SPY": _frame(DATES, [500.0, 501.0, 502.0, 503.0]),
        "EFA": _frame(
            efa_dates,
            [100.0, 101.0, 103.0][: len(efa_dates)],
            adj_ratio=efa_adj_ratio,
        ),
    }


# ------------------------------------------------------------------ detecting holes

def test_outer_join_turns_a_missing_session_into_a_hole() -> None:
    """The mechanism: EFA absent on one date the universe has becomes NaN there."""
    close = close_matrix(_gapped_frames())
    assert close.loc[pd.Timestamp(HOLE), "EFA"] != close.loc[pd.Timestamp(HOLE), "EFA"]  # NaN

    repairable, refused = select_holes_for_repair(close)

    assert [(g["ticker"], str(g["date"].date())) for g in repairable] == [("EFA", HOLE)]
    assert refused == {}


def test_staggered_inception_is_not_a_hole() -> None:
    """EFA and EEM start years after SPY. Leading NaN must never be repaired.

    Filling an inception with a fabricated price would be inventing history, and
    the gate deliberately does not block on leading NaN either.
    """
    frames = {
        "SPY": _frame(DATES, [500.0, 501.0, 502.0, 503.0]),
        "EFA": _frame(DATES[2:], [100.0, 101.0]),
    }
    repairable, refused = select_holes_for_repair(close_matrix(frames))

    assert repairable == []
    assert refused == {}


def test_a_ticker_with_no_observations_at_all_is_refused() -> None:
    frames = {
        "SPY": _frame(DATES, [500.0, 501.0, 502.0, 503.0]),
        "EFA": _frame(DATES, [np.nan] * 4),
    }
    repairable, refused = select_holes_for_repair(close_matrix(frames))

    assert repairable == []
    assert "no observations" in refused["EFA"]


# ------------------------------------------------------------------ the per-ticker cap

def test_two_missing_sessions_are_still_repairable() -> None:
    """At the cap. One or two sessions is a vendor gap."""
    frames = _gapped_frames(efa_missing=DATES[1:3])
    repairable, refused = select_holes_for_repair(close_matrix(frames))

    assert len(repairable) == MAX_HOLE_REPAIR_SESSIONS
    assert refused == {}


def test_three_missing_sessions_are_refused_and_none_are_filled() -> None:
    """Over the cap: refuse the ticker whole rather than half-patch a broken history."""
    frames = _gapped_frames(efa_missing=DATES[1:])
    repairable, refused = select_holes_for_repair(close_matrix(frames))

    assert repairable == []
    assert "exceeds the 2 per-ticker repair cap" in refused["EFA"]


def test_a_refused_ticker_is_not_filled_even_though_bars_are_available(caplog) -> None:
    """The cap is not advisory: a working lookup must still not fill it."""
    frames = _gapped_frames(efa_missing=DATES[1:])
    before = frames["EFA"].copy()

    with caplog.at_level(logging.ERROR, logger="signals.raw_repair"):
        out, report = repair_frames(frames, bar_lookup=_lookup_returning(104.39))

    assert report["filled"] == []
    assert report["holes"] == 0
    pd.testing.assert_frame_equal(out["EFA"], before)
    assert "hole repair REFUSED for EFA" in caplog.text
    assert "the data-quality gate will block the signal" in caplog.text


# ------------------------------------------------------------------ filling

def test_repair_fills_the_hole_and_logs_the_source(caplog) -> None:
    frames = _gapped_frames(efa_adj_ratio=0.98)

    with caplog.at_level(logging.INFO, logger="signals.raw_repair"):
        out, report = repair_frames(frames, bar_lookup=_lookup_returning(104.39))

    assert [f["ticker"] for f in report["filled"]] == ["EFA"]
    assert report["filled"][0]["date"] == pd.Timestamp(HOLE)
    assert report["unresolved"] == []

    efa = out["EFA"]
    assert pd.Timestamp(HOLE) in efa.index
    assert len(efa) == len(DATES), "the hole is filled, so EFA now covers every session"
    assert efa.index.is_monotonic_increasing
    assert not efa.index.has_duplicates

    row = efa.loc[pd.Timestamp(HOLE)]
    # Alpaca gives unadjusted bars; the row is scaled onto EFA's own basis.
    assert row["close"] == pytest.approx(104.39)
    assert row["adj_close"] == pytest.approx(104.39 * 0.98)
    assert row["volume"] == 995_588

    assert f"source={SOURCE_TAG}" in caplog.text
    assert "filled EFA 2026-09-23" in caplog.text


def test_repair_does_not_touch_a_frame_that_gained_nothing() -> None:
    """SPY had no hole, so its frame must come back identical."""
    frames = _gapped_frames()
    before = frames["SPY"].copy()

    out, _ = repair_frames(frames, bar_lookup=_lookup_returning(104.39))

    pd.testing.assert_frame_equal(out["SPY"], before)


def test_adjustment_ratio_uses_that_tickers_own_factor() -> None:
    """The inserted row must not introduce a second adj/close convention."""
    frame = _frame(DATES[:2], [100.0, 101.0], adj_ratio=0.5)
    assert adjustment_ratio(frame, pd.Timestamp(HOLE)) == pytest.approx(0.5)


def test_adjustment_ratio_falls_back_to_one_without_usable_prior_history() -> None:
    frame = _frame([HOLE], [100.0])
    assert adjustment_ratio(frame, pd.Timestamp(HOLE)) == pytest.approx(1.0)


# ------------------------------------------------------------------ nothing is fabricated

def test_a_hole_without_a_bar_is_left_in_place(caplog) -> None:
    """No real bar means no row. The hole stays and the gate blocks."""
    frames = _gapped_frames()

    with caplog.at_level(logging.ERROR, logger="signals.raw_repair"):
        out, report = repair_frames(frames, bar_lookup=_lookup_empty)

    assert report["filled"] == []
    assert [(u["ticker"], str(u["date"].date())) for u in report["unresolved"]] == [("EFA", HOLE)]
    assert pd.Timestamp(HOLE) not in out["EFA"].index
    assert "leaving the hole in place" in caplog.text


def test_a_raising_lookup_is_skipped_rather_than_propagated(caplog) -> None:
    """A repair that cannot run must not break the ingest that called it.

    The hole is reported as unresolved rather than silently dropped, so the
    caller can still tell that the repair did not happen.
    """
    def _boom(tickers, dates):
        raise ConnectionError("connection reset by peer")

    frames = _gapped_frames()
    before = frames["EFA"].copy()

    with caplog.at_level(logging.WARNING, logger="signals.raw_repair"):
        out, report = repair_frames(frames, bar_lookup=_boom)

    assert report["filled"] == []
    assert [(u["ticker"], str(u["date"].date())) for u in report["unresolved"]] == [("EFA", HOLE)]
    pd.testing.assert_frame_equal(out["EFA"], before)
    assert "bar lookup raised (ConnectionError" in caplog.text


def test_repair_is_idempotent() -> None:
    """Running it twice must not add a second row for the same session."""
    frames = _gapped_frames()
    out, _ = repair_frames(frames, bar_lookup=_lookup_returning(104.39))
    again, report = repair_frames(out, bar_lookup=_lookup_returning(104.39))

    assert report["holes"] == 0
    assert report["filled"] == []
    pd.testing.assert_frame_equal(again["EFA"], out["EFA"])


def test_repair_of_an_empty_universe_is_a_no_op() -> None:
    out, report = repair_frames({}, bar_lookup=_lookup_returning(104.39))
    assert out == {} and report["filled"] == []


# ------------------------------------------------------------------ ingest wiring

def test_ingest_repairs_before_writing_raw(tmp_path, monkeypatch, caplog) -> None:
    """The live path: ingest repairs what it fetched, then writes."""
    import signals.etf_universe as eu
    import signals.raw_repair as rr

    order: list[str] = []
    frames = _gapped_frames()

    monkeypatch.setattr(eu, "fetch", lambda tickers, start, end: frames)
    monkeypatch.setattr(
        eu, "write_raw", lambda data, raw_dir: order.append("write_raw") or True
    )

    real_repair = rr.repair_frames

    def _spy_repair(fetched, *args, **kwargs):
        order.append("repair")
        kwargs["bar_lookup"] = _lookup_returning(104.39)
        return real_repair(fetched, *args, **kwargs)

    monkeypatch.setattr(rr, "repair_frames", _spy_repair)

    with caplog.at_level(logging.INFO, logger="signals.raw_repair"):
        eu.ingest(["SPY", "EFA"], raw_dir=tmp_path)

    assert order == ["repair", "write_raw"], "the repair must happen before the write"
    assert f"source={SOURCE_TAG}" in caplog.text
