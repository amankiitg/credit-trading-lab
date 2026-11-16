"""Operational panels for the trade-approval dashboard -- sprint v10.0.

One page, six blocks. Every block opens with the sentence that says what it tells
you and what the number should look like when nothing is wrong, because a bare
number with no reference point cannot be read.

  Header            Title, NAV source, and the six numbers an operator opens the page for.
  Trade Approval    Proposed next trade, stop-state advisory, and approve/reject.
  Exposure          Net and gross for the live book against the stored target.
  Performance       MTD return, volatility, Sharpe, max drawdown, account value, cost.
  Book P&L by sleeve  Where the account's result came from, carry versus price,
                    as two charts: a dated cumulative timeline, then the period
                    split per sleeve. Separate figures, so neither borrows the
                    other's x-axis.
  Risk contribution  Share of book risk by sleeve and by name.
  Morning execution run  What the morning run filled, and what those fills earned
                    from their fill price to the close.

Visual system:

  - Colour carries the asset class and nothing else: navy for equity, green for
    rates, violet for credit, amber for commodity. A signed quantity shows its
    sign by the direction of the bar or the sign in the tooltip, never by a
    second palette.
  - Green as a status colour (good, on target) appears only in the status
    messages, never on a chart.
  - Neutral grey is for series that carry no sleeve: the book's NAV and turnover
    cost.
  - Every history chart on the page reads the one period set in Performance, and
    no chart accepts scroll zoom: the axes are fixed.

No broker call is made from this page. The one series that is not built from stored
runs -- the account's close equity -- is read from Supabase (dashboard.equity_history),
where the evening job stores the close it already reconciles against.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.signal_history import render_signal_history
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
# One colour per asset class, used on every chart that draws a sleeve. The sign
# of a position is the direction of its bar (or the sign in the tooltip); colour
# never encodes it, so a sleeve reads the same whether it is long or short.
ASSET_CLASS_COLORS: dict[str, str] = {
    "equity":    "#1d4ed8",
    "rates":     "#047857",
    "credit":    "#7c3aed",
    "commodity": "#d97706",
}
# Archived name for the same palette, for callers outside this module.
SLEEVE_COLORS: dict[str, str] = ASSET_CLASS_COLORS
SLEEVE_ORDER: tuple[str, ...] = ("equity", "rates", "credit", "commodity")

NEUTRAL_COLOR = "#8a8a8a"
BOOK_TOTAL_COLOR = "#111111"

# Caption fragment wherever a sleeve bar can point either way.
SIGN_NOTE = "colour is the asset class, the sign of the bar shows direction"

# Reference points, from the spec. Display thresholds only, not enforced limits:
# the execution layer's own limits are G_MAX_DEFAULT (gross) and the delta band.
NET_BAND_PP = 5.0         # net more than this far from its target is off target
GROSS_FLAG_X = 2.05       # above this is a mark-to-market overshoot between runs
GROSS_FLOOR_X = 1.80      # the floor observed across the recorded snapshots
RISK_TICKER_FLAG_PCT = 40.0   # one position above 40% of book risk is concentration
RISK_SLEEVE_FLAG_PCT = 60.0   # one sleeve above 60% of book risk is concentration

# Shown with the concentration warning. The flag is a monitor: nothing in the
# signal caps a name's share of book risk, so the line says what the limit is not.
MONITOR_ONLY_NOTE = (
    "Monitor only: sizing is per-name vol and does not cap risk share."
)

PLOTLY_CONFIG = {
    "displayModeBar": True,
    # Scrolling the page must never zoom a chart: the axes are fixedrange below
    # and the wheel never reaches Plotly.
    "scrollZoom": False,
    "doubleClick": False,
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


def _sleeve_colour(sleeve: str) -> str:
    """The asset class's colour; unknown sleeves fall back to neutral grey."""
    return ASSET_CLASS_COLORS.get(sleeve, NEUTRAL_COLOR)


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
    """The one official NAV, its source and timestamp.

    settings.live_nav is written by both crons. The evening signal writes it after
    the close, so that is the run the page treats as official: its value, its run
    date and its completion time are the ones shown in the header, in the
    performance stats and on the account-value chart. When the last signal run is
    older than the last execution run (the evening job failed, or has not run yet)
    the stored value is the morning execution's, and it is labelled as such rather
    than passed off as an evening number.

    The morning execution NAV from the last pnl_log row stays available as the
    labelled secondary line on the account-value chart and in the header caption.
    """
    signal = _get_last_signal_run()
    execution = _get_last_execution_run()

    signal_ts = str(signal["completed_at"]) if signal and signal.get("completed_at") else None
    execution_ts = (
        str(execution["completed_at"])
        if execution and execution.get("completed_at") else None
    )

    if signal_ts and (not execution_ts or signal_ts >= execution_ts):
        source = "evening signal"
        timestamp = signal.get("completed_at")
        session = str(signal.get("run_date") or "") or None
    elif execution_ts:
        source = "morning execution"
        timestamp = execution.get("completed_at")
        session = str(execution.get("run_date") or "") or None
    else:
        source, timestamp, session = "stored NAV", None, None

    morning_nav = None
    morning_timestamp = None
    if not df_pnl.empty and "live_nav" in df_pnl.columns:
        valid = df_pnl.dropna(subset=["live_nav"])
        if not valid.empty:
            last = valid.iloc[-1]
            morning_nav = float(last["live_nav"])
            morning_timestamp = last.get("created_at")

    return {
        "value": float(nav),
        "source": source,
        "timestamp": timestamp,
        "session": session,
        "morning_nav": morning_nav,
        "morning_timestamp": morning_timestamp,
    }


def _nav_basis_line(nav_snapshot: dict) -> str:
    """The same sentence wherever a NAV is shown: value, source, timestamp."""
    return (
        f"NAV ${nav_snapshot['value']:,.2f} — {nav_snapshot['source']}, "
        f"{_fmt_utc(nav_snapshot['timestamp'])}"
    )


def _parse_timestamp(value) -> datetime | None:
    """A stored timestamp as an aware UTC datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _official_nav_frame(
    df_pnl: pd.DataFrame,
    df_sleeve: pd.DataFrame,
    nav_snapshot: dict,
    *,
    start: date | None = None,
    close_navs: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """One row per session: the account's equity at that session's close.

    The close is read from the close_equity table -- the series the evening job stores
    from the portfolio history -- not reconstructed from the next morning's rows. The
    reconstruction this replaces was

        evening_nav(t) = live_nav(t+1) - book_pnl(t+1)

    and it mixed two clocks: live_nav is the equity measured at 10:32 the next
    morning, while book_pnl is close-to-close. Every point therefore carried the next
    day's 10:32-to-close move -- exactly the gap this series is supposed to show.

    The last point is still the stored evening setting, dated and stamped by the run
    that wrote it: a session's close is stored only after that session, so on the
    evening of a session the setting is the number that run measured. Sessions with
    no stored close are skipped, never zero-filled. The second value is why the
    history could not be read, or None.

    `close_navs` injects a {session: close equity} mapping for tests; when it is None
    the table is read here.
    """
    from dashboard.equity_history import (
        CloseEquityUnavailable,
        cached_close_equity_by_session,
    )

    timestamps: dict[str, object] = {}
    if not df_sleeve.empty and "created_at" in df_sleeve.columns:
        dated = df_sleeve.dropna(subset=["created_at"])
        for session, group in dated.groupby("trade_date"):
            timestamps[str(session)] = group["created_at"].iloc[0]

    sessions: set[str] = set()
    if not df_pnl.empty and "trade_date" in df_pnl.columns:
        dated_runs = pd.to_datetime(df_pnl["trade_date"], errors="coerce").dropna()
        sessions |= {stamp.date().isoformat() for stamp in dated_runs}

    snapshot_session: str | None = None
    if nav_snapshot.get("session"):
        stamped = pd.to_datetime(nav_snapshot["session"], errors="coerce")
        if pd.notna(stamped):
            snapshot_session = stamped.date().isoformat()
            sessions.add(snapshot_session)

    ordered = sorted(
        session
        for session in sessions
        if start is None or date.fromisoformat(session) >= start
    )
    if snapshot_session is not None and snapshot_session not in ordered:
        ordered.append(snapshot_session)
        ordered.sort()

    reason: str | None = None
    if close_navs is None:
        try:
            close_navs = cached_close_equity_by_session(tuple(ordered))
        except CloseEquityUnavailable as exc:
            close_navs = {}
            reason = str(exc)

    rows: list[dict] = []
    for session in ordered:
        value = (close_navs or {}).get(session)
        if value is None:
            continue
        rows.append({
            "trade_date": pd.Timestamp(session),
            "nav": float(value),
            "timestamp": timestamps.get(session),
            "source": "close_equity",
        })

    if snapshot_session is not None:
        rows.append({
            "trade_date": pd.Timestamp(snapshot_session),
            "nav": float(nav_snapshot["value"]),
            "timestamp": nav_snapshot.get("timestamp"),
            "source": nav_snapshot.get("source"),
        })

    if not rows:
        return (
            pd.DataFrame(columns=["trade_date", "nav", "timestamp", "source"]),
            reason,
        )
    frame = pd.DataFrame(rows).dropna(subset=["nav"])
    return (
        frame.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True),
        reason,
    )


def _book_pnl_snapshot(
    df_sleeve: pd.DataFrame,
    df_pnl: pd.DataFrame,
    nav: float,
    *,
    nav_source: str | None = None,
    nav_session: str | None = None,
) -> dict:
    """The official daily P&L value, from the same run as the NAV card.

    The evening signal writes daily_sleeve_pnl after the close, one row per sleeve
    per session, and its book_total is that session's book P&L measured on the book
    held at the previous close. When the NAV card is showing that same evening run,
    the sleeve row for the same session is the number that belongs beside it, so it
    wins over the morning execution's pnl_log row. pnl_log stays the fallback for
    mornings where the signal has not recorded a session yet.
    """
    sleeve_value = sleeve_session = sleeve_ts = None
    if not df_sleeve.empty and "book_total" in df_sleeve.columns:
        dated = df_sleeve.dropna(subset=["book_total"])
        if not dated.empty:
            latest = max(str(value) for value in dated["trade_date"].unique())
            rows = dated[dated["trade_date"].astype(str) == latest]
            if not rows.empty:
                sleeve_session = latest
                sleeve_value = float(rows.iloc[0]["book_total"])
                sleeve_ts = _parse_timestamp(rows.iloc[0].get("created_at"))

    pnl_value = pnl_session = pnl_ts = None
    if not df_pnl.empty:
        last = df_pnl.iloc[-1]
        if "book_pnl" in df_pnl.columns and pd.notna(last.get("book_pnl")):
            pnl_value = float(last["book_pnl"])
            pnl_session = str(last.get("trade_date"))
            pnl_ts = _parse_timestamp(last.get("created_at"))

    # Same run as the NAV card: the sleeve row wins when the official NAV came
    # from the evening signal and this is that session's row.
    same_run = (
        nav_source == "evening signal"
        and sleeve_session is not None
        and nav_session is not None
        and str(sleeve_session) == str(nav_session)
    )
    use_sleeve = sleeve_value is not None and (
        pnl_value is None
        or same_run
        or (
            sleeve_ts is not None
            and pnl_ts is not None
            and sleeve_ts >= pnl_ts
        )
    )
    value = sleeve_value if use_sleeve else pnl_value
    if value is None:
        return {
            "value": None,
            "pct_of_nav": None,
            "session": None,
            "source": None,
            "timestamp": None,
        }

    return {
        "value": value,
        "pct_of_nav": value / nav * 100 if nav else None,
        "session": sleeve_session if use_sleeve else pnl_session,
        "source": "evening signal" if use_sleeve else "morning execution",
        "timestamp": sleeve_ts if use_sleeve else pnl_ts,
    }


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


def _performance_stats(nav_frame: pd.DataFrame) -> dict:
    """MTD return, annualized vol, Sharpe, and max drawdown from official NAVs.

    `nav_frame` is the evening-close series built by _official_nav_frame, already
    cut to the period the page is showing, so every statistic and the chart beside
    it describe the same sessions.
    """
    empty = {
        "mtd_pct": None, "ann_vol_pct": None, "sharpe": None,
        "max_dd_pct": None, "n_navs": 0, "first_date": None, "last_date": None,
        "last_nav": None, "last_timestamp": None,
    }
    if nav_frame.empty:
        return empty

    navs = nav_frame.dropna(subset=["nav"]).copy()
    if navs.empty:
        return empty
    navs["trade_date"] = pd.to_datetime(navs["trade_date"])
    navs = navs.sort_values("trade_date").set_index("trade_date")
    values = navs["nav"].astype(float)

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
        "last_timestamp": last.get("timestamp"),
    }


# ---------------------------------------------------------------- rendering

def _section(title: str) -> None:
    """One divider and one heading. Called once per section, so never doubled."""
    st.markdown("---")
    st.markdown(f"### {title}")


def _plot(
    fig: go.Figure,
    *,
    height: int = 320,
    legend_below: bool = False,
    top: int = 40,
    legend_font_size: int | None = None,
) -> None:
    # A figure with subplot titles needs more headroom, or Plotly draws the main
    # title into the first subplot's title.
    margin = dict(l=10, r=10, t=top, b=10)
    legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    if legend_below:
        legend = dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0)
        margin["b"] = 80
    if legend_font_size is not None:
        legend["font"] = {"size": legend_font_size}
    # Fixed axes: the wheel never zooms and a drag never pans, whatever the page
    # does around the chart. Pair this with scrollZoom=False in PLOTLY_CONFIG.
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(
        height=height,
        margin=margin,
        hovermode="x unified",
        legend=legend,
        dragmode=False,
    )
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


def _history_period_control(
    first_date: date | None, last_date: date | None
) -> date | None:
    """The one history window every chart on the page reads.

    Default is 1 October of the latest session's year, clamped to the data on
    file. A single control means the account-value, turnover and book P&L charts
    can never disagree about which sessions they are showing.
    """
    if first_date is None or last_date is None:
        return None
    default = date(last_date.year, 10, 1)
    default = max(first_date, min(default, last_date))
    return st.date_input(
        "History from",
        value=default,
        min_value=first_date,
        max_value=last_date,
        key="history_start",
        help=(
            "One period for the account-value, turnover and book P&L charts. "
            "The default is 1 October."
        ),
    )


def _cumulative_sleeve_pnl(
    df_sleeve: pd.DataFrame, *, start: date | None = None
) -> pd.DataFrame:
    """Cumulative P&L per sleeve plus the book's cumulative total per session.

    The book line is cumsum(book_total); each sleeve line is cumsum(total). The
    four sleeves reconcile to book_total every session, so the four cumulative
    lines sum to the book line at every point -- that is the invariant the chart
    test pins.

    A session with a missing book_total or a missing sleeve row is dropped whole
    rather than zero-filled: it cannot be drawn consistently, and a NaN tail at
    the end of the series (a session recorded before its P&L was) must not extend
    the chart past the last real point.
    """
    if df_sleeve.empty or "sleeve" not in df_sleeve.columns:
        return pd.DataFrame()
    frame = df_sleeve[df_sleeve["sleeve"].isin(SLEEVE_ORDER)].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"])
    if start is not None:
        frame = frame[frame["trade_date"] >= pd.Timestamp(start)]
    if frame.empty:
        return pd.DataFrame()

    wide = frame.pivot_table(
        index="trade_date", columns="sleeve", values="total", aggfunc="sum"
    ).reindex(columns=list(SLEEVE_ORDER))
    book = frame.groupby("trade_date")["book_total"].first()
    combined = pd.concat([wide, book.rename("book_total")], axis=1).sort_index()
    combined = combined.dropna(subset=list(SLEEVE_ORDER) + ["book_total"])
    if combined.empty:
        return pd.DataFrame()

    cumulative = combined[list(SLEEVE_ORDER)].cumsum()
    cumulative["book_total"] = combined["book_total"].cumsum()
    cumulative.index.name = "trade_date"
    return cumulative


def _fill_to_close_mark(
    attr_rows: list[dict],
    sleeve_rows: list[dict],
) -> dict | None:
    """Shares traded x (close - fill) for the latest session, as the email reports it.

    This is the traded part of the session, not a slippage measure and not a
    cost-inclusive mark. Sells are negative shares, so a sell below the close gives
    up the difference and its sign falls out of the arithmetic.

    The evening job computes it with risk.execution_pnl from the broker's fills and
    stores the per-sleeve split in daily_sleeve_pnl.execution_pnl -- the same split
    the summary email prints on its "Execution P&L" line. That column wins here so
    the page and the email cannot disagree about one session.

    Sessions written before that column existed fall back to live_attribution's
    gross_pnl, which feed_attribution built from the same fill notional. net_pnl is
    deliberately not used: it has the day's turnover cost subtracted, which is the
    number that made the page read -$8.74 while the email said +$0.87.
    """
    session: str | None = None
    by_sleeve: dict[str, float] = {}
    source = "evening signal"

    if sleeve_rows:
        frame = pd.DataFrame(sleeve_rows)
        if {"trade_date", "sleeve", "execution_pnl"} <= set(frame.columns):
            frame = frame.copy()
            frame["execution_pnl"] = pd.to_numeric(
                frame["execution_pnl"], errors="coerce"
            )
            frame = frame.dropna(subset=["execution_pnl"])
            if not frame.empty:
                session = max(str(value) for value in frame["trade_date"].unique())
                latest = frame[frame["trade_date"].astype(str) == session]
                for _, row in latest.iterrows():
                    sleeve = str(row.get("sleeve") or "")
                    if sleeve not in SLEEVE_ORDER:
                        continue
                    by_sleeve[sleeve] = by_sleeve.get(sleeve, 0.0) + float(
                        row["execution_pnl"]
                    )

    if not by_sleeve and attr_rows:
        frame = pd.DataFrame(attr_rows)
        if not frame.empty and "run_date" in frame.columns:
            session = max(str(value) for value in frame["run_date"].unique())
            latest = frame[frame["run_date"].astype(str) == session]
            for _, row in latest.iterrows():
                sleeve = str(row.get("asset_class") or "")
                if sleeve not in SLEEVE_ORDER:
                    continue
                gross = row.get("gross_pnl")
                if gross is None or pd.isna(gross):
                    net = row.get("net_pnl")
                    cost = row.get("turnover_cost") or 0.0
                    if net is None or pd.isna(net):
                        continue
                    gross = float(net) + float(cost)
                by_sleeve[sleeve] = by_sleeve.get(sleeve, 0.0) + float(gross)
            source = "live attribution"

    if not by_sleeve or session is None:
        return None

    fills = [row for row in attr_rows if str(row.get("run_date")) == str(session)]
    return {
        "session": str(session),
        "by_sleeve": by_sleeve,
        "total": sum(by_sleeve.values()),
        "fill_count": len(fills) or None,
        "source": source,
    }


def _staged_trades(
    pending_rows: list[dict],
    state: str,
    *,
    auto_approve: bool,
    decision: str | None,
) -> bool:
    """Whether a trade is waiting for this operator's approval right now.

    The window must be open: a delta row on a proposal the execution run has already
    acted on is history, not a staged trade. There must be at least one non-skip
    row, and it must not already be rejected. And the decision must actually be the
    operator's to make: with auto-approve on, the standing instruction is already
    "execute unless I reject", so no day is waiting on them and the public page has
    nothing to ask a visitor to sign in for.
    """
    return (
        bool(pending_rows)
        and state == DECISION_STATE_OPEN
        and not auto_approve
        and decision != "reject"
    )


def _fmt_cost(value: float | None) -> str:
    """A cost as the dollars it took, with no sign.

    Turnover cost is a drag that is already subtracted wherever it is used, so a
    plus sign in front of it reads as a gain. This shows the amount and nothing
    else; the label says it is a cost.
    """
    if value is None:
        return "—"
    return f"${abs(float(value)):,.2f}"


def _metric_card(col, label: str, value: str, note: str | None = None) -> None:
    """One metric with its provenance as a caption, not as st.metric's delta.

    The note used to be the delta argument. A delta draws an arrow and a colour
    beside the number, which reads as "this moved up or down"; these notes are
    provenance ("evening signal 22:31 UTC", "target 2.00x"), so they belong in a
    caption under the card where they cannot be read as a change.
    """
    col.metric(label, value)
    if note:
        col.caption(note)


def _order_summary(pending_rows: list[dict]) -> str | None:
    """The orders a proposal is staging, or None when every row is band noise.

    A row inside the delta band is not an order: the execution layer skips it, so
    "no trades pending" printed beside a table that shows a $253 sell is wrong.
    This names every row that is an order, with its signed delta.
    """
    if not pending_rows:
        return None
    parts = []
    for row in pending_rows:
        ticker = str(row.get("ticker") or "?")
        try:
            delta_text = f"${float(row.get('delta ($)')):+,.0f}"
        except (TypeError, ValueError):
            delta_text = "—"
        action = str(row.get("action") or "").strip()
        parts.append(f"{ticker} {delta_text}" + (f" ({action})" if action else ""))
    noun = "order" if len(pending_rows) == 1 else "orders"
    return f"{len(pending_rows)} {noun} for the next morning run: " + ", ".join(parts)


def _approval_status(*, state: str, auto_approve: bool, decision: str | None) -> str:
    """Whether a human has to act on the staged orders, in one sentence."""
    if decision == "reject":
        return "Approval: not needed — today is rejected, so no trades will execute."
    if decision == "approve":
        return "Approval: already approved — the next morning run executes these."
    if auto_approve:
        return (
            "Approval: not needed — auto-approve is ON, so the run executes "
            "unless you reject."
        )
    if state == DECISION_STATE_LOCKED:
        return (
            "Approval: not needed — the morning run for this signal has already "
            "happened."
        )
    if state == DECISION_STATE_NO_SIGNAL:
        return "Approval: not available yet — waiting for the next signal."
    return "Approval: needed before the next morning run."


def _period_bridge(
    nav_frame: pd.DataFrame,
    df_sleeve: pd.DataFrame,
    df_pnl: pd.DataFrame,
    *,
    start: date | None,
) -> tuple[list[dict], dict]:
    """The period's NAV move, decomposed into the terms that should explain it.

        NAV change = book P&L (price + carry)
                   + cumulative execution P&L
                   + paper short-distribution add-back
                   - turnover cost
                   + residual

    The NAV change is measured on official close NAVs, so it is read from the
    session BEFORE the first P&L session to the last session that has both a
    stored close and a stored sleeve row. A session whose close is not stored yet
    is left out of every term rather than half-counted.

    Execution P&L is only stored from 2026-11-09. A session before it has no
    value, and this reports that as "not stored" instead of a zero that would
    silently vanish into the residual: only sessions where all four sleeve rows
    carry a value are summed, and the count is part of the note.

    Returns (rows, summary). rows is the table the page draws; summary is the same
    numbers for tests and for the caption.
    """
    summary: dict = {
        "first": None, "last": None, "base": None,
        "nav_change": None, "book": None, "price": None, "carry": None,
        "execution": None, "execution_sessions": 0, "sessions": 0,
        "add_back": None, "cost": None, "residual": None, "residual_pct": None,
    }
    if df_sleeve.empty or nav_frame.empty:
        return [], summary

    sleeve = df_sleeve.copy()
    sleeve["trade_date"] = sleeve["trade_date"].astype(str)
    if start is not None:
        sleeve = sleeve[sleeve["trade_date"] >= start.isoformat()]
    if "book_total" in sleeve.columns:
        sleeve = sleeve.dropna(subset=["book_total"])
    if sleeve.empty:
        return [], summary

    navs = nav_frame.copy()
    navs["session"] = pd.to_datetime(navs["trade_date"], errors="coerce").dt.date
    navs = navs.dropna(subset=["session", "nav"])
    by_session = {
        session.isoformat(): float(value)
        for session, value in zip(navs["session"], navs["nav"])
    }
    sessions = sorted(set(sleeve["trade_date"]) & set(by_session))
    if not sessions:
        return [], summary
    base_candidates = [s for s in by_session if s < sessions[0]]
    if not base_candidates:
        return [], summary
    base = max(base_candidates)

    period = sleeve[sleeve["trade_date"].isin(sessions)]
    book = float(period["total"].sum())
    price = float(period["price_pnl"].sum())
    carry = float(period["carry_pnl"].sum())
    # The short leg's distribution is a timing item with two legs, and the bridge used to
    # draw only the first. The book accrues it on the ex-date, inside carry_pnl; paper
    # debits the cash on the PAY date, about three sessions later, and the evening job
    # stores what it took in distribution_pnl. The accrual alone describes a period that
    # closes before the pay date; once the pay date is inside the period, the broker's
    # debit is the same money and cancels it. Over a full ex-to-pay period the term is
    # therefore zero -- a statement about the book, not a term quietly dropped from the
    # identity. Only the short side is adjusted: paper credits a long payout on the pay
    # date and the carry credits it on the ex-date, which net out on their own.
    accrued = float(-period.loc[period["carry_pnl"] < 0, "carry_pnl"].sum())
    debited = 0.0
    if "distribution_pnl" in period.columns:
        debited = float(
            period["distribution_pnl"].fillna(0.0).astype(float).sum()
        )
    add_back = accrued + debited

    execution_sessions: list[str] = []
    execution = 0.0
    if "execution_pnl" in period.columns:
        for session, values in period.groupby("trade_date")["execution_pnl"]:
            if values.notna().all():
                execution_sessions.append(str(session))
                execution += float(values.sum())
    execution_value = execution if execution_sessions else None

    cost = None
    runs = 0
    if not df_pnl.empty and "turnover_cost" in df_pnl.columns:
        log = df_pnl.copy()
        log["trade_date"] = log["trade_date"].astype(str)
        log = log[log["trade_date"].isin(sessions)]
        runs = len(log)
        cost = float(log["turnover_cost"].fillna(0.0).astype(float).sum())

    nav_change = by_session[sessions[-1]] - by_session[base]
    residual = None
    if execution_value is not None:
        residual = nav_change - (
            book + execution_value + add_back - float(cost or 0.0)
        )

    summary.update({
        "first": sessions[0], "last": sessions[-1], "base": base,
        "nav_change": nav_change, "book": book, "price": price, "carry": carry,
        "execution": execution_value,
        "execution_sessions": len(execution_sessions),
        "sessions": len(sessions), "add_back": add_back, "cost": cost,
        "add_back_accrued": accrued, "add_back_debited": debited,
        "residual": residual,
        "residual_pct": (
            residual / by_session[sessions[-1]] * 100
            if residual is not None and by_session[sessions[-1]] else None
        ),
    })

    missing = len(sessions) - len(execution_sessions)
    rows = [
        {
            "term": "NAV change (official closes)",
            "amount": f"${nav_change:+,.2f}",
            "note": (
                f"{base} close ${by_session[base]:,.2f} to "
                f"{sessions[-1]} close ${by_session[sessions[-1]]:,.2f}"
            ),
        },
        {
            "term": "Cumulative book P&L",
            "amount": f"${book:+,.2f}",
            "note": (
                f"price ${price:+,.2f} + carry ${carry:+,.2f}, "
                f"{len(sessions)} session(s)"
            ),
        },
        {
            "term": "Cumulative execution P&L",
            "amount": (
                f"${execution_value:+,.2f}"
                if execution_value is not None else "not stored"
            ),
            "note": (
                (
                    f"{len(execution_sessions)} of {len(sessions)} session(s) stored"
                    + (
                        f"; not stored for {missing} session(s) before 2026-11-09"
                        if missing else ""
                    )
                )
                if execution_sessions
                else "no session in this period has a stored execution P&L"
            ),
        },
        {
            "term": "Paper short-distribution add-back",
            "amount": f"${add_back:+,.2f}",
            "note": (
                f"ex-date accrual ${accrued:+,.2f} + broker pay-date debit "
                f"${debited:+,.2f}"
                + (
                    "; pay date in this period, so the item nets out"
                    if abs(debited) > 0.005
                    else "; pay date not in this period yet"
                )
            ),
        },
        {
            "term": "Turnover cost (subtracted)",
            "amount": _fmt_cost(cost),
            "note": f"{runs} morning run(s) in the period",
        },
        {
            "term": "Residual",
            "amount": f"${residual:+,.2f}" if residual is not None else "—",
            "note": "what the terms above do not explain",
        },
    ]
    return rows, summary


def _render_period_bridge(
    nav_frame: pd.DataFrame,
    df_sleeve: pd.DataFrame,
    df_pnl: pd.DataFrame,
    *,
    start: date | None,
) -> dict:
    """Draw the period bridge table under the Book P&L charts."""
    rows, summary = _period_bridge(nav_frame, df_sleeve, df_pnl, start=start)
    if not rows:
        st.info(
            "The period bridge needs a stored close before the first P&L session "
            "and at least one session with both a close and sleeve rows."
        )
        return summary

    st.markdown(
        "**Period bridge.** Does the account's NAV move equal the book's own P&L? "
        "Each row is one term of the identity below; the residual is what is left "
        "over, and it should stay near zero."
    )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    missing = summary["sessions"] - summary["execution_sessions"]
    parts = [
        "NAV change (official closes) = book P&L (price + carry) + execution P&L "
        "+ paper short-distribution add-back - turnover cost + residual."
    ]
    if summary["residual"] is not None:
        parts.append(
            f"Residual ${summary['residual']:+,.2f}"
            + (
                f" ({summary['residual_pct']:+.2f}% of NAV)"
                if summary["residual_pct"] is not None else ""
            )
            + "."
        )
    if missing:
        parts.append(
            f"Execution P&L is stored for {summary['execution_sessions']} of "
            f"{summary['sessions']} sessions; the {missing} without it are before "
            "2026-11-09, and their traded part sits in the residual, not in a zero."
        )
    st.caption(" ".join(parts))
    return summary


def _active_flags(
    concentration: list[str],
    net_message: str | None,
    gross_message: str | None,
    drift: dict | None,
    stop_states: list[dict],
) -> list[str]:
    """Every active flag as the sentence an operator can act on.

    The header card lists these in full below the count: a category name such as
    "net off target" tells nobody which way, by how much, or against what band.
    """
    flags: list[str] = list(concentration)
    if net_message:
        flags.append(net_message)
    if gross_message:
        flags.append(gross_message)
    if drift:
        drifted = sorted((drift.get("detail") or {}).keys())
        flags.append(
            "position drift: the cached snapshot disagreed with live Alpaca"
            + (f" on {', '.join(drifted)}" if drifted else "")
            + (f" (detected {drift['detected_at']})" if drift.get("detected_at") else "")
        )
    non_normal = [
        row for row in stop_states
        if row.get("state", "NORMAL") not in ("NORMAL", "None", None, "")
    ]
    if non_normal:
        flags.append(
            f"{len(non_normal)} stop state(s) away from NORMAL: "
            + ", ".join(
                f"{row.get('ticker')} {row.get('state')}" for row in non_normal
            )
        )
    return flags


def _render_header(
    *,
    proposed_rows: list[dict],
    as_of_date: str,
    nav: float,
    positions_data: list[dict],
    book_pnl: dict,
    risk: dict | None,
    drift: dict | None,
    stop_states: list[dict],
    nav_snapshot: dict,
    net_message: str | None,
    gross_message: str | None,
) -> None:
    """Title and the six numbers, each with the run it came from.

    The NAV and daily P&L cards are one run apart in time only if something is
    wrong: both carry the run timestamp of the NAV the page is showing.
    """
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

    run_label = f"{nav_snapshot['source']} {_fmt_utc(nav_snapshot['timestamp'])}"
    if book_pnl["value"] is None:
        pnl_value, pnl_delta = "—", "not recorded yet"
    else:
        pnl_value = "$" + f"{float(book_pnl['value']):+,.2f}"
        # The metric delta is a single ellipsised line at the 1200px window the
        # header is read at: keep the percentage and the full run timestamp, and
        # let the caption below the cards name the run and the session.
        pnl_stamp = _fmt_utc(book_pnl["timestamp"])
        pnl_delta = (
            f"{book_pnl['pct_of_nav']:+.2f}% · {pnl_stamp}"
            if book_pnl["pct_of_nav"] is not None
            else pnl_stamp
        )

    book_vol = risk.get("port_vol") if risk and "error" not in risk else None
    book_vol_value = f"{book_vol * 100:.1f}%" if book_vol is not None else "—"

    concentration = _concentration_messages(risk)
    flags = _active_flags(
        concentration, net_message, gross_message, drift, stop_states
    )

    if concentration:
        st.warning("**Concentration risk** — " + "; ".join(concentration) + ".")
        # Beside the flag, not on a chart: the warning reports a share of risk, and
        # nothing in the sizing caps that share.
        st.caption(MONITOR_ONLY_NOTE)

    # Two rows of three. Six cards in one row truncates the values on a
    # 1200px-wide window, and a truncated NAV is worse than no card.
    row_one = st.columns(3)
    target_gross_x = sum(abs(float(r.get("target wt") or 0.0)) for r in proposed_rows)
    target_net_pct = sum(float(r.get("target wt") or 0.0) for r in proposed_rows) * 100
    _metric_card(row_one[0], "NAV", "$" + f"{nav_snapshot['value']:,.2f}", run_label)
    _metric_card(
        row_one[1], "Daily P&L (close-to-close)", pnl_value, pnl_delta
    )
    _metric_card(
        row_one[2], "Gross", f"{gross_x:.2f}x NAV", f"target {target_gross_x:.2f}x"
    )
    row_two = st.columns(3)
    _metric_card(
        row_two[0], "Net", f"{net_pct:+.1f}%",
        "$" + f"{net_usd:+,.0f} · target {target_net_pct:+.1f}%",
    )
    _metric_card(
        row_two[1], "Book vol", book_vol_value,
        "annualized, 120-session covariance",
    )
    with row_two[2].container(border=True):
        st.metric("Active risk flags", str(len(flags)))
        if flags:
            st.markdown("\n".join(f"- {flag}" for flag in flags))
        else:
            st.caption("All clear: no concentration, exposure, drift or stop flags.")

    pnl_basis = (
        f"Official daily P&L is the {book_pnl['source']}'s close-to-close book "
        f"P&L for the {book_pnl['session']} session "
        f"({_fmt_utc(book_pnl['timestamp'])}), the same run as the NAV card. "
        f"It is the only daily P&L on the page."
        if book_pnl["value"] is not None
        else "No book P&L is recorded yet."
    )
    st.caption(
        pnl_basis
        + f" Official NAV: {nav_snapshot['source']}, "
        + f"{_fmt_utc(nav_snapshot['timestamp'])}. "
        + (
            "Secondary line only — morning execution NAV at "
            f"{_fmt_utc(nav_snapshot['morning_timestamp'])}: "
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
    sleeve_colors = [_sleeve_colour(str(s)) for s in sleeve_tbl.index]
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
        f"book's risk. {SIGN_NOTE}."
    )

    with st.expander("Per-ticker detail (weight, marginal risk, contribution)"):
        by_ticker = risk_df.sort_values("pctr", ascending=False)
        ticker_y = [float(v) * 100 for v in by_ticker["pctr"]]
        ticker_colors = [_sleeve_colour(_sleeve_of(str(t))) for t in by_ticker.index]
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
            f"risk; the share is its fraction of the book's total. {SIGN_NOTE}."
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
    sleeve_rows = _get_daily_sleeve_pnl()
    stop_states_raw = _get_stop_states()
    drift = _get_drift_alert()
    nav_snapshot = _nav_snapshot(nav, df_pnl)
    book_pnl = _book_pnl_snapshot(
        pd.DataFrame(sleeve_rows) if sleeve_rows else pd.DataFrame(),
        df_pnl,
        nav_snapshot["value"],
        nav_source=nav_snapshot["source"],
        nav_session=nav_snapshot["session"],
    )
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
        book_pnl=book_pnl,
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

    # Whether a trade is staged is the same question for a signed-in and a
    # signed-out reader, so it is answered before the auth branch: the window must
    # be open, at least one row must be an order rather than band noise, the day
    # must not already be rejected, and the decision must be the operator's to make.
    # With auto-approve on the standing instruction already answers it, so a
    # signed-out visitor is not asked to sign in for nothing.
    today = _today_utc()
    last_run = _get_last_execution_run()
    last_run_date = str(last_run["run_date"]) if last_run else None
    state, window_reason = decision_window(
        today=today,
        as_of_date=as_of_date,
        has_proposal=bool(proposed_rows),
        last_execution_run_date=last_run_date,
    )
    auto_approve = get_auto_approve()
    existing = fetch_decision_for_date(as_of_date)
    staged = _staged_trades(
        pending_rows, state, auto_approve=auto_approve, decision=existing
    )

    order_summary = _order_summary(pending_rows)
    approval = _approval_status(
        state=state, auto_approve=auto_approve, decision=existing
    )

    if not is_authenticated and secrets_configured:
        if order_summary is None:
            # Plain information, no prompt: there is nothing an account could
            # unlock. The advisory stop note above stays readable as it is.
            st.info(
                f"No trades pending for {as_of_date} — every delta is inside the "
                "±$250 band."
            )
        else:
            # A row outside the band is an order. The panel names it and says
            # whether a decision is waiting, instead of "no trades pending"
            # beside a table that lists a sell.
            st.info(order_summary + "\n\n" + approval)
            if staged:
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
        if order_summary is not None:
            st.info(order_summary + "\n\n" + approval)
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
    _metric_card(
        col_net,
        "Net exposure",
        "$" + f"{net_usd:+,.0f}",
        f"{net_pct:+.1f}% of NAV vs target {target_net_pct:+.1f}%",
    )
    _metric_card(
        col_gross,
        "Gross exposure (= leverage)",
        f"{gross_x:.2f}x NAV",
        f"target {target_gross_x:.2f}x, hard cap {float(G_MAX_DEFAULT):.2f}x",
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
            marker_color=[_sleeve_colour(s) for s in SLEEVE_ORDER],
            hovertemplate="%{x}<br>current %{y:+.1f}% of NAV<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Target",
            x=[s.capitalize() for s in SLEEVE_ORDER],
            y=target_pct,
            marker_color=[_sleeve_colour(s) for s in SLEEVE_ORDER],
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
            f"target for {as_of_date}. {SIGN_NOTE}."
        )
    else:
        st.info("No positions yet -- the sleeve view appears after the first execution run.")

    # ---------------------------------------------------------------- Performance
    _section("Performance")

    # One official NAV series, and one period shared with every chart below. The
    # period is chosen from the stored sessions first: the close read only has to
    # cover the window the page is about to draw.
    date_parts = []
    if not df_pnl.empty and "trade_date" in df_pnl.columns:
        date_parts.append(pd.to_datetime(df_pnl["trade_date"], errors="coerce").dropna())
    if nav_snapshot.get("session"):
        snapshot_date = pd.to_datetime(nav_snapshot["session"], errors="coerce")
        if pd.notna(snapshot_date):
            date_parts.append(pd.Series([snapshot_date]))
    if sleeve_rows:
        sleeve_dates = pd.to_datetime(
            pd.DataFrame(sleeve_rows)["trade_date"], errors="coerce"
        ).dropna()
        date_parts.append(sleeve_dates)
    if date_parts:
        all_dates = pd.concat(date_parts)
        history_start = _history_period_control(
            all_dates.min().date(), all_dates.max().date()
        )
    else:
        history_start = None

    official_nav, nav_history_error = _official_nav_frame(
        df_pnl,
        pd.DataFrame(sleeve_rows) if sleeve_rows else pd.DataFrame(),
        nav_snapshot,
        start=history_start,
    )

    window_nav = official_nav
    if history_start is not None and not official_nav.empty:
        window_nav = official_nav[
            official_nav["trade_date"] >= pd.Timestamp(history_start)
        ]

    stats = _performance_stats(window_nav)
    perf_cols = st.columns(4)
    _metric_card(
        perf_cols[0],
        "MTD return",
        f"{stats['mtd_pct']:+.2f}%" if stats["mtd_pct"] is not None else "—",
        "month to date",
    )
    _metric_card(
        perf_cols[1],
        "Annualized vol",
        f"{stats['ann_vol_pct']:.2f}%" if stats["ann_vol_pct"] is not None else "—",
        "daily NAV returns",
    )
    _metric_card(
        perf_cols[2],
        "Sharpe",
        f"{stats['sharpe']:.2f}" if stats["sharpe"] is not None else "—",
        "annualized, zero risk-free",
    )
    _metric_card(
        perf_cols[3],
        "Max drawdown",
        f"{stats['max_dd_pct']:.2f}%" if stats["max_dd_pct"] is not None else "—",
        "peak to trough",
    )

    if nav_history_error:
        st.warning(
            "Account equity history is unavailable, so the chart shows only the "
            f"latest stored NAV. {nav_history_error}"
        )
    if stats["n_navs"]:
        st.caption(
            f"From {stats['n_navs']} evening close NAVs, "
            f"{stats['first_date']} to {stats['last_date']} — the same official "
            f"NAV the header shows, over the period set above."
        )
    else:
        st.caption(
            "No evening close NAV history in this period yet — these fill from "
            "the next signal run."
        )

    if stats["n_navs"] >= 2:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=window_nav["trade_date"],
            y=window_nav["nav"].astype(float),
            mode="lines+markers",
            line=dict(color=BOOK_TOTAL_COLOR, width=2),
            marker=dict(size=4),
            name="Evening close NAV (official)",
            customdata=[[_fmt_utc(ts)] for ts in window_nav["timestamp"]],
            hovertemplate=(
                "%{x|%Y-%m-%d}<br>official NAV %{y:,.2f}<br>"
                "%{customdata[0]}<extra></extra>"
            ),
        ))
        if not df_pnl.empty and "live_nav" in df_pnl.columns:
            morning = df_pnl.dropna(subset=["live_nav"]).copy()
            morning["trade_date"] = pd.to_datetime(morning["trade_date"], errors="coerce")
            morning = morning.dropna(subset=["trade_date"]).sort_values("trade_date")
            if history_start is not None:
                morning = morning[morning["trade_date"] >= pd.Timestamp(history_start)]
            if not morning.empty:
                fig.add_trace(go.Scatter(
                    x=morning["trade_date"],
                    y=morning["live_nav"].astype(float),
                    mode="lines+markers",
                    line=dict(color=NEUTRAL_COLOR, width=1.5, dash="dot"),
                    marker=dict(size=3),
                    name="Morning execution NAV (secondary)",
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>morning NAV %{y:,.2f}"
                        "<extra></extra>"
                    ),
                ))
        fig.update_layout(
            title="Account value — official evening NAV",
            yaxis_title="account equity",
        )
        # The legend names the official and secondary lines; below the plot it
        # cannot collide with the title at a narrow window.
        _plot(fig, height=380, legend_below=True)
        st.caption(
            "Official NAV is the last stored close, or the evening run's own NAV "
            "until that close is published: "
            + f"last ${stats['last_nav']:,.2f} on {stats['last_date']} "
            + f"({_fmt_utc(stats['last_timestamp'])}). "
            + "The dotted line is the morning execution NAV, shown only as a "
            + "secondary reference."
        )
    elif not official_nav.empty:
        st.info(
            "Only one evening close NAV is recorded in this period. The next run "
            "starts the account-value line."
        )
    else:
        st.info("No account equity recorded yet -- the chart appears after the first run.")

    if not df_pnl.empty and "turnover_cost" in df_pnl.columns:
        runs = df_pnl.copy()
        runs["trade_date"] = pd.to_datetime(runs["trade_date"], errors="coerce")
        runs = runs.dropna(subset=["trade_date"]).sort_values("trade_date")
        if history_start is not None:
            runs = runs[runs["trade_date"] >= pd.Timestamp(history_start)]
        if runs.empty:
            st.info("No turnover-cost rows in this period.")
        else:
            costs = runs["turnover_cost"].fillna(0.0).astype(float)
            total_cost = float(costs.sum())
            st.markdown(
                "**Turnover cost per run.** Total over the period "
                + f"${total_cost:,.2f} over {len(runs)} runs."
            )
            fig = go.Figure(go.Bar(
                x=runs["trade_date"],
                y=costs,
                marker_color=NEUTRAL_COLOR,
                hovertemplate="%{x|%Y-%m-%d}<br>cost %{y:,.2f}<extra></extra>",
                name="Turnover cost",
            ))
            fig.update_layout(
                title="Turnover cost per run (same period)",
                yaxis_title="",
                showlegend=False,
            )
            _plot(fig, height=260)
            st.caption(
                "Cost is a drag on account value, not the result: a single-digit "
                "dollar run (0 to 3, 0.00-0.02% of NAV) is normal. Aligned to the "
                "same period as the other history charts."
            )
    else:
        st.info("No turnover-cost rows yet -- the cost chart appears after the first run.")

    # ---------------------------------------------------------------- Book P&L by sleeve
    _section("Book P&L by sleeve")

    if sleeve_rows:
        df_sleeve = pd.DataFrame(sleeve_rows)
        df_sleeve = df_sleeve[df_sleeve["sleeve"].isin(SLEEVE_ORDER)].copy()
        cumulative = _cumulative_sleeve_pnl(df_sleeve, start=history_start)

        if cumulative.empty:
            st.info(
                "No complete sleeve P&L session in this period: every session needs "
                "all four sleeve rows and its book total before it can be drawn."
            )
        else:
            sessions = list(cumulative.index)
            st.markdown(
                "**Book P&L by sleeve.** Where the account's result came from: one "
                "cumulative line per sleeve and the bold line for the book's total, "
                "so a sleeve that gave back its early gain turns down instead of "
                "shrinking a band. The bars below split the period's total per sleeve "
                "into price and carry."
            )

            xs = pd.to_datetime(sessions)
            period = df_sleeve.copy()
            period["trade_date"] = pd.to_datetime(period["trade_date"], errors="coerce")
            period = period[period["trade_date"].isin(xs)]

            # The timeline is its own figure with its own date axis. It used to
            # share an x-axis with the categorical bars below, and the shared axis
            # took the bars' category type: the dates were in the data and
            # invisible on the page.
            timeline = go.Figure()
            for sleeve in SLEEVE_ORDER:
                timeline.add_trace(
                    go.Scatter(
                        x=xs,
                        y=cumulative[sleeve].values,
                        name=sleeve.capitalize(),
                        mode="lines",
                        line=dict(color=_sleeve_colour(sleeve), width=2),
                        hovertemplate=(
                            "%{x|%Y-%m-%d}<br>" + sleeve
                            + " %{y:+,.2f}<extra></extra>"
                        ),
                    )
                )
            timeline.add_trace(
                go.Scatter(
                    x=xs,
                    y=cumulative["book_total"].values,
                    name="Book total",
                    mode="lines",
                    line=dict(color=BOOK_TOTAL_COLOR, width=4),
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>book total %{y:+,.2f}<extra></extra>"
                    ),
                )
            )
            timeline.update_layout(
                title="Cumulative P&L by sleeve (timeline)",
                xaxis_title="session",
                yaxis_title="cumulative P&L",
            )
            # A real date axis with its own ticks. Without this a categorical
            # axis can still swallow the labels, which was the visible bug.
            timeline.update_xaxes(type="date", tickformat="%b %d", nticks=8)
            _plot(timeline, height=380, legend_below=True)

            price = (
                period.groupby("sleeve")["price_pnl"].sum()
                .reindex(list(SLEEVE_ORDER)).fillna(0.0)
            )
            carry = (
                period.groupby("sleeve")["carry_pnl"].sum()
                .reindex(list(SLEEVE_ORDER)).fillna(0.0)
            )
            labels = [sleeve.capitalize() for sleeve in SLEEVE_ORDER]
            bars = go.Figure()
            # The legend says which bar is price and which is carry, so its
            # swatches are neutral: solid grey against light grey. A sleeve
            # colour in the legend would read as a fifth, colourless sleeve.
            for name, shade in (("Price", NEUTRAL_COLOR), ("Carry", _rgba(NEUTRAL_COLOR, 0.35))):
                bars.add_trace(
                    go.Scatter(
                        x=[labels[0]],
                        y=[None],
                        mode="markers",
                        marker=dict(color=shade, size=10, symbol="square"),
                        name=name,
                        hoverinfo="skip",
                        showlegend=True,
                    )
                )
            bars.add_trace(
                go.Bar(
                    x=labels,
                    y=price.values,
                    name="Price",
                    marker_color=[_sleeve_colour(s) for s in SLEEVE_ORDER],
                    hovertemplate="%{x}<br>price %{y:+,.2f}<extra></extra>",
                    showlegend=False,
                )
            )
            bars.add_trace(
                go.Bar(
                    x=labels,
                    y=carry.values,
                    name="Carry",
                    marker_color=[_rgba(_sleeve_colour(s), 0.45) for s in SLEEVE_ORDER],
                    hovertemplate="%{x}<br>carry %{y:+,.2f}<extra></extra>",
                    showlegend=False,
                )
            )
            bars.update_layout(
                title="Period P&L by sleeve: price vs carry",
                xaxis_title="sleeve",
                yaxis_title="$ over the period",
                barmode="group",
            )
            _plot(bars, height=320, legend_below=True)

            gap = float(
                cumulative["book_total"].iloc[-1]
                - cumulative[list(SLEEVE_ORDER)].iloc[-1].sum()
            )
            st.caption(
                "Lines are cumulative P&L from the first session in the period. "
                "Colour is the asset class; the bold black line is the book total. "
                "Measured on the book held at the previous close, so a trade "
                f"contributes from the session after it. The four sleeves sum to the "
                f"book total to within ${abs(gap):,.6f} at every point over "
                f"{len(sessions)} session(s)."
            )
            st.caption(
                "Bars: each sleeve's total over the period, solid for the price move "
                "and lighter for distributions paid on shorts (negative) or received "
                "on longs (carry). Colour is the asset class; the sign of the bar is "
                "its direction."
            )
            if float(period["carry_pnl"].abs().sum()) == 0.0:
                st.caption(
                    "Carry is 0.00 in every recorded session: no distribution is "
                    "recorded for those dates."
                )

            # Under the charts, the identity that ties them to the account's own
            # NAV move: the charts say where the money came from, the bridge says
            # whether that adds up to what the broker reported.
            _render_period_bridge(
                official_nav, df_sleeve, df_pnl, start=history_start
            )
    else:
        st.info(
            "No sleeve P&L recorded yet. The signal cron writes one row per sleeve per "
            "session, so this fills from its next run."
        )

    # ---------------------------------------------------------------- Risk contribution
    _section("Risk contribution")
    _render_mctr_pctr(nav, positions_data)

    # ---------------------------------------------------------------- Morning execution run
    _section("Morning execution run")

    last_run = df_pnl.iloc[-1] if not df_pnl.empty else None
    run_date = str(last_run["trade_date"]) if last_run is not None else "—"
    cost_today = last_run.get("turnover_cost") if last_run is not None else None
    run_stamp = _fmt_utc(last_run.get("created_at") if last_run is not None else None)

    if cost_today is None or pd.isna(cost_today):
        st.metric("Turnover cost today", "—")
    else:
        st.metric("Turnover cost today", _fmt_cost(float(cost_today)))

    # The cumulative cost is scoped like every chart above it: the period the
    # reader picked. It used to sum the whole pnl_log, so a 21-day window compared
    # a month of cost against a day's P&L.
    period_runs = df_pnl
    if (
        history_start is not None
        and not df_pnl.empty
        and "trade_date" in df_pnl.columns
    ):
        period_runs = df_pnl[
            pd.to_datetime(df_pnl["trade_date"], errors="coerce")
            >= pd.Timestamp(history_start)
        ]
    cost_total = (
        float(period_runs["turnover_cost"].fillna(0.0).astype(float).sum())
        if "turnover_cost" in period_runs.columns else 0.0
    )
    cost_scope = (
        f"over the {len(period_runs)} runs in this period"
        if history_start is not None
        else f"all-time over {len(period_runs)} runs"
    )
    st.caption(
        f"The morning execution run of {run_date} ({run_stamp}). Its book P&L is "
        f"not repeated here: the page carries one daily number, the official "
        f"close-to-close P&L in the header. Turnover cost is a drag, not part of "
        f"the result -- " + _fmt_cost(cost_total)
        + f" {cost_scope}, and a single-digit dollar day is normal."
    )

    attr_rows = _get_live_attribution(60)
    mark = _fill_to_close_mark(attr_rows, sleeve_rows)
    if mark:
        sleeves = [s for s in SLEEVE_ORDER if s in mark["by_sleeve"]]
        st.markdown(
            f"**Execution P&L on {mark['session']}'s fills, by sleeve "
            f"(shares traded x (close - fill), sells negative).**"
        )
        fig = go.Figure(go.Bar(
            x=[s.capitalize() for s in sleeves],
            y=[mark["by_sleeve"][s] for s in sleeves],
            marker_color=[_sleeve_colour(s) for s in sleeves],
            text=["$" + f"{mark['by_sleeve'][s]:+,.2f}" for s in sleeves],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:+,.2f}<extra></extra>",
        ))
        fig.update_layout(
            title="Fill-to-close mark on the day's filled legs, by sleeve",
            xaxis_title="sleeve",
            yaxis_title="fill to close, $",
            showlegend=False,
        )
        _plot(fig, height=300)
        legs = (
            f" across {mark['fill_count']} filled leg(s)" if mark["fill_count"] else ""
        )
        st.caption(
            "$" + f"{mark['total']:+,.2f}" + legs
            + f" on {mark['session']}: shares traded x (close - fill), sells "
            + "negative, before turnover cost. This is the daily email's "
            + "Execution P&L definition, so the two figures agree. One day, "
            + "filled legs only: not a period return."
        )
    elif attr_rows:
        st.info("No fill-to-close mark is recorded for the latest run.")
    else:
        st.info("No live_attribution rows yet -- they populate after an execution run.")

    # ---------------------------------------------------------------- Signal history
    # Last block on the page, collapsed: eight small price-vs-trend charts with
    # the stored book weight, from 120 sessions before the first live session.
    render_signal_history()
