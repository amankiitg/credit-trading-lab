"""The risk panel's covariance: current closes, no padding, no zero fill.

The panel used to build Sigma from `data/raw/*.parquet` -- a build-time snapshot that
stopped in April/June 2026 on this repository -- and then called `pct_change()` with
pandas' default `fill_method='pad'`, which turns the ten-month gap between the last
cached price and the live book into a run of exactly-zero daily returns. `Sigma` was
then `fillna(0)`-ed, so any pair that could not be estimated was recorded as
*covariance zero* rather than as missing. Both are measurements the data does not
support: a padded return says "this ETF did not move for months", and a zero
covariance says "these two are independent", and neither is true.

The outcome was a risk panel that reported the equity sleeve at 79% of the book's
risk on 2026-10-30 and at 7-9% two sessions later, purely as a function of which
sleeve the stale window happened to flatter. The tests below pin the three properties
that make the number trustworthy again: the prices are current, a ticker without a
history is named instead of zeroed, and the diagnosed equity share reproduces on a
frozen copy of the real closes.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dashboard import live_prices
from dashboard.live_prices import LIVE_LOOKBACK_DAYS, window_returns
from risk.live_risk import (
    COV_WINDOW_SESSIONS,
    MIN_RETURN_OBS,
    mctr_pctr,
    risk_contributions,
    select_eligible_returns,
)
from signals.etf_universe import ASSET_CLASS, UNIVERSE

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLOSES_CSV = FIXTURES / "live_closes_to_2026-11-02.csv"
WEIGHTS_JSON = FIXTURES / "live_book_weights.json"


def _fixture_closes() -> pd.DataFrame:
    return pd.read_csv(CLOSES_CSV, index_col="date", parse_dates=["date"])


def _fixture_weights(as_of: str) -> pd.Series:
    raw = json.loads(WEIGHTS_JSON.read_text())["weights"][as_of]
    return pd.Series(raw, dtype="float64")


def _equity_share(weights: pd.Series, rets: pd.DataFrame) -> float:
    contrib, excluded = risk_contributions(weights, rets)
    equity = [t for t in contrib.index if ASSET_CLASS[t] == "equity"]
    return float(contrib.loc[equity, "pctr"].sum())


class TestLiveClosesNotTheCommittedSnapshot:
    """Requirement 1: the covariance comes from current closes, never the cache."""

    def test_module_has_no_path_to_the_committed_cache(self):
        """The live path must not even import the snapshot loader.

        `load_universe_close` / `load_close_matrix` read `data/raw/*.parquet`, which is
        committed to the repository and refreshed only inside the signal cron's own
        container. Reaching either from the dashboard is what made the panel stale.
        """
        source = Path(live_prices.__file__).read_text()
        # Executable code only: the module docstring explains what it replaced.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) or isinstance(node, ast.Import):
                names = [a.name for a in node.names] + [getattr(node, "module", None)]
                flat = " ".join(n for n in names if n)
                assert "loader" not in flat, flat
        called = {
            node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, (ast.Attribute, ast.Name))
        }
        for forbidden in ("load_close_matrix", "load_universe_close"):
            assert forbidden not in called, (
                f"{forbidden} is called in dashboard/live_prices.py -- the panel must "
                "not read the committed price snapshot"
            )
        # And it does use the boundary run_signal refreshes through.
        assert "signals.load" in source and "fetch" in source

    def test_window_ends_at_the_latest_session_fetched(self, monkeypatch):
        """A fetch that reaches today wins over a stale parquet on disk.

        The committed snapshot on this branch ends 2026-06-15 (and its SPY column
        2026-04-15). If any code path consulted it, the window could not end in
        November.
        """
        live_prices.load_live_closes.clear()

        dates = pd.bdate_range("2026-02-02", "2026-11-10")
        rng = np.random.default_rng(7)
        frames = {
            t: pd.DataFrame(
                {"adj_close": 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))},
                index=dates,
            )
            for t in UNIVERSE
        }

        def fake_fetch(tickers, start, end):
            assert set(tickers) == set(UNIVERSE)
            return frames

        monkeypatch.setattr("signals.load.fetch", fake_fetch)

        rets = live_prices.load_live_returns(tickers=tuple(UNIVERSE))

        assert rets.index.max() == pd.Timestamp("2026-11-10")
        assert len(rets) == COV_WINDOW_SESSIONS

        committed = Path("data/raw/SPY.parquet")
        if committed.exists():
            stale_end = pd.read_parquet(committed).index.max()
            assert stale_end < rets.index.max(), (
                "the fixture fetch did not beat the committed cache -- test is vacuous"
            )

    def test_fetch_failure_raises_instead_of_falling_back(self, monkeypatch):
        live_prices.load_live_closes.clear()

        def boom(tickers, start, end):
            raise RuntimeError("yfinance down")

        monkeypatch.setattr("signals.load.fetch", boom)
        with pytest.raises(RuntimeError, match="yfinance down"):
            live_prices.load_live_returns(tickers=("SPY", "EFA"))


class TestNoZeroPadding:
    """Requirement 2: no padded prices, no zero-filled covariance."""

    def test_gap_is_left_missing_not_padded_to_zero(self):
        """A hole inside the window yields NaN returns, not zeros.

        This is the shape of the stale cache: a column that did not trade while the
        calendar ran on. `pct_change()` with the default `fill_method='pad'`
        reported the missing sessions as a 0.0% move, which a covariance reads as a
        real quiet day.
        """
        dates = pd.bdate_range("2026-01-02", periods=130)
        closes = pd.DataFrame(
            {
                "A": np.linspace(100, 120, len(dates)),
                "B": np.linspace(50, 55, len(dates)),
                "GAPPED": np.linspace(10, 11, len(dates)),
            },
            index=dates,
        )
        # GAPPED has no close for five sessions inside the 120-return window.
        closes.loc[dates[70]:dates[74], "GAPPED"] = np.nan

        rets = window_returns(closes, sessions=120)

        assert len(rets) == 120
        hole = rets["GAPPED"].isna()
        # Five missing closes, plus the return that would have divided by the
        # last of them: all NaN, none 0.0.
        assert int(hole.sum()) == 6, hole.sum()
        assert not (rets["GAPPED"].dropna() == 0.0).any()
        assert select_eligible_returns(rets)[1]["GAPPED"].startswith("incomplete")

    def test_trailing_gap_shortens_the_window_instead_of_padding_it(self):
        """A ticker whose history stops early anchors the window, it does not emit
        zeros for the sessions after its last close."""
        dates = pd.bdate_range("2026-01-02", periods=150)
        closes = pd.DataFrame(
            {
                "A": np.linspace(100, 120, len(dates)),
                "B": np.linspace(50, 55, len(dates)),
                "LATE": np.linspace(10, 11, len(dates)),
            },
            index=dates,
        )
        # LATE stops trading 25 sessions before the others.
        closes.loc[dates[-25]:, "LATE"] = np.nan

        rets = window_returns(closes, sessions=120)

        assert rets.index[-1] == dates[-26]
        assert len(rets) == 120
        assert rets["LATE"].notna().all()
        assert not (rets["LATE"] == 0.0).any()

    def test_missing_ticker_is_excluded_not_zero_filled(self):
        """A ticker short of returns is dropped, and the rest are unchanged.

        The old code kept the ticker with a zero covariance row/column, which leaves
        its weight in `w' Sigma w` while claiming it carries no risk. Excluding it must
        reproduce the result of a book that never held it.
        """
        rng = np.random.default_rng(11)
        n, k = COV_WINDOW_SESSIONS, 4
        tickers = ["A", "B", "C", "SHORT"]
        rets = pd.DataFrame(
            rng.normal(0, 0.01, (n, k)),
            index=pd.bdate_range("2026-01-02", periods=n),
            columns=tickers,
        )
        # SHORT has fewer than MIN_RETURN_OBS observations inside the window.
        rets.loc[rets.index[:-10], "SHORT"] = np.nan

        weights = pd.Series({"A": 0.4, "B": 0.3, "C": -0.3, "SHORT": 0.5})

        contrib, excluded = risk_contributions(weights, rets)

        assert "SHORT" in excluded
        assert "insufficient history" in excluded["SHORT"]
        assert "SHORT" not in contrib.index
        assert abs(float(contrib["pctr"].sum()) - 1.0) <= 1e-6

        # Identical to a book that never held it: no dilution, no zero row.
        without = risk_contributions(
            pd.Series({"A": 0.4, "B": 0.3, "C": -0.3}), rets[["A", "B", "C"]]
        )[0]
        np.testing.assert_allclose(
            contrib[["mctr", "ctr", "pctr"]].to_numpy(),
            without[["mctr", "ctr", "pctr"]].to_numpy(),
            rtol=0, atol=0,
        )

    def test_hole_inside_the_window_also_excludes(self):
        """A gap in the middle is not "enough history": the column is incomplete."""
        rng = np.random.default_rng(5)
        n = COV_WINDOW_SESSIONS
        rets = pd.DataFrame(
            rng.normal(0, 0.01, (n, 3)),
            index=pd.bdate_range("2026-01-02", periods=n),
            columns=["A", "B", "C"],
        )
        rets.iloc[n // 2, rets.columns.get_loc("C")] = np.nan

        eligible, excluded = select_eligible_returns(rets)

        assert "C" in excluded and "incomplete" in excluded["C"]
        assert list(eligible.columns) == ["A", "B"]

    def test_mctr_pctr_refuses_a_matrix_with_holes(self):
        """The strict core raises rather than filling Sigma with zeros."""
        rng = np.random.default_rng(3)
        n = 40
        rets = pd.DataFrame(
            rng.normal(0, 0.01, (n, 2)),
            index=pd.bdate_range("2026-01-02", periods=n),
            columns=["A", "B"],
        )
        rets.iloc[5, 0] = np.nan

        with pytest.raises(ValueError, match="holes"):
            mctr_pctr(pd.Series({"A": 0.5, "B": 0.5}), rets)

    def test_source_has_no_zero_fill(self):
        import risk.live_risk as live_risk

        source = Path(live_risk.__file__).read_text()
        assert "fillna(0" not in source
        assert "fillna(0.0)" not in source

    def test_min_obs_is_the_documented_floor(self):
        rng = np.random.default_rng(2)
        n = COV_WINDOW_SESSIONS
        rets = pd.DataFrame(
            rng.normal(0, 0.01, (n, 2)),
            index=pd.bdate_range("2026-01-02", periods=n),
            columns=["A", "B"],
        )
        rets.loc[rets.index[: n - MIN_RETURN_OBS + 1], "B"] = np.nan  # 19 returns
        eligible, excluded = select_eligible_returns(rets)
        assert "B" in excluded and list(eligible.columns) == ["A"]


class TestEquityShareDiagnosis:
    """Requirement 4: the 2026-10-30 -> 2026-11-02 equity share reproduces."""

    def test_equity_share_matches_the_diagnosis(self):
        """79% on 2026-10-30 -> ~9% on 2026-11-02, on clean closes.

        Closes are a frozen copy of the real universe (tests/fixtures), and the weights
        are the stored `positions.weight` for each date. The two sessions differ by one
        position: EEM is +20.0% of NAV on 2026-10-30 and -23.6% on 2026-11-02. That
        single flip is the whole story -- with the old zero-padded covariance the panel
        could not see it, because the window was ten months old and half its columns
        were flattened to zero returns.
        """
        close = _fixture_closes()

        oct30 = window_returns(close.loc[:"2026-10-30"], sessions=COV_WINDOW_SESSIONS)
        nov02 = window_returns(close.loc[:"2026-11-02"], sessions=COV_WINDOW_SESSIONS)

        assert (oct30.index[-1], nov02.index[-1]) == (
            pd.Timestamp("2026-10-30"),
            pd.Timestamp("2026-11-02"),
        )

        share_oct30 = _equity_share(_fixture_weights("2026-10-30"), oct30)
        share_nov02 = _equity_share(_fixture_weights("2026-11-02"), nov02)

        assert share_oct30 == pytest.approx(0.7919, abs=0.02), share_oct30
        assert share_nov02 == pytest.approx(0.0855, abs=0.02), share_nov02
        assert share_oct30 - share_nov02 > 0.60, (
            "the equity sleeve's share collapsed across the EEM flip and must stay "
            "visible on a 120-session window"
        )

    def test_the_flip_is_eem(self):
        """Oct 30 is long EEM, Nov 2 is short it; the equity sleeve is SPY/EFA/EEM."""
        oct30, nov02 = _fixture_weights("2026-10-30"), _fixture_weights("2026-11-02")
        assert oct30["EEM"] > 0.15 > nov02["EEM"]
        assert all(ASSET_CLASS[t] == "equity" for t in ("SPY", "EFA", "EEM"))


class TestWindowIsCurrent:
    def test_window_is_the_documented_length_and_ends_at_the_last_session(self):
        close = _fixture_closes()
        rets = window_returns(close, sessions=COV_WINDOW_SESSIONS)
        assert len(rets) == COV_WINDOW_SESSIONS == 120
        assert rets.index[-1] == close.index[-1]
        assert rets.index[0] == close.index[-COV_WINDOW_SESSIONS]

    def test_lookback_leaves_room_for_a_full_window(self):
        # ~172 calendar days for 120 sessions; the request must be comfortably longer.
        assert LIVE_LOOKBACK_DAYS >= COV_WINDOW_SESSIONS * 1.3


def _yf_frame(values: pd.Series) -> pd.DataFrame:
    """One ticker as ``signals.load.fetch`` returns it: an OHLCV frame."""
    return pd.DataFrame(
        {
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "adj_close": values,
            "volume": 1_000_000,
        },
        index=values.index,
    )


class TestRealFetchShape:
    """The shape the panel actually receives, not a tidy frozen matrix.

    ``signals.load.fetch`` returns one OHLCV frame per ticker, and
    ``close_matrix_from_frames` outer-joins them. The union index can therefore
    contain a date on which one ticker -- or, before the close, every ticker --
    has no price. That is the session that produced "119 of 120 returns" on all
    eight columns and excluded the whole book.
    """

    DATES = pd.bdate_range("2026-02-02", "2026-11-10")

    def _frames(
        self,
        *,
        phantom_today: bool = False,
        missing_today: tuple[str, ...] = (),
    ) -> dict[str, pd.DataFrame]:
        """Per-ticker frames; today's row is either NaN or absent for a ticker."""
        rng = np.random.default_rng(17)
        frames: dict[str, pd.DataFrame] = {}
        for ticker in UNIVERSE:
            closes = pd.Series(
                100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(self.DATES)))),
                index=self.DATES,
            )
            if phantom_today or ticker in missing_today:
                closes.iloc[-1] = np.nan
            frame = _yf_frame(closes)
            if missing_today and ticker in missing_today and not phantom_today:
                frame = frame.iloc[:-1]          # today's row has not arrived at all
            frames[ticker] = frame
        return frames

    def test_phantom_today_row_is_not_a_session(self):
        """The regression: today listed with no close yet used to exclude all 8.

        With the fetch shape below the panel reported every column as "119 of 120
        returns", because the last row of the 120-return window was today's
        not-yet-priced row rather than the last session that has a close.
        """
        close = live_prices.close_matrix_from_frames(self._frames(phantom_today=True))
        assert close.index[-1] == self.DATES[-1]
        assert close.iloc[-1].isna().all(), "the row the old window was reading"

        rets = window_returns(close, sessions=COV_WINDOW_SESSIONS)

        assert len(rets) == COV_WINDOW_SESSIONS == 120
        assert rets.index[-1] == self.DATES[-2]
        assert rets.index[0] == self.DATES[-1 - COV_WINDOW_SESSIONS]
        assert int(rets.notna().sum().min()) == COV_WINDOW_SESSIONS, "119 of 120 again"

        eligible, excluded = select_eligible_returns(rets)
        assert excluded == {}
        assert list(eligible.columns) == list(UNIVERSE)

    def test_one_tickers_missing_close_shortens_the_window_for_all(self, monkeypatch):
        """A close that has not arrived for one ticker must not exclude the book."""
        live_prices.load_live_closes.clear()
        frames = self._frames(missing_today=("EFA",))
        monkeypatch.setattr("signals.load.fetch", lambda tickers, start, end: frames)

        rets = live_prices.load_live_returns(tickers=tuple(UNIVERSE))

        assert len(rets) == COV_WINDOW_SESSIONS
        assert rets.index[-1] == self.DATES[-2], "window is anchored to today"
        assert not rets.isna().any().any()
        assert int(rets.notna().sum().min()) == COV_WINDOW_SESSIONS

    def test_the_whole_book_is_estimated_on_the_real_fetch_shape(self):
        """All 8 names enter the covariance; PCTR still sums to 1."""
        close = live_prices.close_matrix_from_frames(self._frames(phantom_today=True))
        rets = window_returns(close, sessions=COV_WINDOW_SESSIONS)
        weights = pd.Series(
            {t: (1.0 if i % 2 else -1.0) for i, t in enumerate(UNIVERSE)}
        )

        contrib, excluded = risk_contributions(weights, rets)

        assert excluded == {}
        assert set(contrib.index) == set(UNIVERSE)
        assert abs(float(contrib["pctr"].sum()) - 1.0) <= 1e-6

    def test_a_120_return_window_needs_121_closes(self):
        dates = pd.bdate_range("2026-01-02", periods=COV_WINDOW_SESSIONS)
        close = pd.DataFrame(
            {t: np.linspace(100.0, 110.0, len(dates)) for t in UNIVERSE},
            index=dates,
        )
        with pytest.raises(ValueError, match="121 closes needed"):
            window_returns(close, sessions=COV_WINDOW_SESSIONS)

    def test_121_closes_build_120_returns_without_a_nan_row(self):
        dates = pd.bdate_range("2026-01-02", periods=COV_WINDOW_SESSIONS + 1)
        close = pd.DataFrame(
            {t: np.linspace(100.0, 110.0, len(dates)) for t in UNIVERSE},
            index=dates,
        )
        rets = window_returns(close, sessions=COV_WINDOW_SESSIONS)
        assert len(rets) == 120
        assert rets.index[0] == dates[1]
        assert not rets.isna().any().any()


class TestPanelCopy:
    """Requirements 2 and 3 as the panel renders them."""

    def _render(self, tmp_path, monkeypatch, returns: pd.DataFrame):
        monkeypatch.setattr(live_prices, "load_live_returns", lambda **kw: returns)
        script = tmp_path / "panel.py"
        script.write_text(
            "import streamlit as st\n"
            "from dashboard.views import operational\n"
            "positions = [\n"
            "    {'trade_date': '2026-10-30', 'ticker': t, 'signed_notional': 25000.0 * w}\n"
            "    for t, w in [('SPY', 1.0), ('EFA', 1.0), ('EEM', 0.8), ('TLT', -1.0),\n"
            "                  ('IEF', -1.0), ('HYG', -1.0), ('LQD', -1.0), ('GLD', -0.8)]\n"
            "]\n"
            "operational._render_mctr_pctr(100_000.0, positions)\n"
        )
        at = AppTest.from_file(str(script), default_timeout=600)
        at.run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    def test_panel_names_the_window_and_the_excluded_ticker(self, tmp_path, monkeypatch):
        close = _fixture_closes().loc[:"2026-10-30"]
        rets = window_returns(close, sessions=COV_WINDOW_SESSIONS)
        # GLD stops trading inside the window -> no complete history.
        rets.iloc[-30:, rets.columns.get_loc("GLD")] = np.nan

        at = self._render(tmp_path, monkeypatch, rets)

        warnings = " ".join(w.value for w in at.warning)
        assert "Risk not available for GLD" in warnings
        assert "incomplete history" in warnings

        captions = " ".join(c.value for c in at.caption)
        start, end = rets.index[0].date(), rets.index[-1].date()
        assert f"{start} to {end}" in captions
        assert "120-session covariance" in captions
        assert "121 aligned closes" in captions

        # The excluded name is not silently priced at zero inside the numbers.
        assert "risk not available" not in " ".join(m.value for m in at.markdown).lower()

    def test_panel_does_not_draw_from_a_stale_snapshot(self, tmp_path, monkeypatch):
        def boom(**kw):
            raise RuntimeError("no network")

        monkeypatch.setattr(live_prices, "load_live_returns", boom)
        script = tmp_path / "panel2.py"
        script.write_text(
            "from dashboard.views import operational\n"
            "operational._render_mctr_pctr(100_000.0, [\n"
            "    {'trade_date': '2026-11-10', 'ticker': 'SPY', 'signed_notional': 26_000.0},\n"
            "    {'trade_date': '2026-11-10', 'ticker': 'TLT', 'signed_notional': -25_000.0},\n"
            "])\n"
        )
        at = AppTest.from_file(str(script), default_timeout=600)
        at.run()
        assert not at.exception, [str(e.value) for e in at.exception]

        info = " ".join(i.value for i in at.info)
        assert "Live closes are unavailable" in info
        assert "committed price snapshot" in info
