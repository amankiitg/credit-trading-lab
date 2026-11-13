"""Operational panels for the trade-approval dashboard -- sprint v10.0.

One page, six blocks. Every block opens with the sentence that says what it tells
you and what the number should look like when nothing is wrong, because a bare
number with no reference point cannot be read.

  Header            Title, NAV source, and the six numbers an operator opens the page for.
  Trade Approval    Proposed next trade, stop-state advisory, and approve/reject.
  Exposure          Net and gross for the live book against the stored target.
  Performance       MTD return, volatility, Sharpe, max drawdown, account value, cost.
  Book P&L by sleeve  Where the account's result came from, carry versus price.
  Risk contribution  Share of book risk by sleeve and by name.
  Today's result    The run's dollars, kept apart.

Visual system:

  - Long and short are the only two colours used for signed quantities:
    LONG_COLOR for a long position, SHORT_COLOR for a short. Nothing else is
    coloured by direction.
  - Green means good or on target; red means a flag or a breach, and nothing else.
    Neither ever encodes position sign.
  - Neutral grey is for series that carry no direction: the NAV line and turnover
    cost.

No live Alpaca calls are made from this module (U6 gate from the v8.5 PRD).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.supabase_client import (
    fetch_daily_sleeve_pnl,
    fetch_decision_for_date,
    fetch_last_cron_run,
    fetch_live_attribution,
    fetch_pnl_log,
    fetch_positions,
    fetch_stop_states,
    get_auto_approve,
    get_setting,
    set_auto_approve,
    write_decision,
)

# ---------------------------------------------------------------- visual system
#
# Two colours, one meaning each: long is blue, short is orange. Sleeve identity
# stays in the label and the hover, never in a third and fourth colour.
LONG_COLOR = "#2f6db3"
SHORT_COLOR = "#d9822b"
NEUTRAL_COLOR = "#8a8a8a"

# Kept for older callers that still import the shared palette; charts below use
# the two-colour long/short scheme instead.
SLEEVE_COLORS: dict[str, str] = {
    "equity":    "#4c78a8",
    "rates":     "#f2a93b",
    "credit":    "#8e6cae",
    "commodity": "#5fa8a0",
}
SLEEVE_ORDER: tuple[str, ...] = ("equity", "rates", "credit", "commodity")

# One long/short treatment everywhere a signed position is drawn.
SHORT_HATCH = "//"
LONG_SHORT_NOTE = "blue = long, orange = short"

# Reference points, from the spec. Display thresholds only, not enforced limits:
# the execution layer's own limits are G_MAX_DEFAULT (gross) and the delta band.
NET_BAND_PP = 5.0         # net more than this far from its target is off target
GROSS_FLAG_X = 2.05       # above this is a mark-to-market overshoot between runs
GROSS_FLOOR_X = 1.80      # the floor observed across the recorded snapshots
RISK_TICKER_FLAG_PCT = 40.0   # one position above 40% of book risk is concentration
RISK_SLEEVE_FLAG_PCT = 60.0   # one sleeve above 60% of book risk is concentration

PLOTLY_CONFIG = {
    "displayModeBar": True,
    "scrollZoom": True,
    "displaylogo": False,
}

# ---------------------------------------------------------------- decision window
EXECUTION_JOB = "run_execution"
SIGNAL_JOB = "run_signal"

# Context only, never a gate.
EXECUTION_CRON_ET = "10:30 ET"

DECISION_STATE_NO_SIGNAL = "no_signal"
DECISION_STATE_OPEN = "open"
DECISION_STATE_LOCKED = "locked"


def _today_utc() -> str:
    """Today's date in UTC as YYYY-MM-DD.

    The execution cron stamps its cron_runs row with its own container's date, which
    is UTC on Render. Comparing against that row means using the same basis, so the
    dashboard uses UTC too: on a box set to US Eastern the local date rolls over four
    or five hours before UTC does, and in those hours the local date is not the date
    the run was written under.
    """
    return datetime.now(timezone.utc).date().isoformat()


def _fmt_utc(value) -> str:
    """A timestamp from cron_runs or pnl_log, in UTC, never a bare date."""
    if value is None or value == "":
        return "time not recorded"
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _rgba(hex_colour: str, alpha: float) -> str:
    """Plotly fill colour from a hex string and an alpha."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _side_colour(signed_value: float) -> str:
    """The one long/short encoding: blue for long, orange for short."""
    return LONG_COLOR if float(signed_value) >= 0 else SHORT_COLOR


def _side_word(signed_value: float) -> str:
    return "long" if float(signed_value) >= 0 else "short"


def decision_window(
    *,
    today: str,
    as_of_date: str | None,
    has_proposal: bool,
    last_execution_run_date: str | None,
) -> tuple[str, str]:
    """Which decision state the panel is in, and the reason in plain words.

      no_signal -- no proposal is on file, or the one on file has already been acted
                   on by an execution run and no newer signal has arrived.
      open      -- a proposal is on file and no run has acted on it yet. This is the
                   only state that shows the buttons.
      locked    -- an execution run has already completed today, so today's decision
                   is settled, whatever it was.

    The "already acted on" test is run > as_of. A proposal computed from the close of
    day A is acted on by the first run after it, which has a run_date later than A.
    """
    if not has_proposal or not as_of_date or as_of_date == "—":
        return DECISION_STATE_NO_SIGNAL, "no proposal is on file yet"

    if last_execution_run_date == today:
        return (
            DECISION_STATE_LOCKED,
            f"the execution run for {today} has already been recorded",
        )

    if last_execution_run_date and last_execution_run_date > as_of_date:
        return (
            DECISION_STATE_NO_SIGNAL,
            f"the {as_of_date} signal was already acted on by the "
            f"{last_execution_run_date} run, and no newer signal has arrived",
        )

    return (
        DECISION_STATE_OPEN,
        f"the {as_of_date} signal has not been acted on yet",
    )


def _render_execution_outcome(
    *,
    today: str,
    as_of_date: str,
    decision: str | None,
    attr_rows: list[dict],
    pnl_row: dict | None,
) -> None:
    """What today's execution run did, for the locked state."""
    if decision == "reject":
        st.error(f"Skipped, you rejected: nothing traded on the {as_of_date} signal.")
        return

    legs = [r for r in attr_rows if str(r.get("run_date")) == today]
    if legs:
        tickers = ", ".join(sorted({str(r.get("ticker")) for r in legs}))
        mark = sum(float(r.get("net_pnl") or 0.0) for r in legs)
        st.success(
            f"Traded: {len(legs)} leg(s) filled today on the {as_of_date} signal "
            f"({tickers}). Fill-day mark " + f"${mark:+,.2f}."
        )
        return

    traded_notional = float(pnl_row.get("gross_pnl") or 0.0) if pnl_row else 0.0
    if traded_notional > 0:
        cost = float(pnl_row.get("turnover_cost") or 0.0) if pnl_row else 0.0
        st.success(
            "Traded: " + f"${traded_notional:,.2f}" + " of notional today, turnover cost "
            + f"${cost:,.2f}" + ". Leg-level detail was not recorded for this run."
        )
        return

    st.success(
        "Ran with no fills: the execution run completed today and filled nothing. "
        "The daily email has the reason."
    )


def _sleeve_of(ticker: str) -> str:
    """The sleeve a ticker belongs to, per the universe's own asset classes."""
    from signals.etf_universe import ASSET_CLASS

    return ASSET_CLASS.get(ticker, "unknown")


def _fill_for(signed: float | None) -> str | None:
    if signed is None:
        return None
    return SHORT_HATCH if float(signed) < 0 else None


def net_flag(net_pct: float, target_net_pct: float) -> str | None:
    """The net leg's flag sentence, or None when it sits inside its band.

    The band is around the signal's target, not around zero. This is a per-asset
    trend book: each name takes its own long or short call, so net is whatever those
    eight calls add up to and drifts as prices move. Measured in percentage points
    of NAV.
    """
    gap_pp = net_pct - target_net_pct
    if abs(gap_pp) <= NET_BAND_PP:
        return None
    return (
        f"net is {net_pct:+.1f}% of NAV against a target of {target_net_pct:+.1f}%, "
        f"{gap_pp:+.1f}pp outside the +/-{NET_BAND_PP:.0f}pp band"
    )


def gross_flag(gross_x: float) -> str | None:
    """The gross leg's flag sentence, or None."""
    if gross_x > GROSS_FLAG_X:
        return f"gross is {gross_x:.2f}x, above the {GROSS_FLAG_X:.2f}x overshoot mark"
    if gross_x < GROSS_FLOOR_X:
        return f"gross is {gross_x:.2f}x, below the {GROSS_FLOOR_X:.2f}x floor"
    return None


def _fmt_dollars(value: float | None) -> str:
    """A dollar amount, or an explicit dash when the value is not available."""
    if value is None:
        return "—"
    return f"${float(value):+,.2f}"


def _sleeve_exposure(
    positions_data: list[dict],
    proposed_rows: list[dict],
    nav: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Current and target net exposure per sleeve, in dollars."""
    current: dict[str, float] = {s: 0.0 for s in SLEEVE_ORDER}
    for p in positions_data:
        sleeve = _sleeve_of(str(p.get("ticker", "")))
        if sleeve in current:
            current[sleeve] += float(p.get("signed_notional") or 0.0)

    target: dict[str, float] = {s: 0.0 for s in SLEEVE_ORDER}
    for row in proposed_rows:
        sleeve = _sleeve_of(str(row.get("ticker", "")))
        if sleeve in target:
            target[sleeve] += float(row.get("target wt") or 0.0) * nav

    return current, target


# ---------------------------------------------------------------- readers

@st.cache_data(ttl=300)
def _get_stop_states() -> list[dict]:
    """Fetch the stop-ladder state cache from Supabase (advisory mode only)."""
    return fetch_stop_states()


@st.cache_data(ttl=300)
def _get_positions() -> list[dict]:
    """Position snapshot for the exposure block and the risk panels."""
    return fetch_positions(latest_only=True)


@st.cache_data(ttl=300)
def _get_pnl_log() -> list[dict]:
    """The run rows behind every NAV and P&L figure on the page."""
    return fetch_pnl_log(limit=500)


@st.cache_data(ttl=300)
def _get_live_attribution(limit: int) -> list[dict]:
    """Live attribution rows, newest first. The limit is part of the cache key."""
    return fetch_live_attribution(limit=limit)


@st.cache_data(ttl=300)
def _get_daily_sleeve_pnl() -> list[dict]:
    """Sleeve P&L rows, four per session, newest first."""
    return fetch_daily_sleeve_pnl()


@st.cache_data(ttl=60)
def _get_last_execution_run() -> dict | None:
    """The newest run_execution row in cron_runs, or None."""
    return fetch_last_cron_run(EXECUTION_JOB)


@st.cache_data(ttl=60)
def _get_last_signal_run() -> dict | None:
    """The newest run_signal row in cron_runs, or None.

    Used only to label where the stored NAV came from: the two crons both write
    settings.live_nav, and the later write is the one on screen.
    """
    return fetch_last_cron_run(SIGNAL_JOB)


@st.cache_data(ttl=300)
def _get_drift_alert() -> dict | None:
    """Position drift flagged by run_execution.py's check_position_drift."""
    import json

    raw = get_setting("position_drift_alert")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@st.cache_data(ttl=300)
def _get_proposed_trade() -> tuple[list[dict], str, float]:
    """Return delta orders using signal weights stored by run_signal.py.

    Uses frozen Supabase positions and NAV -- the same values the execution cron
    will use -- so what you approve is exactly what executes.
    Returns (rows, as_of_date, nav).
    """
    import json
    from signals.etf_universe import UNIVERSE

    as_of_date     = get_setting("signal_as_of_date") or "—"
    weights_json   = get_setting("signal_target_weights")
    target_weights: dict[str, float] = json.loads(weights_json) if weights_json else {}

    nav_str = get_setting("live_nav")
    nav = float(nav_str) if nav_str else 100_000.0

    pos_rows = fetch_positions(latest_only=True)
    current_notionals: dict[str, float] = {
        r["ticker"]: float(r["signed_notional"]) for r in pos_rows
    }

    rows = []
    for ticker in UNIVERSE:
        w = float(target_weights.get(ticker) or 0.0)
        if w != w:
            w = 0.0
        target_notional  = w * nav
        current_notional = current_notionals.get(ticker, 0.0)
        delta            = target_notional - current_notional
        current_w        = current_notional / nav if nav > 0 else 0.0

        if abs(delta) < 250:
            action = "skip — within band"
        elif delta > 0:
            action = "buy"
        else:
            action = "sell / short"

        rows.append({
            "ticker":      ticker,
            "current ($)": round(current_notional, 0),
            "current wt":  round(current_w, 4),
            "target wt":   round(w, 4),
            "delta ($)":   round(delta, 0),
            "action":      action,
        })
    return rows, as_of_date, nav


def _nav_snapshot(nav: float, df_pnl: pd.DataFrame) -> dict:
    """Where the NAV on the page came from, and when it was written.

    Both crons refresh settings.live_nav: the morning execution run after it trades,
    and the evening signal run after the close. The later completion in cron_runs is
    the write that produced the stored value, so that is the source and timestamp
    shown next to every NAV. The morning execution NAV is read separately from the
    last pnl_log row, because that is the snapshot the day's P&L is measured from.
    """
    candidates: list[tuple[str, str]] = []
    for source, run in (
        ("morning execution", _get_last_execution_run()),
        ("evening signal", _get_last_signal_run()),
    ):
        if run and run.get("completed_at"):
            candidates.append((str(run["completed_at"]), source))

    if candidates:
        timestamp, source = max(candidates)
    else:
        timestamp, source = None, "stored NAV"

    morning_nav = None
    morning_timestamp = None
    if not df_pnl.empty:
        last = df_pnl.iloc[-1]
        if "live_nav" in df_pnl.columns and pd.notna(last.get("live_nav")):
            morning_nav = float(last["live_nav"])
            morning_timestamp = last.get("created_at")

    return {
        "value": float(nav),
        "source": source,
        "timestamp": timestamp,
        "morning_nav": morning_nav,
        "morning_timestamp": morning_timestamp,
    }


def _nav_basis_line(nav_snapshot: dict) -> str:
    """The same sentence wherever a NAV is shown: value, source, timestamp."""
    return (
        f"NAV ${nav_snapshot['value']:,.2f} — {nav_snapshot['source']}, "
        f"{_fmt_utc(nav_snapshot['timestamp'])}"
    )


def _risk_snapshot(nav: float, positions_data: list[dict]) -> dict | None:
    """Live covariance and risk contributions, or a reason they are unavailable.

    Weights are the live book; the covariance is built from current closes fetched
    through the signal's own yfinance boundary. The committed price snapshot is
    never read. A ticker without a complete return history is excluded and named.
    """
    if not positions_data:
        return None

    from dashboard.live_prices import load_live_returns
    from risk.live_risk import COV_WINDOW_SESSIONS, risk_contributions
    from signals.etf_universe import UNIVERSE

    w_dict: dict[str, float] = {}
    for pos in positions_data:
        ticker = pos["ticker"]
        notional = float(pos.get("signed_notional", 0))
        w_dict[ticker] = notional / nav if nav > 0 else 0.0

    live_tickers = [t for t in UNIVERSE if t in w_dict]
    if not live_tickers:
        return {"error": "No positions in the universe."}

    weights = pd.Series({t: w_dict[t] for t in live_tickers})

    try:
        rets = load_live_returns(tickers=tuple(live_tickers))
    except Exception as exc:
        return {
            "error": (
                "Live closes are unavailable right now, so the risk view is skipped; "
                "it is not drawn from the committed price snapshot. "
                f"({type(exc).__name__}: {exc})"
            )
        }

    if len(rets) < 2:
        return {"error": "Fewer than two sessions of live returns -- cannot estimate a covariance."}

    try:
        risk_df, excluded = risk_contributions(weights, rets)
    except ValueError as exc:
        return {"error": f"Risk contribution unavailable: {exc}"}
    except Exception as exc:
        return {"error": f"Risk decomposition failed: {exc}"}

    port_vol = float(risk_df["ctr"].sum())
    if port_vol <= 0:
        return {"error": "Portfolio volatility is zero at these weights -- nothing to attribute."}

    risk_df = risk_df.copy()
    risk_df["sleeve"] = [_sleeve_of(t) for t in risk_df.index]

    sleeve_tbl = risk_df.groupby("sleeve")[["weight", "ctr"]].sum()
    sleeve_tbl["pctr"] = sleeve_tbl["ctr"] / port_vol
    sleeve_tbl = sleeve_tbl.reindex([s for s in SLEEVE_ORDER if s in sleeve_tbl.index])
    if sleeve_tbl.empty:
        return {"error": "No sleeve could be attributed -- risk view skipped."}

    return {
        "risk_df": risk_df,
        "sleeve_tbl": sleeve_tbl,
        "excluded": excluded,
        "port_vol": port_vol,
        "window_start": rets.index[0].date(),
        "window_end": rets.index[-1].date(),
        "window_sessions": COV_WINDOW_SESSIONS,
        "book_date": str(positions_data[0].get("trade_date", "—")),
    }


def _concentration_messages(risk: dict | None) -> list[str]:
    """Names and sleeves above the display thresholds, for the top warning card."""
    if not risk or "error" in risk:
        return []
    risk_df = risk["risk_df"]
    sleeve_tbl = risk["sleeve_tbl"]
    messages: list[str] = []

    ticker_share = float(risk_df["pctr"].max())
    if ticker_share > RISK_TICKER_FLAG_PCT / 100:
        messages.append(
            f"{risk_df['pctr'].idxmax()} carries {ticker_share * 100:.0f}% of the "
            f"book's risk (over {RISK_TICKER_FLAG_PCT:.0f}%)"
        )

    sleeve_share = float(sleeve_tbl["pctr"].max())
    if sleeve_share > RISK_SLEEVE_FLAG_PCT / 100:
        messages.append(
            f"the {sleeve_tbl['pctr'].idxmax()} sleeve carries "
            f"{sleeve_share * 100:.0f}% of it (over {RISK_SLEEVE_FLAG_PCT:.0f}%)"
        )
    return messages


def _performance_stats(df_pnl: pd.DataFrame) -> dict:
    """MTD return, annualized vol, Sharpe, and max drawdown from stored NAVs."""
    empty = {
        "mtd_pct": None, "ann_vol_pct": None, "sharpe": None,
        "max_dd_pct": None, "n_navs": 0, "first_date": None, "last_date": None,
        "last_nav": None, "last_timestamp": None,
    }
    if df_pnl.empty or "live_nav" not in df_pnl.columns:
        return empty

    navs = df_pnl[df_pnl["live_nav"].notna()].copy()
    if navs.empty:
        return empty
    navs["trade_date"] = pd.to_datetime(navs["trade_date"])
    navs = navs.sort_values("trade_date").set_index("trade_date")
    values = navs["live_nav"].astype(float)

    returns = values.pct_change().dropna()
    mtd_pct = ann_vol_pct = sharpe = None
    if len(values) >= 2:
        month_start = values.index[-1].replace(day=1)
        before_month = values[values.index < month_start]
        baseline = float(before_month.iloc[-1]) if len(before_month) else float(values.iloc[0])
        if baseline:
            mtd_pct = (float(values.iloc[-1]) / baseline - 1.0) * 100
    if len(returns) >= 2 and float(returns.std(ddof=1)) > 0:
        ann_vol_pct = float(returns.std(ddof=1)) * (252 ** 0.5) * 100
        sharpe = float(returns.mean()) / float(returns.std(ddof=1)) * (252 ** 0.5)

    max_dd_pct = None
    if len(values) >= 2:
        drawdown = values / values.cummax() - 1.0
        max_dd_pct = float(drawdown.min()) * 100

    last = navs.iloc[-1]
    return {
        "mtd_pct": mtd_pct,
        "ann_vol_pct": ann_vol_pct,
        "sharpe": sharpe,
        "max_dd_pct": max_dd_pct,
        "n_navs": int(len(values)),
        "first_date": values.index[0].date().isoformat(),
        "last_date": values.index[-1].date().isoformat(),
        "last_nav": float(values.iloc[-1]),
        "last_timestamp": last.get("created_at"),
    }


# ---------------------------------------------------------------- rendering

def _section(title: str) -> None:
    """One divider and one heading. Called once per section, so never doubled."""
    st.markdown("---")
    st.markdown(f"### {title}")


def _plot(fig: go.Figure, *, height: int = 320) -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


def _render_header(
    *,
    proposed_rows: list[dict],
    as_of_date: str,
    nav: float,
    positions_data: list[dict],
    df_pnl: pd.DataFrame,
    risk: dict | None,
    drift: dict | None,
    stop_states: list[dict],
    nav_snapshot: dict,
    net_message: str | None,
    gross_message: str | None,
) -> None:
    """Title and the six numbers, each with the run it came from."""
    st.title("Credit Lab | Multi-Asset Trend")
    st.caption(
        f"Signal as of {as_of_date} · positions snapshot "
        f"{str(positions_data[0].get('trade_date', '—')) if positions_data else '—'} · "
        f"built from stored runs, not live broker calls."
    )

    notionals = [float(p.get("signed_notional") or 0.0) for p in positions_data]
    net_usd = sum(notionals)
    gross_usd = sum(abs(n) for n in notionals)
    net_pct = net_usd / nav * 100 if nav else 0.0
    gross_x = gross_usd / nav if nav else 0.0

    last_run = df_pnl.iloc[-1] if not df_pnl.empty else None
    book_pnl = last_run.get("book_pnl") if last_run is not None else None
    pnl_timestamp = last_run.get("created_at") if last_run is not None else None
    if book_pnl is None or pd.isna(book_pnl):
        pnl_value, pnl_delta = "—", "not recorded yet"
    else:
        pnl_pct = (float(book_pnl) / float(last_run.get("live_nav")) * 100
                   if last_run is not None and pd.notna(last_run.get("live_nav")) else None)
        pnl_value = "$" + f"{float(book_pnl):+,.2f}"
        pnl_delta = (
            f"{pnl_pct:+.2f}% of NAV · morning execution {_fmt_utc(pnl_timestamp)}"
            if pnl_pct is not None
            else f"morning execution {_fmt_utc(pnl_timestamp)}"
        )

    book_vol = risk.get("port_vol") if risk and "error" not in risk else None
    book_vol_value = f"{book_vol * 100:.1f}%" if book_vol is not None else "—"

    concentration = _concentration_messages(risk)
    flags: list[str] = list(concentration)
    if net_message:
        flags.append("net off target")
    if gross_message:
        flags.append("gross off target")
    if drift:
        flags.append("position drift")
    non_normal = [
        r for r in stop_states
        if r.get("state", "NORMAL") not in ("NORMAL", "None", None, "")
    ]
    if non_normal:
        flags.append(
            f"{len(non_normal)} stop state(s): "
            + ", ".join(str(r.get("ticker")) for r in non_normal)
        )

    if concentration:
        st.warning("**Concentration risk** — " + "; ".join(concentration) + ".")

    columns = st.columns(6)
    columns[0].metric(
        "NAV", "$" + f"{nav_snapshot['value']:,.2f}",
        delta=f"{nav_snapshot['source']} · {_fmt_utc(nav_snapshot['timestamp'])}",
        delta_color="off",
    )
    columns[1].metric("1-day P&L", pnl_value, delta=pnl_delta, delta_color="off")
    columns[2].metric(
        "Gross", f"{gross_x:.2f}x NAV",
        delta=f"target {sum(abs(float(r.get('target wt') or 0.0)) for r in proposed_rows):.2f}x",
        delta_color="off",
    )
    columns[3].metric(
        "Net", f"{net_pct:+.1f}%",
        delta="$" + f"{net_usd:+,.0f} · target "
              f"{sum(float(r.get('target wt') or 0.0) for r in proposed_rows) * 100:+.1f}%",
        delta_color="off",
    )
    columns[4].metric(
        "Book vol", book_vol_value,
        delta="annualized, 120-session covariance",
        delta_color="off",
    )
    columns[5].metric(
        "Active risk flags", str(len(flags)),
        delta="; ".join(flags) if flags else "all clear",
        delta_color="off",
        help="Concentration, exposure, position drift and stop states.",
    )

    st.caption(
        f"NAV source: {nav_snapshot['source']}, {_fmt_utc(nav_snapshot['timestamp'])}. "
        + (
            f"Morning execution NAV at {_fmt_utc(nav_snapshot['morning_timestamp'])}: "
            + f"${nav_snapshot['morning_nav']:,.2f}."
            if nav_snapshot.get("morning_nav") is not None
            else "No morning execution NAV is recorded."
        )
    )


def _render_mctr_pctr(nav: float, positions_data: list[dict]) -> None:
    """Risk contribution by sleeve and by name, with live hover and zoom."""
    if not positions_data:
        st.info("No positions -- risk contribution needs an open book.")
        return

    risk = _risk_snapshot(nav, positions_data)
    if risk is None:
        st.info("No positions -- risk contribution needs an open book.")
        return
    if "error" in risk:
        st.info(risk["error"])
        return

    for ticker in sorted(risk["excluded"]):
        st.warning(f"Risk not available for {ticker}: {risk['excluded'][ticker]}.")

    risk_df = risk["risk_df"]
    sleeve_tbl = risk["sleeve_tbl"]
    port_vol = risk["port_vol"]

    ranked = sleeve_tbl["pctr"].sort_values(ascending=False)
    driver = str(ranked.index[0])
    offsets = [s for s in sleeve_tbl.index if float(sleeve_tbl.loc[s, "pctr"]) < 0]
    line = (
        f"**Share of the book's total volatility (annualized {port_vol * 100:.1f}%).** "
        f"{driver.capitalize()} carries {float(ranked.iloc[0]) * 100:.0f}% of the book's risk"
    )
    if offsets:
        parts = " and ".join(
            f"{s} ({float(sleeve_tbl.loc[s, 'pctr']) * 100:.0f}%)" for s in offsets
        )
        line += f", with the {parts} sleeves offsetting it (they are the short side)"
    st.markdown(line + ".")
    st.caption(
        f"{risk['window_sessions']}-session covariance, **{risk['window_start']} to "
        f"{risk['window_end']}** of live closes ({risk['window_sessions'] + 1} aligned "
        f"closes), ending at the latest session every ticker has a close for. "
        f"Weights are the book at {risk['book_date']}. "
        f"Sleeves cover the {len(risk_df)} position(s) with a complete return history."
    )

    sleeve_y = [float(sleeve_tbl.loc[s, "pctr"]) * 100 for s in sleeve_tbl.index]
    sleeve_colors = [_side_colour(float(sleeve_tbl.loc[s, "weight"])) for s in sleeve_tbl.index]
    fig = go.Figure(
        go.Bar(
            x=[s.capitalize() for s in sleeve_tbl.index],
            y=sleeve_y,
            marker_color=sleeve_colors,
            text=[f"{v:.0f}%" for v in sleeve_y],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:.2f}% of book risk<extra></extra>",
        )
    )
    fig.update_layout(
        title="Share of book risk by sleeve",
        yaxis_title="% of total risk",
        showlegend=False,
    )
    _plot(fig, height=340)
    st.caption(
        "Sleeve shares sum to 100%; a negative bar is a sleeve that reduces the "
        f"book's risk. {LONG_SHORT_NOTE}."
    )

    with st.expander("Per-ticker detail (weight, marginal risk, contribution)"):
        by_ticker = risk_df.sort_values("pctr", ascending=False)
        ticker_y = [float(v) * 100 for v in by_ticker["pctr"]]
        ticker_colors = [_side_colour(float(w)) for w in by_ticker["weight"]]
        fig = go.Figure(
            go.Bar(
                x=list(by_ticker.index),
                y=ticker_y,
                marker_color=ticker_colors,
                hovertemplate=(
                    "%{x}<br>%{y:.2f}% of book risk<br>"
                    "weight %{customdata[0]:+.1%}<extra></extra>"
                ),
                customdata=[[float(w)] for w in by_ticker["weight"]],
            )
        )
        fig.update_layout(
            title="Share of book risk by ticker",
            yaxis_title="% of total risk",
            showlegend=False,
        )
        _plot(fig, height=340)
        st.dataframe(
            risk_df[["sleeve", "weight", "mctr", "ctr", "pctr"]].round(4),
            width="stretch",
        )
        st.caption(
            f"Shares sum to {risk_df['pctr'].sum():.4f}. Contribution is annualized "
            f"risk; the share is its fraction of the book's total. {LONG_SHORT_NOTE}."
        )


def render(
    user_email: str,
    is_authenticated: bool = False,
    secrets_configured: bool = False,
) -> None:
    """Render the whole operational page. Auth is only required for approve/reject."""

    with st.spinner("Loading today's stored signal and current positions..."):
        proposed_rows, as_of_date, nav = _get_proposed_trade()

    positions_data = _get_positions()
    pnl_rows = _get_pnl_log()
    df_pnl = pd.DataFrame(pnl_rows).sort_values("trade_date") if pnl_rows else pd.DataFrame()
    stop_states_raw = _get_stop_states()
    drift = _get_drift_alert()
    nav_snapshot = _nav_snapshot(nav, df_pnl)
    risk = _risk_snapshot(nav, positions_data)

    notionals = [float(p.get("signed_notional") or 0.0) for p in positions_data]
    net_usd = sum(notionals)
    gross_usd = sum(abs(n) for n in notionals)
    net_pct = net_usd / nav * 100 if nav else 0.0
    gross_x = gross_usd / nav if nav else 0.0
    target_net_pct = sum(float(r.get("target wt") or 0.0) for r in proposed_rows) * 100
    net_message = net_flag(net_pct, target_net_pct) if proposed_rows else None
    gross_message = gross_flag(gross_x) if positions_data and nav else None

    _render_header(
        proposed_rows=proposed_rows,
        as_of_date=as_of_date,
        nav=nav,
        positions_data=positions_data,
        df_pnl=df_pnl,
        risk=risk,
        drift=drift,
        stop_states=stop_states_raw,
        nav_snapshot=nav_snapshot,
        net_message=net_message,
        gross_message=gross_message,
    )

    # ---------------------------------------------------------------- Trade Approval
    _section("Trade Approval")

    if drift:
        shares_basis = drift.get("basis") == "shares"
        unit = "sh" if shares_basis else "$"
        fmt = (lambda v: f"{v:,.4f}") if shares_basis else (lambda v: f"{v:,.0f}")
        drifted_tickers = ", ".join(
            f"{t} (cached {unit}{fmt(d['cached'])} vs live {unit}{fmt(d['live'])})"
            for t, d in drift["detail"].items()
        )
        st.error(
            f"⚠️ Position drift detected at {drift['detected_at']}: the cached "
            f"position snapshot disagreed with live Alpaca on "
            f"{'share counts' if shares_basis else 'dollar values'} before that run "
            f"computed deltas — {drifted_tickers}. That run still executed against "
            f"the cached (pre-drift) snapshot; the deltas below reflect the corrected "
            f"positions."
        )

    st.caption(
        f"Signal as-of: {as_of_date}  |  {_nav_basis_line(nav_snapshot)}  |  "
        f"Delta = target minus current position"
    )

    df_trade = pd.DataFrame(proposed_rows) if proposed_rows else pd.DataFrame()

    stop_map: dict[str, dict] = (
        {r["ticker"]: r for r in stop_states_raw} if stop_states_raw else {}
    )
    advisory = any(r.get("advisory") for r in stop_states_raw) if stop_states_raw else True

    risk_columns = []
    non_normal: list[dict] = []
    for row in proposed_rows:
        t = row["ticker"]
        ss = stop_map.get(t, {})
        state = ss.get("state", "NORMAL")
        z_val = ss.get("z")

        if state == "NORMAL" or not state or state == "None":
            risk_columns.append("NORMAL")
        elif state == "REDUCED":
            pct = int((1 - float(ss.get("multiplier", 0.5))) * 100)
            z_str = f"{z_val:.1f}σ" if z_val is not None else "—"
            label = f"REDUCED -{pct}% (DD {z_str})"
            risk_columns.append(label)
            non_normal.append({"ticker": t, "state": "REDUCED", "z": z_val, "label": label})
        elif state == "STOPPED":
            z_str = f"{z_val:.1f}σ" if z_val is not None else "—"
            label = f"STOPPED (DD {z_str})"
            risk_columns.append(label)
            non_normal.append({"ticker": t, "state": "STOPPED", "z": z_val, "label": label})
        else:
            risk_columns.append(str(state))

    if not df_trade.empty:
        df_trade["risk status"] = risk_columns

    prefix = "ADVISORY (not traded) — " if advisory else ""
    if non_normal:
        lines = []
        for nn in non_normal:
            z_info = f", z={nn['z']:.2f}" if nn["z"] is not None else ""
            recovery = (
                "z ≥ -1.0 restores NORMAL"
                if nn["state"] == "REDUCED"
                else "z ≥ -2.0 restores REDUCED"
            )
            lines.append(f"**{nn['ticker']}**: {nn['state']}{z_info} → {recovery}")
        st.warning(
            f"{prefix}**{len(non_normal)} ticker(s) in drawdown:**\n\n"
            + "\n\n".join(lines)
        )
    elif stop_states_raw:
        live_non_normal = [
            r for r in stop_states_raw
            if r.get("state", "NORMAL") not in ("NORMAL", "None", None, "")
        ]
        if live_non_normal:
            lines = []
            for r in live_non_normal:
                t = r["ticker"]
                s = r.get("state", "NORMAL")
                z_val = r.get("z")
                z_info = f", z={z_val:.2f}" if z_val is not None else ""
                recovery = (
                    "z ≥ -1.0 restores NORMAL"
                    if s == "REDUCED"
                    else "z ≥ -2.0 restores REDUCED"
                )
                lines.append(f"**{t}**: {s}{z_info} → {recovery}")
            st.warning(
                f"{prefix}**{len(live_non_normal)} ticker(s) in drawdown "
                "(live stop_states):**\n\n" + "\n\n".join(lines)
            )
        else:
            st.success(f"{prefix}All positions NORMAL — no stop states active.")

    if not df_trade.empty:
        st.dataframe(df_trade, width="stretch", hide_index=True)

    pending_rows = [
        r for r in proposed_rows
        if not str(r.get("action", "")).startswith("skip")
    ]

    supabase_ok = bool(os.environ.get("SUPABASE_SECRET_KEY"))

    if not is_authenticated and secrets_configured:
        if not pending_rows:
            st.info("No trades pending today — there is nothing to approve.")
        else:
            st.info("Sign in to approve or reject today's trades.")
            if st.button("Sign in with Google", key="signin_btn"):
                st.login("google")
    elif not supabase_ok:
        st.warning(
            "Supabase credentials not configured -- the decision window cannot be read "
            "and a decision could not be saved. Set SUPABASE_URL and "
            "SUPABASE_SECRET_KEY in .env and restart."
        )
    else:
        today = _today_utc()
        existing = fetch_decision_for_date(as_of_date)
        last_run = _get_last_execution_run()
        last_run_date = str(last_run["run_date"]) if last_run else None
        state, window_reason = decision_window(
            today=today,
            as_of_date=as_of_date,
            has_proposal=bool(proposed_rows),
            last_execution_run_date=last_run_date,
        )

        auto_approve = get_auto_approve()
        new_val = st.toggle(
            "Auto-approve: execute every day unless I explicitly reject",
            value=auto_approve,
            help="When ON, the cron runs each morning without needing a daily approval. "
                 "Turn OFF to require an explicit approve each day.",
        )
        effective_auto_approve = auto_approve
        if new_val != auto_approve:
            if set_auto_approve(new_val):
                effective_auto_approve = new_val
                if new_val:
                    st.success("Auto-approve ON -- trades will execute daily unless you reject.")
                else:
                    st.info("Auto-approve OFF -- you must approve each morning to trade.")
            else:
                st.error(
                    "Could not save the auto-approve setting: the write to Supabase "
                    "failed, so the setting is unchanged. Try again."
                )

        if effective_auto_approve:
            st.caption(
                "Cron logic: execute unless today's decision is reject. "
                "No row or approve both trigger execution."
            )
        else:
            st.caption(
                "Cron logic: execute only if today's decision is approve. "
                "No row or reject both skip execution."
            )

        st.caption(
            f"Scheduled execution: {EXECUTION_CRON_ET} on weekdays. Whether this panel "
            f"offers a decision is decided by the recorded run, not the clock."
        )

        if state == DECISION_STATE_LOCKED:
            st.markdown(f"**Locked for today ({today}).** {window_reason.capitalize()}.")
            _render_execution_outcome(
                today=today,
                as_of_date=as_of_date,
                decision=existing,
                attr_rows=_get_live_attribution(60),
                pnl_row=next(
                    (r for r in pnl_rows if str(r.get("trade_date")) == today),
                    None,
                ),
            )
        elif state == DECISION_STATE_NO_SIGNAL:
            st.info(f"Waiting for the next signal: {window_reason}.")
        else:
            st.markdown(f"**Awaiting your decision for {as_of_date}.** {window_reason.capitalize()}.")
            if existing == "approve":
                st.success(f"Approved for {as_of_date} -- trades will execute at next cron run.")
                if st.button("Change to: Reject / skip today", key=f"reject_{as_of_date}"):
                    if write_decision(as_of_date, "reject"):
                        st.rerun()
            elif existing == "reject":
                st.error(f"Rejected for {as_of_date} -- no trades will execute.")
                if st.button("Change to: Approve all trades", type="primary",
                             key=f"approve_{as_of_date}"):
                    if write_decision(as_of_date, "approve"):
                        st.rerun()
            else:
                col_approve, col_reject = st.columns(2)
                if col_approve.button("Approve all trades", type="primary",
                                      key=f"approve_{as_of_date}"):
                    if write_decision(as_of_date, "approve"):
                        st.rerun()
                if col_reject.button("Reject / skip today", key=f"reject_{as_of_date}"):
                    if write_decision(as_of_date, "reject"):
                        st.rerun()

    # ---------------------------------------------------------------- Exposure
    _section("Exposure — current vs target")

    from signals.trend_signal import G_MAX_DEFAULT

    snapshot_date = (
        str(positions_data[0].get("trade_date", "—")) if positions_data else "—"
    )
    longs = sum(1 for n in notionals if n > 0)
    shorts = sum(1 for n in notionals if n < 0)
    target_gross_x = sum(abs(float(r.get("target wt") or 0.0)) for r in proposed_rows)

    st.markdown(
        f"**Net is the sum of each asset's trend call. It is not targeted at zero.** "
        f"Gross is the size of the book, aimed near {float(G_MAX_DEFAULT):.2f}x NAV. "
        f"Current: net {net_pct:+.1f}% of NAV vs target {target_net_pct:+.1f}%; "
        f"gross {gross_x:.2f}x vs target {target_gross_x:.2f}x. "
        f"Snapshot {snapshot_date}."
    )

    col_net, col_gross = st.columns(2)
    col_net.metric(
        "Net exposure",
        "$" + f"{net_usd:+,.0f}",
        delta=f"{net_pct:+.1f}% of NAV vs target {target_net_pct:+.1f}%",
        delta_color="off",
    )
    col_gross.metric(
        "Gross exposure (= leverage)",
        f"{gross_x:.2f}x NAV",
        delta=f"target {target_gross_x:.2f}x, hard cap {float(G_MAX_DEFAULT):.2f}x",
        delta_color="off",
    )

    st.markdown(f"**{longs} long, {shorts} short.**")

    off_target = [m for m in (net_message, gross_message) if m]
    if off_target:
        st.error("Off target: " + "; ".join(off_target) + ".")
    else:
        st.success(
            f"On target: net {net_pct:+.1f}% of NAV against a target of "
            f"{target_net_pct:+.1f}% (band +/-{NET_BAND_PP:.0f}pp); "
            f"gross {gross_x:.2f}x (floor {GROSS_FLOOR_X:.2f}x, "
            f"overshoot mark {GROSS_FLAG_X:.2f}x)."
        )

    st.caption(
        f"Market values as of the {snapshot_date} snapshot; the target is the signal "
        f"weights stored for {as_of_date}. {_nav_basis_line(nav_snapshot)}. Net is not "
        f"a target of its own: it is whatever the individual calls add up to. Leverage "
        f"is the same number as gross: exposure per dollar of equity."
    )

    if positions_data:
        current, target = _sleeve_exposure(positions_data, proposed_rows, nav)
        current_pct = [current[s] / nav * 100 if nav else 0.0 for s in SLEEVE_ORDER]
        target_pct = [target[s] / nav * 100 if nav else 0.0 for s in SLEEVE_ORDER]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Current",
            x=[s.capitalize() for s in SLEEVE_ORDER],
            y=current_pct,
            marker_color=[_side_colour(v) for v in current_pct],
            hovertemplate="%{x}<br>current %{y:+.1f}% of NAV<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Target",
            x=[s.capitalize() for s in SLEEVE_ORDER],
            y=target_pct,
            marker_color=[_side_colour(v) for v in target_pct],
            marker_opacity=0.35,
            hovertemplate="%{x}<br>target %{y:+.1f}% of NAV<extra></extra>",
        ))
        fig.update_layout(
            title="Net exposure by sleeve: current vs target",
            yaxis_title="net exposure, % of NAV",
            barmode="group",
        )
        _plot(fig, height=340)
        st.caption(
            f"Full bar = the sleeve's current net exposure; pale bar = the signal's "
            f"target for {as_of_date}. {LONG_SHORT_NOTE}."
        )
    else:
        st.info("No positions yet -- the sleeve view appears after the first execution run.")

    # ---------------------------------------------------------------- Performance
    _section("Performance")

    stats = _performance_stats(df_pnl)
    perf_cols = st.columns(4)
    perf_cols[0].metric(
        "MTD return",
        f"{stats['mtd_pct']:+.2f}%" if stats["mtd_pct"] is not None else "—",
        delta="month to date",
        delta_color="off",
    )
    perf_cols[1].metric(
        "Annualized vol",
        f"{stats['ann_vol_pct']:.2f}%" if stats["ann_vol_pct"] is not None else "—",
        delta="daily NAV returns",
        delta_color="off",
    )
    perf_cols[2].metric(
        "Sharpe",
        f"{stats['sharpe']:.2f}" if stats["sharpe"] is not None else "—",
        delta="annualized, zero risk-free",
        delta_color="off",
    )
    perf_cols[3].metric(
        "Max drawdown",
        f"{stats['max_dd_pct']:.2f}%" if stats["max_dd_pct"] is not None else "—",
        delta="peak to trough",
        delta_color="off",
    )

    if stats["n_navs"]:
        st.caption(
            f"From {stats['n_navs']} stored morning-execution NAVs, "
            f"{stats['first_date']} to {stats['last_date']}."
        )
    else:
        st.caption("No stored NAV history yet -- these fill from the next execution run.")

    if stats["n_navs"] >= 2:
        navs = df_pnl[df_pnl["live_nav"].notna()].copy()
        navs["trade_date"] = pd.to_datetime(navs["trade_date"])
        navs = navs.sort_values("trade_date")
        fig = go.Figure(go.Scatter(
            x=navs["trade_date"],
            y=navs["live_nav"].astype(float),
            mode="lines+markers",
            line=dict(color=NEUTRAL_COLOR, width=2),
            marker=dict(size=4),
            hovertemplate=(
                "%{x|%Y-%m-%d}<br>NAV %{y:,.2f}<br>"
                "morning execution<extra></extra>"
            ),
            name="Morning execution NAV",
        ))
        fig.update_layout(title="Account value over time", yaxis_title="account equity")
        _plot(fig, height=340)
        st.caption(
            "Last " + f"${stats['last_nav']:,.2f}" + f" on {stats['last_date']} "
            f"(morning execution, {_fmt_utc(stats['last_timestamp'])}). "
            f"{stats['n_navs']} recorded run(s)."
        )
    elif pnl_rows:
        st.info(
            "No account equity recorded yet. The next execution run starts the NAV "
            "history, and this chart then shows the account's real equity."
        )
    else:
        st.info("No account equity recorded yet -- the chart appears after the first run.")

    if not df_pnl.empty and "turnover_cost" in df_pnl.columns:
        costs = df_pnl["turnover_cost"].fillna(0.0).astype(float)
        total_cost = float(costs.sum())
        st.markdown(
            "**Turnover cost per run.** Total to date " + f"${total_cost:,.2f}"
            + f" over {len(df_pnl)} runs."
        )
        fig = go.Figure(go.Bar(
            x=pd.to_datetime(df_pnl["trade_date"]),
            y=costs,
            marker_color=NEUTRAL_COLOR,
            hovertemplate="%{x|%Y-%m-%d}<br>cost %{y:,.2f}<extra></extra>",
            name="Turnover cost",
        ))
        fig.update_layout(title="Turnover cost per run", yaxis_title="", showlegend=False)
        _plot(fig, height=260)
        st.caption(
            "Cost is a drag on account value, not the result: a single-digit dollar "
            "run (0 to 3, 0.00-0.02% of NAV) is normal."
        )
    else:
        st.info("No turnover-cost rows yet -- the cost chart appears after the first run.")

    # ---------------------------------------------------------------- Book P&L by sleeve
    _section("Book P&L by sleeve")

    sleeve_rows = _get_daily_sleeve_pnl()
    if sleeve_rows:
        df_sleeve = pd.DataFrame(sleeve_rows)
        df_sleeve = df_sleeve[df_sleeve["sleeve"].isin(SLEEVE_ORDER)]
        sessions = sorted(df_sleeve["trade_date"].unique())

        def _band(column: str) -> pd.DataFrame:
            wide = df_sleeve.pivot_table(
                index="trade_date", columns="sleeve", values=column, aggfunc="sum"
            )
            return wide.reindex(index=sessions, columns=list(SLEEVE_ORDER)).fillna(0.0)

        price_cum = _band("price_pnl").cumsum()
        carry_cum = _band("carry_pnl").cumsum()
        book_cum = (
            df_sleeve.groupby("trade_date")["book_total"].first()
            .reindex(sessions)
            .fillna(0.0)
            .cumsum()
        )

        st.markdown(
            "**Book P&L by sleeve.** Where the account's result came from: each band is "
            "one sleeve's contribution to the book's P&L since the first recorded "
            "session, stacked, and the line is the book's total."
        )

        xs = pd.to_datetime(list(price_cum.index))
        side = {
            s: "long" if sum(
                float(p.get("signed_notional") or 0.0)
                for p in positions_data if _sleeve_of(str(p.get("ticker", ""))) == s
            ) >= 0 else "short"
            for s in SLEEVE_ORDER
        }

        fig = go.Figure()
        for sleeve in SLEEVE_ORDER:
            colour = LONG_COLOR if side[sleeve] == "long" else SHORT_COLOR
            price_inc = price_cum[sleeve].diff()
            price_inc.iloc[0] = price_cum[sleeve].iloc[0]
            fig.add_trace(go.Scatter(
                x=xs, y=price_inc.values,
                name=f"{sleeve.capitalize()} price ({side[sleeve]})",
                stackgroup="one", mode="lines",
                line=dict(width=0.5, color=colour),
                fillcolor=_rgba(colour, 0.80),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:+,.2f}<extra></extra>",
            ))
            carry_inc = carry_cum[sleeve].diff()
            carry_inc.iloc[0] = carry_cum[sleeve].iloc[0]
            fig.add_trace(go.Scatter(
                x=xs, y=carry_inc.values,
                name=f"{sleeve.capitalize()} carry ({side[sleeve]})",
                stackgroup="one", mode="lines",
                line=dict(width=0.5, color=colour),
                fillcolor=_rgba(colour, 0.40),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:+,.2f}<extra></extra>",
            ))
        fig.add_trace(go.Scatter(
            x=xs, y=book_cum.values, name="Book total", mode="lines",
            line=dict(color="#111111", width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>book total %{y:+,.2f}<extra></extra>",
        ))
        fig.update_layout(title="Book P&L by sleeve: carry and price, stacked",
                          yaxis_title="cumulative P&L")
        _plot(fig, height=400)

        gap = float(book_cum.iloc[-1] - (price_cum.iloc[-1].sum() + carry_cum.iloc[-1].sum()))
        st.caption(
            "Solid band = price move, lighter band = distributions received (carry). "
            "Measured on the book held at the previous close, so a trade contributes "
            f"from the session after it. The four sleeves sum to the book total to "
            f"within " + f"${abs(gap):,.6f}" + f" over {len(sessions)} session(s)."
        )
        if float(df_sleeve["carry_pnl"].abs().sum()) == 0.0:
            st.caption("Carry is 0.00 in every recorded session: no distribution is recorded for those dates.")
    else:
        st.info(
            "No sleeve P&L recorded yet. The signal cron writes one row per sleeve per "
            "session, so this fills from its next run."
        )

    # ---------------------------------------------------------------- Risk contribution
    _section("Risk contribution")
    _render_mctr_pctr(nav, positions_data)

    # ---------------------------------------------------------------- Today's result
    _section("Today's result")

    last_run = df_pnl.iloc[-1] if not df_pnl.empty else None
    run_date = str(last_run["trade_date"]) if last_run is not None else "—"
    book_pnl = last_run.get("book_pnl") if last_run is not None else None
    cost_today = last_run.get("turnover_cost") if last_run is not None else None
    nav_at_run = last_run.get("live_nav") if last_run is not None else None

    col_book, col_cost = st.columns(2)
    if book_pnl is None or pd.isna(book_pnl):
        col_book.metric("Book P&L (equity move since previous run)", "not recorded yet")
    else:
        book_pct = (float(book_pnl) / float(nav_at_run) * 100) if nav_at_run else None
        col_book.metric(
            "Book P&L (equity move since previous run)",
            "$" + f"{float(book_pnl):+,.2f}",
            delta=f"{book_pct:+.2f}% of NAV" if book_pct is not None else None,
            delta_color="off",
        )

    if cost_today is None or pd.isna(cost_today):
        col_cost.metric("Turnover cost today", "—")
    else:
        col_cost.metric("Turnover cost today", _fmt_dollars(float(cost_today)))

    cost_total = (
        float(df_pnl["turnover_cost"].fillna(0.0).sum())
        if "turnover_cost" in df_pnl.columns else 0.0
    )
    st.caption(
        f"Book P&L is the account's equity move since the previous run, on the last "
        f"morning execution ({run_date}, {_fmt_utc(last_run.get('created_at') if last_run is not None else None)}). "
        f"Turnover cost is a drag on it, not part of it -- " + f"${cost_total:,.2f}"
        + f" cumulative over {len(df_pnl)} runs, and a single-digit dollar day is normal."
    )

    attr_rows = _get_live_attribution(60)
    if attr_rows:
        df_attr = pd.DataFrame(attr_rows)
        attr_date = str(df_attr["run_date"].max())
        df_latest = df_attr[df_attr["run_date"] == attr_date]
        if not df_latest.empty and "asset_class" in df_latest.columns:
            mark = df_latest.groupby("asset_class")["net_pnl"].sum()
            mark = mark.reindex([s for s in SLEEVE_ORDER if s in mark.index])
            total_mark = float(mark.sum())
            st.markdown(
                f"**Fill-day mark on {attr_date}'s filled legs, by sleeve "
                f"(not the book's P&L).**"
            )
            mark_colors = []
            for sleeve in mark.index:
                sleeve_net = sum(
                    float(p.get("signed_notional") or 0.0)
                    for p in positions_data
                    if _sleeve_of(str(p.get("ticker", ""))) == sleeve
                )
                mark_colors.append(_side_colour(sleeve_net))
            fig = go.Figure(go.Bar(
                x=[s.capitalize() for s in mark.index],
                y=mark.values,
                marker_color=mark_colors,
                text=["$" + f"{v:+,.2f}" for v in mark.values],
                textposition="outside",
                hovertemplate="%{x}<br>%{y:+,.2f}<extra></extra>",
            ))
            fig.update_layout(
                title="Fill-to-close mark on the day's filled legs, by sleeve",
                yaxis_title="", showlegend=False,
            )
            _plot(fig, height=300)
            st.caption(
                "$" + f"{total_mark:+,.2f}" + f" across {len(df_latest)} filled leg(s) on "
                f"{attr_date}, net of their turnover cost. One day, filled legs only, "
                f"so it is not a period return and not the book's P&L."
            )
        else:
            st.info("No attribution rows for the latest run.")
    else:
        st.info("No live_attribution rows yet -- they populate after an execution run.")
