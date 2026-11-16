"""Signal history by ticker: price against its 120-session trend base, with the book's weight.

The last block on the Trade Approval page. One small chart per ticker answers a
question the rest of the page cannot: is the position in this name aligned with the
price trend the signal measures, and when did the book's weight move?

Each chart has two panels:

  - top   -- the daily close (solid, sleeve colour), the close from
             ``TREND_LOOKBACK_SESSIONS`` trading sessions earlier (dashed, same
             colour), and on the right axis the book's stored position weight
             (grey step line) with a zero line, so a short is visibly below zero.
  - below -- the trend score, ``close / close_L - 1``, with a zero line and the
             signal's dead zone shaded: +/- 0.5 x that ticker's own realised vol
             x sqrt(L / 252). A weight flip only happens once the score leaves
             the band, so the two panels can be read against the same sessions.

The window starts ``PRE_TRADING_SESSIONS`` sessions before the first live trading
session, so the lookback base is already meaningful on the day the book started.
Before that first session the weight is exactly 0 because the book held nothing,
and the caption says so. A ticker without price history gets an annotation on its
own chart rather than a zero-filled line: a padded close is not a measurement.

Closes come from ``dashboard.live_prices`` -- the same fetch the risk panel reads,
not the committed parquet snapshot. Weights come from the ``positions`` table, one
row per held ticker per stored run: a stored session with no row for a ticker means
the book held none, and a session with no stored snapshot holds the previous weight
(the line is a step, never interpolated).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from signals.etf_universe import ASSET_CLASS, UNIVERSE

# The trend base the signal compares today's close against.
TREND_LOOKBACK_SESSIONS: int = 120

# The trend signal's vol estimate: 63-session realised vol, annualised, and the
# pre-registered dead-zone multiple (signals/trend_signal.py). The band on the
# lower panel must use the same units as the signal it is explaining.
VOL_WINDOW_SESSIONS: int = 63
TRADING_DAYS: int = 252
DEAD_ZONE_MULTIPLE: float = 0.5

# The dashed line is a shifted close, not an average. Say so everywhere it is
# labelled: the distinction is the whole point of the chart.
TREND_BASE_LABEL: str = (
    "close 120 sessions ago (signal lookback base, NOT a moving average)"
)
# Same label with a line break, so a 230px-wide chart legend can show all of it
# instead of clipping the end. Hover and the section legend use the flat string.
TREND_BASE_LEGEND: str = (
    "close 120 sessions ago <br>(signal lookback base, NOT a moving average)"
)

# Sessions of history to show *before* the first live session. This is the same
# number as the trend lookback: it puts the lookback base on screen from the first
# day the book existed instead of starting the chart mid-trend.
PRE_TRADING_SESSIONS: int = 120

# Calendar days to request from the price source. The chart needs
# PRE_TRADING_SESSIONS + TREND_LOOKBACK_SESSIONS = 240 sessions before the first
# live session, about 345 calendar days, plus the live period itself. 760 leaves
# room for holidays and a long weekend at either end.
HISTORY_LOOKBACK_DAYS: int = 760

WEIGHT_TTL_SECS: int = 600

WEIGHT_COLOR: str = "#8a8a8a"

# The share-count line sits on the lower panel's right axis. It has to be legible
# against the sleeve-coloured score AND against the band, so it is a darker neutral
# than the weight line rather than a second grey of the same value.
SHARES_COLOR: str = "#4a4a4a"


def _sleeve_colour(ticker: str) -> str:
    """The dashboard's asset-class colour for this ticker."""
    from dashboard.views.operational import ASSET_CLASS_COLORS, NEUTRAL_COLOR

    return ASSET_CLASS_COLORS.get(ASSET_CLASS.get(ticker, ""), NEUTRAL_COLOR)


@st.cache_data(ttl=3600, show_spinner=False)
def load_history_closes() -> pd.DataFrame:
    """Adjusted closes for the whole universe, deep enough for the trend base.

    Same boundary as the risk panel (``signals.load.fetch`` through
    ``dashboard.live_prices``). Raises on failure; the caller says the history is
    unavailable instead of drawing the committed snapshot.
    """
    from dashboard.live_prices import load_live_closes

    return load_live_closes(
        tickers=tuple(UNIVERSE), lookback_days=HISTORY_LOOKBACK_DAYS
    )


@st.cache_data(ttl=WEIGHT_TTL_SECS, show_spinner=False)
def load_stored_weights() -> pd.DataFrame:
    """Every stored position row, as (trade_date, ticker, weight).

    ``fetch_positions(latest_only=False)`` is the existing read path; the table
    already keeps one snapshot per run. Missing values stay missing -- nothing is
    filled here.
    """
    from dashboard.supabase_client import fetch_positions

    rows = fetch_positions(latest_only=False)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "ticker", "weight", "shares"])

    frame = pd.DataFrame(rows)
    if "weight" not in frame.columns:
        return pd.DataFrame(columns=["trade_date", "ticker", "weight", "shares"])

    # shares is carried alongside the weight because the two answer different
    # questions about the same snapshot: the weight says how much of the book, the
    # share count says how many units the rebalance band actually moved. A table
    # without the column is a deployment older than the chart, not an error.
    columns = ["trade_date", "ticker", "weight"]
    if "shares" in frame.columns:
        columns.append("shares")
    frame = frame[columns].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    if "shares" in frame.columns:
        frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "ticker"])
    return frame.drop_duplicates(subset=["trade_date", "ticker"], keep="last")


def trading_sessions(closes: pd.DataFrame) -> pd.DatetimeIndex:
    """Dates on which at least one ticker has a close."""
    if closes.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(closes.index[closes.notna().any(axis=1)])


def history_window(
    closes: pd.DataFrame,
    first_live_session: pd.Timestamp | None,
) -> pd.DatetimeIndex:
    """Sessions from PRE_TRADING_SESSIONS before the first live session to the last.

    Without stored weights there is no "first live session"; the chart then shows
    the last PRE_TRADING_SESSIONS sessions of price and omits the weight line.
    """
    sessions = trading_sessions(closes)
    if len(sessions) == 0:
        return sessions

    if first_live_session is None:
        start_pos = max(0, len(sessions) - PRE_TRADING_SESSIONS)
        return sessions[start_pos:]

    anchor = pd.Timestamp(first_live_session).normalize()
    # First session on or after the first stored position. searchsorted can land
    # past the end if the stored date is newer than the price fetch; clamp it.
    pos = int(sessions.searchsorted(anchor, side="left"))
    pos = min(pos, len(sessions) - 1)
    start_pos = max(0, pos - PRE_TRADING_SESSIONS)
    return sessions[start_pos:]


def _position_series(
    positions: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
    column: str,
) -> pd.Series | None:
    """One stored position column in ``ticker``, one value per plotted session.

    - Before the first live session: 0.0, because there was no book.
    - On a stored date with no row for this ticker: 0.0, because the book held none.
    - On a session with no stored snapshot at all: the previous stored value (the
      step holds), never an interpolated value.
    - A ticker with no stored row ever: ``None`` -- the chart says so.

    Returns None rather than a fabricated zero series.

    Shared by the weight and the share count on purpose: the two lines are drawn
    against the same snapshots, and a session where one is carried forward but the
    other is not would put two different books on one chart.
    """
    if positions.empty or sessions.empty or column not in positions.columns:
        return None

    ticker_rows = positions[positions["ticker"] == ticker]
    if ticker_rows.empty or ticker_rows[column].notna().sum() == 0:
        # No row at all, or only rows with a null value: the value is unknown, not
        # zero. The chart says so rather than drawing a flat line at 0.
        return None

    stored_dates = pd.DatetimeIndex(sorted(positions["trade_date"].dropna().unique()))
    # Every stored date has a value: the row's weight, or zero when this ticker
    # was not held. This is the distinction between "not held" and "no snapshot".
    by_date = (
        ticker_rows.dropna(subset=[column])
        .set_index("trade_date")[column]
        .reindex(stored_dates)
        .fillna(0.0)
    )

    # ffill only carries a stored observation forward; it never backfills, so the
    # days before the first live session stay NaN until we set them to zero below.
    aligned = by_date.reindex(sessions, method="ffill")

    if first_live_session is not None:
        first_live = pd.Timestamp(first_live_session).normalize()
        aligned = aligned.copy()
        aligned[sessions < first_live] = 0.0

    if aligned.notna().sum() == 0:
        return None
    return aligned.astype(float)


def weight_series(
    weights: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
) -> pd.Series | None:
    """The stored book weight in ``ticker``, on the step rule above."""
    return _position_series(weights, ticker, sessions, first_live_session, "weight")


def shares_series(
    positions: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
) -> pd.Series | None:
    """The book's SHARE COUNT in ``ticker``, on the same step rule as the weight.

    Same snapshots, same carry-forward, same zero before the first live session:
    the two lines have to describe one book, or the panel would say the position
    changed when only the notional did.

    None when the table predates the column, which omits the axis rather than
    drawing a book that held nothing.
    """
    return _position_series(positions, ticker, sessions, first_live_session, "shares")


def build_ticker_history(
    closes: pd.DataFrame,
    weights: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
) -> dict:
    """Series for one chart: close, lookback close, trend score, band, weight."""
    if ticker not in closes.columns:
        empty = pd.Series(index=sessions, dtype=float)
        return {
            "price": empty,
            "lookback": empty.copy(),
            "trend_score": empty.copy(),
            "weight": None,
            "shares": None,
            "vol": None,
            "dead_zone": None,
            "has_price": False,
            "has_lookback": False,
            "has_trend_score": False,
            "has_weight": False,
            "has_shares": False,
        }

    full = closes[ticker].astype(float)
    price = full.reindex(sessions)
    # shift() counts rows in the fetch's session index, which is trading days.
    lookback = full.shift(TREND_LOOKBACK_SESSIONS).reindex(sessions)
    trend_score = price / lookback - 1.0
    weight = weight_series(weights, ticker, sessions, first_live_session)
    shares = shares_series(weights, ticker, sessions, first_live_session)
    vol = realised_vol(closes, ticker)
    band = dead_zone_half_width(vol)

    return {
        "price": price,
        "lookback": lookback,
        "trend_score": trend_score,
        "weight": weight,
        "shares": shares,
        "vol": vol,
        "dead_zone": band,
        "has_price": bool(price.notna().sum() >= 2),
        "has_lookback": bool(lookback.notna().any()),
        "has_trend_score": bool(trend_score.notna().sum() >= 2),
        "has_weight": weight is not None,
        "has_shares": shares is not None,
    }


def realised_vol(closes: pd.DataFrame, ticker: str) -> float | None:
    """The ticker's own annualised 63-session realised vol, at the last session.

    Same estimate the trend signal uses for position sizing and for the dead
    zone: std(log returns, window=63) * sqrt(252). This is a fixed reference
    for the chart's shaded band: one vol per ticker, not a cross-sectional
    average and not a rolling ribbon.
    """
    if ticker not in closes.columns:
        return None
    series = closes[ticker].astype(float).dropna()
    if len(series) < VOL_WINDOW_SESSIONS + 1:
        return None
    log_ret = np.log(series).diff()
    vol = log_ret.rolling(
        VOL_WINDOW_SESSIONS, min_periods=VOL_WINDOW_SESSIONS
    ).std().iloc[-1]
    if not np.isfinite(vol) or vol <= 0:
        return None
    return float(vol * np.sqrt(TRADING_DAYS))


def dead_zone_half_width(vol: float | None) -> float | None:
    """Half-width of the signal's dead zone: k * sigma * sqrt(L / 252)."""
    if vol is None or not np.isfinite(vol) or vol <= 0:
        return None
    return float(
        DEAD_ZONE_MULTIPLE * vol * np.sqrt(TREND_LOOKBACK_SESSIONS / TRADING_DAYS)
    )


def _price_range(price: pd.Series, lookback: pd.Series) -> list[float]:
    values = pd.concat([price, lookback]).dropna()
    if values.empty:
        return [0.0, 1.0]
    low = float(values.min())
    high = float(values.max())
    if high <= low:
        pad = max(abs(high) * 0.02, 0.01)
    else:
        pad = (high - low) * 0.04
    return [low - pad, high + pad]


def _weight_range(weight: pd.Series | None) -> list[float]:
    if weight is None or weight.dropna().empty:
        return [-0.5, 0.5]
    bound = max(0.25, float(weight.abs().max()) * 1.15)
    return [-bound, bound]


def _shares_range(shares: pd.Series | None) -> list[float]:
    """Y-range for the share-count axis, always including zero.

    Zero is in range on purpose: the line crossing it is a long-to-short flip, and
    it is the one feature of this axis a reader looks for.
    """
    if shares is None or shares.dropna().empty:
        return [0.0, 1.0]
    values = shares.dropna()
    low = min(0.0, float(values.min()))
    high = max(0.0, float(values.max()))
    if high <= low:
        return [low - 1.0, high + 1.0]
    pad = (high - low) * 0.08
    return [low - pad, high + pad]


def ticker_figure(
    ticker: str,
    history: dict,
    sessions: pd.DatetimeIndex,
) -> go.Figure:
    """One ticker: price and weight on top, trend score and dead zone below."""
    from dashboard.views.operational import NEUTRAL_COLOR

    colour = _sleeve_colour(ticker)
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.11,
        row_heights=[0.62, 0.38],
        # Both rows carry a secondary axis: the weight on top, the share count below.
        specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
    )

    band = history.get("dead_zone")
    if band is not None:
        # Shaded first so both lines stay on top of it. The band is a visual
        # scale for the score: crossing it, not merely crossing zero, is what
        # moves the book's weight under the signal's hysteresis rule.
        fig.add_shape(
            type="rect",
            xref="x2 domain",
            yref="y3",
            x0=0,
            x1=1,
            y0=-band,
            y1=band,
            fillcolor="rgba(138,138,138,0.16)",
            line_width=0,
            layer="below",
        )

    if history["has_price"]:
        fig.add_trace(
            go.Scatter(
                x=sessions,
                y=history["price"].values,
                mode="lines",
                name="close",
                line=dict(color=colour, width=2),
                hovertemplate="close %{y:,.2f}<extra></extra>",
            ),
            secondary_y=False,
        )
    if history["has_lookback"]:
        fig.add_trace(
            go.Scatter(
                x=sessions,
                y=history["lookback"].values,
                mode="lines",
                name=TREND_BASE_LEGEND,
                line=dict(color=colour, width=1.5, dash="dash"),
                hovertemplate=TREND_BASE_LABEL + " %{y:,.2f}<extra></extra>",
            ),
            secondary_y=False,
        )
    if history["has_weight"]:
        fig.add_trace(
            go.Scatter(
                x=sessions,
                y=history["weight"].values,
                mode="lines",
                name="position weight",
                line=dict(color=WEIGHT_COLOR, width=1.6, shape="hv"),
                hovertemplate="weight %{y:.1%}<extra></extra>",
            ),
            secondary_y=True,
        )
    if history["has_trend_score"]:
        # The dead zone is a property of the ticker, not a series, so it has no trace
        # of its own to hover. It rides along as customdata on the score line: the
        # reader then gets the band and the score in the same tooltip, at the session
        # they are actually looking at, instead of a caption they have to hold in
        # their head.
        band_values = (
            np.full(len(sessions), float(band)) if band is not None else np.zeros(len(sessions))
        )
        fig.add_trace(
            go.Scatter(
                x=sessions,
                y=history["trend_score"].values,
                mode="lines",
                name="trend score",
                line=dict(color=colour, width=1.8),
                customdata=band_values,
                hovertemplate=(
                    "trend score %{y:.2%}<br>"
                    "dead zone ±%{customdata:.2%}<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )
        if history.get("has_shares"):
            fig.add_trace(
                go.Scatter(
                    x=sessions,
                    y=history["shares"].values,
                    mode="lines",
                    name="shares held",
                    line=dict(color=SHARES_COLOR, width=1.4, shape="hv"),
                    hovertemplate="shares held %{y:,.2f}<extra></extra>",
                ),
                row=2,
                col=1,
                secondary_y=True,
            )

    fig.update_layout(
        title=dict(text=f"{ticker} · {ASSET_CLASS.get(ticker, '')}", font=dict(size=13)),
        height=460,
        margin=dict(l=8, r=8, t=36, b=8),
        hovermode="x unified",
        showlegend=True,
        bargap=0.1,
    )
    fig.update_xaxes(
        fixedrange=True,
        range=[sessions[0], sessions[-1]] if len(sessions) else None,
        showgrid=False,
    )
    fig.update_yaxes(
        title_text="close",
        secondary_y=False,
        fixedrange=True,
        range=_price_range(history["price"], history["lookback"]),
        automargin=True,
        showgrid=True,
        gridcolor="rgba(0,0,0,0.06)",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="weight",
        secondary_y=True,
        fixedrange=True,
        range=_weight_range(history["weight"]),
        tickformat=".0%",
        zeroline=True,
        zerolinecolor=NEUTRAL_COLOR,
        zerolinewidth=1,
        showgrid=False,
        automargin=True,
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="trend score",
        fixedrange=True,
        range=_trend_range(history["trend_score"], band),
        tickformat=".0%",
        zeroline=True,
        zerolinecolor=NEUTRAL_COLOR,
        zerolinewidth=1,
        showgrid=True,
        gridcolor="rgba(0,0,0,0.06)",
        automargin=True,
        row=2,
        col=1,
    )
    # Only drawn when the snapshot table has the column: an absent axis is better
    # than an axis for a book that never existed.
    if history.get("has_shares"):
        fig.update_yaxes(
            title_text="shares held",
            secondary_y=True,
            fixedrange=True,
            range=_shares_range(history["shares"]),
            tickformat=",.0f",
            zeroline=True,
            zerolinecolor=SHARES_COLOR,
            zerolinewidth=1,
            showgrid=False,
            automargin=True,
            row=2,
            col=1,
        )
    if band is not None:
        fig.add_annotation(
            text=f"dead zone ±{band:.1%}",
            xref="x2 domain",
            yref="y3 domain",
            x=0.99,
            y=1.06,
            xanchor="right",
            yanchor="bottom",
            showarrow=False,
            font=dict(size=9, color=NEUTRAL_COLOR),
        )

    notes: list[str] = []
    if not history["has_price"]:
        notes.append(f"No usable price history for {ticker} in this window — not zero-filled.")
    elif not history["has_lookback"]:
        notes.append(f"{TREND_LOOKBACK_SESSIONS}-session lookback unavailable for {ticker}.")
    if history["weight"] is None:
        notes.append(f"No stored position weight for {ticker}.")
    for i, note in enumerate(notes):
        fig.add_annotation(
            text=note,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5 if not history["has_price"] else 0.92 - 0.09 * i,
            showarrow=False,
            font=dict(size=10, color=NEUTRAL_COLOR),
        )

    return fig


def _trend_range(score: pd.Series, band: float | None) -> list[float]:
    """Y-range for the lower panel, always including zero and the dead zone."""
    values = score.dropna()
    lows = []
    highs = []
    if not values.empty:
        lows.append(float(values.min()))
        highs.append(float(values.max()))
    if band is not None:
        lows.append(-band)
        highs.append(band)
    if not lows:
        return [-0.1, 0.1]
    low = min(lows)
    high = max(highs)
    pad = (high - low) * 0.08 if high > low else max(abs(high) * 0.1, 0.01)
    return [low - pad, high + pad]


def _legend_slot(container) -> None:
    """The ninth grid slot: what the lines and the band mean."""
    container.markdown(
        "**How to read these charts**\n\n"
        "- **Solid line** — the daily close, in the sleeve colour.\n"
        "- **Dashed line** — close 120 sessions ago (signal lookback base, NOT a "
        "moving average). Where it crosses the solid line is where the trend flips.\n"
        "- **Grey step line** — the book's position weight in that ticker, on the "
        "right axis. The zero line separates long from short.\n\n"
        "- **Dark step line (lower panel)** — the share count held in that ticker, on "
        "the lower panel's right axis, read from the same stored snapshot as the "
        "weight.\n\n"
        "- **Trend score (lower panel)** — close / close 120 sessions ago − 1, "
        "on the same sessions. The shaded band is the dead zone, ±0.5 × that "
        "ticker's own 63-session realised vol × √(120/252); the signal only flips "
        "when the score leaves the band, so watch the band edges against the "
        "weight steps above.\n\n"
        "Colour is the asset class: equity blue, rates green, credit violet, "
        "commodity amber."
    )


def render_grid(closes: pd.DataFrame, weights: pd.DataFrame) -> None:
    """Eight ticker charts in a 3x3 grid; Streamlit stacks the columns on phones."""
    from dashboard.views.operational import _plot

    sessions = trading_sessions(closes)
    if len(sessions) == 0:
        st.warning("The price source returned no sessions, so there is nothing to chart.")
        return

    first_live = (
        pd.Timestamp(weights["trade_date"].min())
        if not weights.empty and weights["trade_date"].notna().any()
        else None
    )
    window = history_window(closes, first_live)
    if len(window) == 0:
        st.warning("No session falls inside the signal-history window.")
        return

    if first_live is not None:
        st.caption(
            f"From {window[0].date()} — 120 sessions before the first live session "
            f"({first_live.date()}) — to {window[-1].date()}. Position weight is 0 "
            "before that first session, when the book held nothing."
        )
    else:
        st.caption(
            "No stored position history, so weights are omitted. Price runs over the "
            f"last {PRE_TRADING_SESSIONS} sessions: {window[0].date()} to "
            f"{window[-1].date()}."
        )

    rows = [UNIVERSE[i:i + 3] for i in range(0, len(UNIVERSE), 3)]
    for row_index, tickers in enumerate(rows):
        columns = st.columns(3)
        for column, ticker in zip(columns, tickers):
            history = build_ticker_history(closes, weights, ticker, window, first_live)
            with column:
                _plot(
                    ticker_figure(ticker, history, window),
                    height=460,
                    top=36,
                    legend_below=True,
                    legend_font_size=8,
                )
        # The ninth cell shares the last row so the grid stays 3x3.
        if row_index == len(rows) - 1:
            for column in columns[len(tickers):]:
                with column:
                    _legend_slot(column)


def render_signal_history() -> None:
    """The collapsed section at the end of the page."""
    with st.expander("Signal history by ticker", expanded=False):
        st.caption(
            "Each ticker's close against its 120-session trend base, with the book's "
            "position weight over the same window. The lower panel is the trend score "
            "with the signal's dead zone shaded at each ticker's own 63-session "
            "realised vol. Closes are the live source the risk panel reads; weights "
            "are the stored daily positions. Nothing is zero-filled: a ticker without "
            "history says so on its own chart."
        )
        st.caption(
            "Share count changes for two reasons: a signal flip (score leaves the grey "
            "band) or a rebalance-band trade (NAV or vol drift beyond 20%); inside the "
            "grey band only the second can happen."
        )

        try:
            closes = load_history_closes()
        except Exception as exc:
            st.warning(
                "Signal history is unavailable: the live price source could not be "
                f"read ({type(exc).__name__}: {exc}). It is not drawn from the "
                "committed price snapshot."
            )
            return

        weights = load_stored_weights()
        if weights.empty:
            st.warning(
                "No stored position rows were returned, so the weight line is "
                "omitted rather than drawn at zero. The price charts below are "
                "still live closes."
            )

        render_grid(closes, weights)
