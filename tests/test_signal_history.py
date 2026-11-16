"""Signal history by ticker: window, trend base, stored weight, and layout.

The section is one chart per ticker. These tests pin the things that would make
it quietly wrong rather than ugly: a window that does not start 120 sessions
before trading, a lookback line that is not the close from 120 sessions earlier,
a weight line that invents zeros for sessions with no snapshot, a price line
filled when the ticker has no history, and a grid that is not 3x3.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dashboard import signal_history
from dashboard.views import operational


def _closes(periods: int = 300, tickers: tuple[str, ...] = ("SPY", "TLT", "HYG", "GLD")) -> pd.DataFrame:
    index = pd.bdate_range("2025-06-02", periods=periods)
    data = {t: [100.0 + i for i in range(periods)] for t in tickers}
    return pd.DataFrame(data, index=index)


def _weights(first: str, last: str, tickers: tuple[str, ...] = ("SPY", "TLT", "HYG", "GLD")) -> pd.DataFrame:
    dates = pd.bdate_range(first, last)
    rows = []
    for d in dates:
        for i, t in enumerate(tickers):
            rows.append({"trade_date": d, "ticker": t, "weight": 0.1 * (i + 1)})
    return pd.DataFrame(rows)


class TestWindow:
    def test_starts_120_sessions_before_the_first_live_session(self) -> None:
        closes = _closes()
        first_live = pd.Timestamp("2026-05-04")
        window = signal_history.history_window(closes, first_live)

        sessions = signal_history.trading_sessions(closes)
        live_pos = int(sessions.searchsorted(first_live))
        assert window[0] == sessions[live_pos - 120]
        assert window[-1] == sessions[-1]
        assert len(window) == len(sessions) - (live_pos - 120)

    def test_falls_back_to_the_last_120_sessions_without_weights(self) -> None:
        closes = _closes()
        window = signal_history.history_window(closes, None)
        assert len(window) == 120
        assert window[-1] == closes.index[-1]

    def test_clamps_when_the_anchor_is_past_the_price_fetch(self) -> None:
        closes = _closes()
        window = signal_history.history_window(closes, pd.Timestamp("2030-01-01"))
        # The anchor is clamped to the final session, so the window is that
        # session plus the 120 before it.
        assert len(window) == 121
        assert window[-1] == closes.index[-1]


class TestTrendBase:
    def test_lookback_is_the_close_from_120_trading_sessions_earlier(self) -> None:
        closes = _closes()
        weights = _weights("2026-05-04", "2026-06-30")
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)
        history = signal_history.build_ticker_history(
            closes, weights, "SPY", window, first_live
        )

        full = closes["SPY"]
        for session in (window[0], window[75], window[-1]):
            pos = full.index.get_loc(session)
            expected = full.iloc[pos - 120]
            assert history["lookback"].loc[session] == expected
            assert history["price"].loc[session] == full.loc[session]

    def test_ticker_without_history_is_marked_not_zero_filled(self) -> None:
        closes = _closes(tickers=("SPY",))
        weights = _weights("2026-05-04", "2026-06-30", tickers=("SPY",))
        window = signal_history.history_window(closes, weights["trade_date"].min())
        history = signal_history.build_ticker_history(
            closes, weights, "GLD", window, weights["trade_date"].min()
        )

        assert history["has_price"] is False
        assert history["price"].isna().all()

        fig = signal_history.ticker_figure("GLD", history, window)
        notes = " ".join(str(a.text) for a in fig.layout.annotations)
        assert "No usable price history for GLD" in notes
        assert "not zero-filled" in notes


class TestStoredWeight:
    def test_zero_before_the_first_live_session_and_zero_when_not_held(self) -> None:
        sessions = pd.bdate_range("2026-01-05", periods=10)
        first_live = sessions[5]
        weights = pd.DataFrame(
            [
                {"trade_date": sessions[5], "ticker": "SPY", "weight": 0.2},
                {"trade_date": sessions[6], "ticker": "SPY", "weight": 0.2},
                {"trade_date": sessions[8], "ticker": "SPY", "weight": 0.5},
                # TLT was not held on sessions[6], but that snapshot exists, so a
                # missing row means zero -- not "carry the previous weight".
                {"trade_date": sessions[5], "ticker": "TLT", "weight": 0.1},
                {"trade_date": sessions[8], "ticker": "TLT", "weight": -0.1},
            ]
        )

        spy = signal_history.weight_series(weights, "SPY", sessions, first_live)
        tlt = signal_history.weight_series(weights, "TLT", sessions, first_live)

        assert (spy.iloc[:5] == 0.0).all()
        assert spy.loc[sessions[5]] == 0.2
        # sessions[7] has no stored snapshot: the step holds the previous weight.
        assert spy.loc[sessions[7]] == 0.2
        assert spy.loc[sessions[8]] == 0.5
        assert spy.loc[sessions[9]] == 0.5

        assert tlt.loc[sessions[5]] == 0.1
        assert tlt.loc[sessions[6]] == 0.0  # held none on a stored date
        assert tlt.loc[sessions[8]] == -0.1

    def test_a_ticker_with_no_stored_rows_returns_none(self) -> None:
        sessions = pd.bdate_range("2026-01-05", periods=4)
        weights = pd.DataFrame(
            [{"trade_date": sessions[0], "ticker": "SPY", "weight": 0.2}]
        )
        assert signal_history.weight_series(
            weights, "GLD", sessions, sessions[0]
        ) is None

    def test_a_ticker_with_only_null_weights_returns_none(self) -> None:
        sessions = pd.bdate_range("2026-01-05", periods=4)
        weights = pd.DataFrame(
            [{"trade_date": sessions[0], "ticker": "SPY", "weight": None}]
        )
        assert signal_history.weight_series(
            weights, "SPY", sessions, sessions[0]
        ) is None


class TestFigure:
    def test_colours_dashes_and_axes(self) -> None:
        closes = _closes()
        weights = _weights("2026-05-04", "2026-06-30")
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)
        history = signal_history.build_ticker_history(
            closes, weights, "SPY", window, first_live
        )
        fig = signal_history.ticker_figure("SPY", history, window)

        by_name = {trace.name: trace for trace in fig.data}
        base = signal_history.TREND_BASE_LEGEND
        assert set(by_name) == {"close", base, "held weight", "trend score"}
        assert by_name["close"].line.color == "#1d4ed8"
        assert by_name["close"].line.dash in (None, "solid")
        assert by_name[base].line.color == "#1d4ed8"
        assert by_name[base].line.dash == "dash"
        assert by_name["held weight"].line.color == signal_history.WEIGHT_COLOR
        assert by_name["held weight"].line.shape == "hv"

        # The weight is on the top panel's secondary axis; the score is below it.
        assert by_name["held weight"].yaxis == "y2"
        assert by_name["trend score"].yaxis == "y3"
        assert fig.layout.yaxis2.zeroline is True
        assert fig.layout.yaxis2.tickformat == ".0%"
        assert fig.layout.yaxis3.zeroline is True
        assert fig.layout.yaxis3.tickformat == ".0%"

        assert fig.layout.hovermode == "x unified"
        assert fig.layout.xaxis.fixedrange is True
        assert fig.layout.yaxis.fixedrange is True
        assert fig.layout.yaxis2.fixedrange is True
        assert fig.layout.yaxis3.fixedrange is True
        assert fig.layout.xaxis.range == (window[0], window[-1])

    def test_hover_names_date_close_lookback_and_weight(self) -> None:
        closes = _closes()
        weights = _weights("2026-05-04", "2026-06-30")
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)
        history = signal_history.build_ticker_history(
            closes, weights, "TLT", window, first_live
        )
        fig = signal_history.ticker_figure("TLT", history, window)

        templates = {trace.name: (trace.hovertemplate or "") for trace in fig.data}
        assert "close" in templates["close"]
        assert "close 120 sessions ago (signal lookback base, NOT a moving average)" in (
            templates[signal_history.TREND_BASE_LEGEND]
        )
        assert "held weight" in templates["held weight"]
        assert "trend score" in templates["trend score"]
        # Unified hover supplies the date header for all four traces.
        assert fig.layout.hovermode == "x unified"

    def test_trend_score_is_the_close_over_the_lookback_minus_one(self) -> None:
        closes = _closes()
        weights = _weights("2026-05-04", "2026-06-30")
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)
        history = signal_history.build_ticker_history(
            closes, weights, "SPY", window, first_live
        )

        full = closes["SPY"]
        for session in (window[0], window[60], window[-1]):
            pos = full.index.get_loc(session)
            expected = full.iloc[pos] / full.iloc[pos - 120] - 1.0
            assert history["trend_score"].loc[session] == pytest.approx(expected)

    def test_dead_zone_uses_each_tickers_own_vol(self) -> None:
        index = pd.bdate_range("2025-01-01", periods=200)
        calm = pd.Series(100.0, index=index)
        wild = pd.Series(100.0, index=index)
        calm.iloc[1::2] = 100.01
        wild.iloc[1::2] = 110.0
        closes = pd.DataFrame({"SPY": calm, "EFA": wild})

        spy_vol = signal_history.realised_vol(closes, "SPY")
        efa_vol = signal_history.realised_vol(closes, "EFA")
        assert spy_vol is not None and spy_vol > 0
        assert efa_vol is not None and efa_vol > 1.0

        spy_band = signal_history.dead_zone_half_width(spy_vol)
        efa_band = signal_history.dead_zone_half_width(efa_vol)
        assert spy_band is not None and spy_band > 0
        assert efa_band == pytest.approx(
            0.5 * efa_vol * (120 / 252) ** 0.5
        )
        assert efa_band > spy_band

    def test_the_lower_panel_draws_the_dead_zone_and_names_its_width(self) -> None:
        closes = _closes()
        weights = _weights("2026-05-04", "2026-06-30")
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)
        history = signal_history.build_ticker_history(
            closes, weights, "GLD", window, first_live
        )
        band = history["dead_zone"]
        assert band is not None and band > 0

        fig = signal_history.ticker_figure("GLD", history, window)
        rects = [s for s in fig.layout.shapes if s.type == "rect"]
        assert len(rects) == 1
        assert rects[0].y0 == pytest.approx(-band)
        assert rects[0].y1 == pytest.approx(band)
        assert fig.layout.yaxis3.title.text == "trend score"
        notes = " ".join(str(a.text) for a in fig.layout.annotations)
        assert f"dead zone ±{band:.1%}" in notes

    @pytest.mark.parametrize(
        "ticker,colour",
        [
            ("SPY", "#1d4ed8"),
            ("EFA", "#1d4ed8"),
            ("TLT", "#047857"),
            ("IEF", "#047857"),
            ("HYG", "#7c3aed"),
            ("LQD", "#7c3aed"),
            ("GLD", "#d97706"),
        ],
    )
    def test_price_line_uses_the_asset_class_palette(self, ticker: str, colour: str) -> None:
        assert signal_history._sleeve_colour(ticker) == colour

    def test_scroll_zoom_is_off(self) -> None:
        assert operational.PLOTLY_CONFIG["scrollZoom"] is False


class _FakeColumn:
    def __init__(self) -> None:
        self.markdowns: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def markdown(self, text: str) -> None:
        self.markdowns.append(text)


class TestLayout:
    def test_eight_charts_in_a_3x3_grid_with_a_legend_cell(self, monkeypatch) -> None:
        closes = _closes()
        weights = _weights(
            "2026-05-04",
            "2026-06-30",
            tickers=("SPY", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD"),
        )
        for ticker in ("EFA", "EEM", "IEF", "LQD"):
            closes[ticker] = closes["SPY"]
        first_live = weights["trade_date"].min()
        window = signal_history.history_window(closes, first_live)

        columns_calls: list[int] = []
        grids: list[list[_FakeColumn]] = []

        def fake_columns(n: int):
            columns_calls.append(n)
            cols = [_FakeColumn() for _ in range(n)]
            grids.append(cols)
            return cols

        drawn = []
        monkeypatch.setattr(signal_history.st, "columns", fake_columns)
        monkeypatch.setattr(signal_history.st, "caption", lambda *a, **k: None)
        monkeypatch.setattr(signal_history.st, "warning", lambda *a, **k: None)
        monkeypatch.setattr(operational, "_plot", lambda fig, **k: drawn.append(fig))

        signal_history.render_grid(closes, weights)

        assert columns_calls == [3, 3, 3]
        assert len(drawn) == 8
        titles = [f.layout.title.text for f in drawn]
        assert titles == [
            "SPY · equity", "EFA · equity", "EEM · equity",
            "TLT · rates", "IEF · rates", "HYG · credit",
            "LQD · credit", "GLD · commodity",
        ]
        # The ninth slot is the legend, not a chart.
        legend = " ".join(grids[-1][2].markdowns)
        assert "Solid line" in legend and "Dashed line" in legend and "step line" in legend

    def test_the_section_is_collapsed_by_default_and_last_on_the_page(self) -> None:
        source = Path(signal_history.__file__).read_text()
        assert 'st.expander("Signal history by ticker", expanded=False)' in source

        page = Path(operational.__file__).read_text()
        assert page.rstrip().endswith("render_signal_history()")
        assert "from dashboard.signal_history import render_signal_history" in page

    def test_the_price_source_is_the_live_boundary_not_the_committed_cache(self) -> None:
        source = Path(signal_history.__file__).read_text()
        assert "load_live_closes" in source
        assert "load_close_matrix" not in source
        assert "load_universe_close" not in source
