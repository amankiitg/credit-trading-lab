"""Signal history by ticker: price against its 120-session trend base, with the book's weight.

The last block on the Trade Approval page. One small chart per ticker answers a
question the rest of the page cannot: is the position in this name aligned with the
price trend the signal measures, and when did the book's weight move?

Each chart has two axes:

  - left  -- the daily close (solid, sleeve colour) and the close from
             ``TREND_LOOKBACK_SESSIONS`` trading sessions earlier (dashed, same
             colour). Where the two cross is where the 120-session trend flips.
  - right -- the book's stored position weight in that ticker (grey step line)
             with a zero line, so a short is visibly below zero.

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

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from signals.etf_universe import ASSET_CLASS, UNIVERSE

# The trend base the signal compares today's close against.
TREND_LOOKBACK_SESSIONS: int = 120

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
        return pd.DataFrame(columns=["trade_date", "ticker", "weight"])

    frame = pd.DataFrame(rows)
    if "weight" not in frame.columns:
        return pd.DataFrame(columns=["trade_date", "ticker", "weight"])

    frame = frame[["trade_date", "ticker", "weight"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
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


def weight_series(
    weights: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
) -> pd.Series | None:
    """The stored book weight in ``ticker``, one value per plotted session.

    - Before the first live session: 0.0, because there was no book.
    - On a stored date with no row for this ticker: 0.0, because the book held none.
    - On a session with no stored snapshot at all: the previous stored weight (the
      step holds), never an interpolated value.
    - A ticker with no stored row ever: ``None`` -- the chart says so.

    Returns None rather than a fabricated zero series.
    """
    if weights.empty or sessions.empty:
        return None

    ticker_rows = weights[weights["ticker"] == ticker]
    if ticker_rows.empty or ticker_rows["weight"].notna().sum() == 0:
        # No row at all, or only rows with a null weight: the weight is unknown,
        # not zero. The chart says so rather than drawing a flat line at 0.
        return None

    stored_dates = pd.DatetimeIndex(sorted(weights["trade_date"].dropna().unique()))
    # Every stored date has a value: the row's weight, or zero when this ticker
    # was not held. This is the distinction between "not held" and "no snapshot".
    by_date = (
        ticker_rows.dropna(subset=["weight"])
        .set_index("trade_date")["weight"]
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


def build_ticker_history(
    closes: pd.DataFrame,
    weights: pd.DataFrame,
    ticker: str,
    sessions: pd.DatetimeIndex,
    first_live_session: pd.Timestamp | None,
) -> dict:
    """Series for one chart: close, 120-session lookback close, weight, and gaps."""
    if ticker not in closes.columns:
        empty = pd.Series(index=sessions, dtype=float)
        return {
            "price": empty,
            "lookback": empty.copy(),
            "weight": None,
            "has_price": False,
            "has_lookback": False,
            "has_weight": False,
        }

    full = closes[ticker].astype(float)
    price = full.reindex(sessions)
    # shift() counts rows in the fetch's session index, which is trading days.
    lookback = full.shift(TREND_LOOKBACK_SESSIONS).reindex(sessions)
    weight = weight_series(weights, ticker, sessions, first_live_session)

    return {
        "price": price,
        "lookback": lookback,
        "weight": weight,
        "has_price": bool(price.notna().sum() >= 2),
        "has_lookback": bool(lookback.notna().any()),
        "has_weight": weight is not None,
    }


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


def ticker_figure(
    ticker: str,
    history: dict,
    sessions: pd.DatetimeIndex,
) -> go.Figure:
    """One small dual-axis chart. Fixed ranges, unified hover, no scroll zoom."""
    from dashboard.views.operational import NEUTRAL_COLOR

    colour = _sleeve_colour(ticker)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

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
                name="120-session lookback",
                line=dict(color=colour, width=1.5, dash="dash"),
                hovertemplate="lookback close %{y:,.2f}<extra></extra>",
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

    fig.update_layout(
        title=dict(text=f"{ticker} · {ASSET_CLASS.get(ticker, '')}", font=dict(size=13)),
        height=300,
        margin=dict(l=8, r=8, t=36, b=8),
        hovermode="x unified",
        showlegend=False,
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
        showgrid=True,
        gridcolor="rgba(0,0,0,0.06)",
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


def _legend_slot(container) -> None:
    """The ninth grid slot: what the three lines mean."""
    container.markdown(
        "**How to read these charts**\n\n"
        "- **Solid line** — the daily close, in the sleeve colour.\n"
        "- **Dashed line** — the close 120 trading sessions earlier, the trend base. "
        "Where it crosses the solid line is where the trend flips.\n"
        "- **Grey step line** — the book's position weight in that ticker, on the "
        "right axis. The zero line separates long from short.\n\n"
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
                _plot(ticker_figure(ticker, history, window), height=300, top=36)
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
            "position weight over the same window. Closes are the live source the risk "
            "panel reads; weights are the stored daily positions. Nothing is "
            "zero-filled: a ticker without history says so on its own chart."
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
