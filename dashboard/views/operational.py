"""Operational panels for the trade-approval dashboard -- sprint v9.7.

One page, five panels. Every panel opens with the sentence that says what it tells
you and what the number should look like when nothing is wrong, because a bare
number with no reference point cannot be read.

  H      Proposed next trade, stop-state advisory, and the approve/reject control.
         The only interactive panel; the cron reads the same decisions row.
  Exposure  Net and gross/leverage for the live book against the stored target.
  K      Account value over time, and turnover cost per run on its own chart.
  L      The book's P&L by sleeve, carry versus price, since the first recorded day.
  M-A    Share of the book's total volatility, by sleeve.
  M-B    The run's three dollars: book P&L, turnover cost, fill-day mark by sleeve.

Visual system, applied the same way in every panel:

  - Four fixed sleeve colours (SLEEVE_COLORS). A sleeve is always the same colour,
    and the colours are deliberately not green or red so a category cannot read as
    good or bad.
  - Green means good or on target; red means a flag or a breach, and nothing else.
    Neither ever encodes position sign: a short is not a problem and a long is not
    good news.
  - Long vs short is one treatment everywhere: solid fill for long, hatched for
    short, with the word in the label (SHORT_HATCH / LONG_SHORT_NOTE).

No live Alpaca calls are made from this module (U6 gate from the v8.5 PRD).
"""

from __future__ import annotations

import os

import pandas as pd
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
# Sleeve colours: fixed, one per sleeve, identical in every panel that shows a
# sleeve. Not green and not red, because those two carry meaning here (see below).
SLEEVE_COLORS: dict[str, str] = {
    "equity":    "#4c78a8",  # blue
    "rates":     "#f2a93b",  # amber
    "credit":    "#8e6cae",  # purple
    "commodity": "#5fa8a0",  # teal
}
SLEEVE_ORDER: tuple[str, ...] = ("equity", "rates", "credit", "commodity")

# Meaning is carried by st.success (green: good or on target) and st.error (red: a
# flag or a breach). Those two are the only green and red on the page, they never
# encode long vs short, and nothing else may use them. NEUTRAL_COLOR is for lines and
# bars that carry no judgement at all.
NEUTRAL_COLOR = "#8a8a8a"

# One long/short treatment everywhere a signed position is drawn.
SHORT_HATCH = "//"
LONG_SHORT_NOTE = "solid = long, hatched = short"

# Reference points, from the spec. Display thresholds only, not enforced limits:
# the execution layer's own limits are G_MAX_DEFAULT (gross) and the delta band.
NET_BAND_PCT = 5.0        # |net| above 5% of NAV is off target
GROSS_FLAG_X = 2.05       # above this is a mark-to-market overshoot between runs
GROSS_FLOOR_X = 1.80      # the floor observed across the recorded snapshots
RISK_TICKER_FLAG_PCT = 40.0   # one position above 40% of book risk is concentration
RISK_SLEEVE_FLAG_PCT = 60.0   # one sleeve above 60% of book risk is concentration

# ---------------------------------------------------------------- decision window
#
# The approve/reject buttons are shown only while a decision can still change what
# runs. The gate is the execution run's own record in cron_runs, never the clock: the
# cron can fire late, and a scheduled time that has passed says nothing about whether
# the run has happened.
EXECUTION_JOB = "run_execution"

# Context only, never a gate. render.yaml schedules credit-lab-execution at
# "30 14 * * 1-5" UTC.
EXECUTION_CRON_UTC = "14:30 UTC"

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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


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

    `last_execution_run_date` is the newest run_execution row in cron_runs, or None.
    Dates here are ISO strings, so they compare chronologically as strings.

    The "already acted on" test is `run > as_of`. A proposal computed from the close
    of day A is acted on by the first run after it, which has a run_date later than A;
    the run on day A itself acted on the previous proposal. That is why the normal
    morning (last run == as_of) stays open, and why a weekend stays open: the run that
    will act on a Friday signal is Monday's, which has not happened yet.
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
    """What today's execution run did, for the locked state.

    The filled legs are the direct record of what traded. If that write is missing
    (the run filled but the attribution write failed) the pnl_log row still carries
    the day's traded notional, so the fallback reports what it can instead of
    claiming nothing traded.
    """
    if decision == "reject":
        st.error(f"Skipped, you rejected: nothing traded on the {as_of_date} signal.")
        return

    legs = [r for r in attr_rows if str(r.get("run_date")) == today]
    if legs:
        tickers = ", ".join(sorted({str(r.get("ticker")) for r in legs}))
        mark = sum(float(r.get("net_pnl") or 0.0) for r in legs)
        st.success(
            f"Traded: {len(legs)} leg(s) filled today on the {as_of_date} signal "
            f"({tickers}). Fill-day mark ${mark:+,.2f}."
        )
        return

    # pnl_log.gross_pnl is the day's traded notional, not a P&L.
    traded_notional = float(pnl_row.get("gross_pnl") or 0.0) if pnl_row else 0.0
    if traded_notional > 0:
        cost = float(pnl_row.get("turnover_cost") or 0.0) if pnl_row else 0.0
        st.success(
            f"Traded: ${traded_notional:,.2f} of notional today, turnover cost "
            f"${cost:,.2f}. Leg-level detail was not recorded for this run."
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
    """Hatch a short, leave a long solid. The one long/short treatment."""
    if signed is None:
        return None
    return SHORT_HATCH if float(signed) < 0 else None


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
    """Current and target net exposure per sleeve, in dollars.

    Current comes from the position snapshot; target from the weights the signal
    stored for the current as-of date, which are the same weights the execution cron
    sizes from. So the target is what the book is aiming at, not a convention.
    """
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


@st.cache_data(ttl=300)
def _get_stop_states() -> list[dict]:
    """Fetch v9.1 stop-ladder state cache from Supabase (advisory mode only)."""
    return fetch_stop_states()


# The three readers below were called straight from render(), so every rerun did a
# Supabase round trip: every widget change and tab switch, and every tab renders on
# each script run. They are display reads, so a 300s TTL matches the caching the
# proposed-trade panel already uses and removes the repeated network work and the
# transient frames it allocates.
@st.cache_data(ttl=300)
def _get_positions() -> list[dict]:
    """Position snapshot for the exposure block and the risk panels."""
    return fetch_positions(latest_only=True)


@st.cache_data(ttl=300)
def _get_pnl_log() -> list[dict]:
    """The run rows the account-value and turnover-cost charts draw.

    The reader's default limit of 60 silently dropped the earliest runs, so a chart
    captioned as history was not the history. 500 covers what is recorded with room
    to grow, and the panel's caption states how many runs it actually drew.
    """
    return fetch_pnl_log(limit=500)


@st.cache_data(ttl=300)
def _get_live_attribution(limit: int) -> list[dict]:
    """Live attribution rows, newest first. `limit` is part of the cache key."""
    return fetch_live_attribution(limit=limit)


@st.cache_data(ttl=300)
def _get_daily_sleeve_pnl() -> list[dict]:
    """Sleeve P&L rows, four per session, newest first.

    The whole history rather than a window: the chart is cumulative, so dropping the
    earliest sessions would change what the line says, not just how far back it draws.
    """
    return fetch_daily_sleeve_pnl()


@st.cache_data(ttl=60)
def _get_last_execution_run() -> dict | None:
    """The newest run_execution row in cron_runs, or None.

    Deliberately a shorter TTL than the display readers: this is the gate that hides
    the approve/reject buttons once the run has happened, and a five-minute cache
    would leave the buttons live for five minutes after the decision was settled.
    Sixty seconds still collapses the repeated reads within a single interaction.
    """
    return fetch_last_cron_run(EXECUTION_JOB)


@st.cache_data(ttl=300)
def _get_drift_alert() -> dict | None:
    """Position drift flagged by run_execution.py's check_position_drift.

    Set once per execution run: non-empty when the frozen Supabase position
    snapshot disagreed with live Alpaca positions before that run computed
    its deltas (e.g. a manual paper-account reset outside normal order
    flow). Cleared (empty string) by the same run when nothing drifted.
    """
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

    Uses frozen Supabase positions and NAV — the same values the execution
    cron will use — so what you approve is exactly what executes.
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


def _render_mctr_pctr(nav: float, positions_data: list[dict]) -> None:
    """Emit the plain-English header and the sleeve-coloured risk contribution view.

    Weights are the live book. The covariance is the last 63 sessions of the committed
    price cache, which ends well before the live book's date, so the panel states its
    window and says indicative only: a risk share measured against a window that does
    not cover the book is a shape, not a current number. The dashboard deliberately has
    no Alpaca keys (render.yaml), so it cannot refresh the prices itself.
    """
    from dashboard.loader import load_close_matrix
    from signals.etf_universe import UNIVERSE

    # Build live weight vector
    w_dict: dict[str, float] = {}
    for pos in positions_data:
        t = pos["ticker"]
        notional = float(pos.get("signed_notional", 0))
        w_dict[t] = notional / nav if nav > 0 else 0.0

    live_tickers = [t for t in UNIVERSE if t in w_dict]
    if not live_tickers:
        st.info("No positions in universe -- risk contribution needs an open book.")
        return

    weights = pd.Series({t: w_dict[t] for t in live_tickers})

    try:
        close = load_close_matrix()
    except Exception:
        st.info("Close data not available -- risk contribution skipped.")
        return

    rets = close[live_tickers].pct_change().dropna(how="all").tail(63)
    if len(rets) < 20:
        st.info(f"Only {len(rets)} days of returns -- need >=20 for a covariance.")
        return

    from risk.live_risk import mctr_pctr
    try:
        risk_df = mctr_pctr(weights, rets)
    except Exception as exc:
        st.warning(f"MCTR/PCTR failed: {exc}")
        return

    # Euler additivity holds for CTR (it sums to portfolio sigma), so a sleeve's share
    # of total risk is its summed CTR over that sigma. Summing MCTR would be wrong.
    port_vol = float(risk_df["ctr"].sum())
    if port_vol <= 0:
        st.info("Portfolio volatility is zero at these weights -- nothing to attribute.")
        return

    risk_df = risk_df.copy()
    risk_df["sleeve"] = [_sleeve_of(t) for t in risk_df.index]

    sleeve_tbl = risk_df.groupby("sleeve")[["weight", "ctr"]].sum()
    sleeve_tbl["pctr"] = sleeve_tbl["ctr"] / port_vol
    sleeve_tbl = sleeve_tbl.reindex([s for s in SLEEVE_ORDER if s in sleeve_tbl.index])
    if sleeve_tbl.empty:
        st.info("No sleeve could be attributed -- risk view skipped.")
        return

    window = f"{rets.index[0].date()} to {rets.index[-1].date()}"
    book_date = str(positions_data[0].get("trade_date", "—")) if positions_data else "—"

    ranked = sleeve_tbl["pctr"].sort_values(ascending=False)
    driver = str(ranked.index[0])
    offsets = [s for s in sleeve_tbl.index if float(sleeve_tbl.loc[s, "pctr"]) < 0]
    line = (
        f"**Share of the book's total volatility (annualized {port_vol * 100:.1f}%).** "
        f"{driver.capitalize()} carries {float(ranked.iloc[0]) * 100:.0f}% of the book's "
        f"risk"
    )
    if offsets:
        parts = " and ".join(
            f"{s} ({float(sleeve_tbl.loc[s, 'pctr']) * 100:.0f}%)" for s in offsets
        )
        line += f", with the {parts} sleeves offsetting it (they are the short side)"
    st.markdown(line + ".")
    st.caption(
        f"63-session covariance, {window}. **Indicative only**: the window ends "
        f"{rets.index[-1].date()} and the book is {book_date}, so this is the risk shape "
        f"of today's weights measured against a window that does not cover them. "
        f"Weights are the live book; the covariance is not."
    )

    ticker_flag = float(risk_df["pctr"].max())
    sleeve_flag = float(sleeve_tbl["pctr"].max())
    flags: list[str] = []
    if ticker_flag > RISK_TICKER_FLAG_PCT / 100:
        flags.append(
            f"{risk_df['pctr'].idxmax()} is {ticker_flag * 100:.0f}% of the book's risk "
            f"(over {RISK_TICKER_FLAG_PCT:.0f}%)"
        )
    if sleeve_flag > RISK_SLEEVE_FLAG_PCT / 100:
        flags.append(
            f"the {sleeve_tbl['pctr'].idxmax()} sleeve is {sleeve_flag * 100:.0f}% of it "
            f"(over {RISK_SLEEVE_FLAG_PCT:.0f}%)"
        )
    if flags:
        st.error("Concentration: " + "; ".join(flags) + ".")
    else:
        st.success(
            f"No single position above {RISK_TICKER_FLAG_PCT:.0f}% of book risk and no "
            f"sleeve above {RISK_SLEEVE_FLAG_PCT:.0f}%."
        )

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 3.2))
    for i, sleeve in enumerate(sleeve_tbl.index):
        val = float(sleeve_tbl.loc[sleeve, "pctr"]) * 100
        net_w = float(sleeve_tbl.loc[sleeve, "weight"])
        ax.bar(i, val, 0.6, color=SLEEVE_COLORS.get(sleeve, NEUTRAL_COLOR),
               hatch=_fill_for(net_w), edgecolor="white", linewidth=0.8)
        ax.text(i, val, f"{val:.0f}%", ha="center",
                va="bottom" if val >= 0 else "top", fontsize=9)
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xticks(range(len(sleeve_tbl)))
    ax.set_xticklabels([s.capitalize() for s in sleeve_tbl.index])
    ax.set_ylabel("% of total risk")
    ax.set_title("Share of book risk by sleeve")
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)
    st.caption(
        "Sleeve shares sum to 100%; a negative bar is a sleeve that reduces the book's "
        f"risk. Bar colour is the sleeve, {LONG_SHORT_NOTE}."
    )

    with st.expander("Per-ticker detail (weight, MCTR, CTR, PCTR)"):
        by_ticker = risk_df.sort_values("pctr", ascending=False)
        fig, ax = plt.subplots(figsize=(10, 3.2))
        for i, (ticker, row) in enumerate(by_ticker.iterrows()):
            val = float(row["pctr"]) * 100
            ax.bar(i, val, 0.6,
                   color=SLEEVE_COLORS.get(str(row["sleeve"]), NEUTRAL_COLOR),
                   hatch=_fill_for(float(row["weight"])), edgecolor="white",
                   linewidth=0.8)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xticks(range(len(by_ticker)))
        ax.set_xticklabels(list(by_ticker.index), rotation=45)
        ax.set_ylabel("% of total risk")
        ax.set_title("Share of book risk by ticker")
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)
        st.dataframe(
            risk_df[["sleeve", "weight", "mctr", "ctr", "pctr"]].round(4),
            width="stretch",
        )
        st.caption(
            f"PCTR sum: {risk_df['pctr'].sum():.6f} (must = 1.0 within 1e-6). MCTR is "
            f"annualized vol, PCTR is the share of the book's total. {LONG_SHORT_NOTE}."
        )


def render(
    user_email: str,
    is_authenticated: bool = False,
    secrets_configured: bool = False,
) -> None:
    """Render all operational panels. Auth is only required for approve/reject."""

    # ---------------------------------------------------------------- Panel H
    st.markdown("### H - Proposed Next Trade")

    drift = _get_drift_alert()
    if drift:
        # Values are share counts, not dollars: the check compares share counts so a
        # price move cannot raise an alert. Alerts written before that change have no
        # "basis" key and were dollar-based, so they are labelled as such.
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
            f"computed deltas — {drifted_tickers}. That run still executed "
            f"against the cached (pre-drift) snapshot as usual; the deltas "
            f"below now reflect the corrected, real positions."
        )

    with st.spinner("Loading today's stored signal and current positions..."):
        proposed_rows, as_of_date, nav = _get_proposed_trade()

    if as_of_date == "—" or not proposed_rows:
        st.warning("Signal not yet available — run_signal cron has not fired today.")
    st.caption(f"Signal as-of: {as_of_date}  |  NAV: ${nav:,.0f}  |  Delta = target minus current position")

    df_trade = pd.DataFrame(proposed_rows) if proposed_rows else pd.DataFrame()

    # --- v9.1 T7: risk-status column from stop_states ---
    stop_states_raw = _get_stop_states()
    stop_map: dict[str, dict] = {r["ticker"]: r for r in stop_states_raw} if stop_states_raw else {}
    advisory = any(r.get("advisory") for r in stop_states_raw) if stop_states_raw else True

    risk_columns = []
    non_normal: list[dict] = []
    for row in proposed_rows:
        t = row["ticker"]
        ss = stop_map.get(t, {})
        state = ss.get("state", "NORMAL")
        z_val = ss.get("z")
        is_advisory = ss.get("advisory", True)

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

    # Warning banner for non-NORMAL states (from proposed trades AND from live stop_states)
    prefix = "ADVISORY (not traded) — " if advisory else ""
    if non_normal:
        lines = []
        for nn in non_normal:
            z_info = f", z={nn['z']:.2f}" if nn['z'] is not None else ""
            if nn["state"] == "REDUCED":
                recovery = "z ≥ -1.0 restores NORMAL"
            else:
                recovery = "z ≥ -2.0 restores REDUCED"
            lines.append(f"**{nn['ticker']}**: {nn['state']}{z_info} → {recovery}")
        st.warning(
            f"{prefix}**{len(non_normal)} ticker(s) in drawdown:**\n\n" +
            "\n\n".join(lines)
        )
    elif stop_states_raw:
        # Also check stop_states for non-normal states not in proposed trade table
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
                if s == "REDUCED":
                    recovery = "z ≥ -1.0 restores NORMAL"
                else:
                    recovery = "z ≥ -2.0 restores REDUCED"
                lines.append(f"**{t}**: {s}{z_info} → {recovery}")
            st.warning(
                f"{prefix}**{len(live_non_normal)} ticker(s) in drawdown (live stop_states):**\n\n" +
                "\n\n".join(lines)
            )
        else:
            st.success(f"{prefix}All positions NORMAL — no stop states active.")

    # No colour on the action column: green and red mean on-target and breach in
    # this dashboard, and a buy is neither. Buy vs sell/short is in the words, and
    # the long/short treatment is the fill style, not a colour.
    if not df_trade.empty:
        st.dataframe(df_trade, width="stretch", hide_index=True)

    # ---- approve / reject: requires sign-in, and only while the window is open ----
    supabase_ok = bool(os.environ.get("SUPABASE_SECRET_KEY"))

    if not is_authenticated and secrets_configured:
        st.info("Sign in to approve or reject today's trades.")
        if st.button("Sign in with Google", key="signin_btn"):
            st.login("google")
        st.markdown("---")
        # skip decision/auto-approve UI for unauthenticated visitors
    elif not supabase_ok:
        # Without credentials the window cannot be read, so the panel will not claim to
        # know it, and a decision could not be written anyway.
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
            help="When ON, the v8.6 cron runs each morning without needing a daily approval. "
                 "Turn OFF to require an explicit approve each day.",
        )
        # The toggle is a standing setting, not a per-day decision, so it stays usable
        # in every window state. The copy below reports the stored value, and after a
        # failed write the stored value is the old one, so it is the effective value
        # that decides which sentence is true.
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
                "Cron logic: execute unless `decision = reject` for today. "
                "No row or `decision = approve` both trigger execution."
            )
        else:
            st.caption(
                "Cron logic: execute only if `decision = approve` for today. "
                "No row or `decision = reject` both skip execution."
            )

        st.caption(
            f"Scheduled execution: {EXECUTION_CRON_UTC} on weekdays. Whether this panel "
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
                    (r for r in _get_pnl_log() if str(r.get("trade_date")) == today),
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
                if st.button("Change to: Approve all trades", type="primary", key=f"approve_{as_of_date}"):
                    if write_decision(as_of_date, "approve"):
                        st.rerun()

            else:
                col_approve, col_reject = st.columns(2)
                if col_approve.button("Approve all trades", type="primary", key=f"approve_{as_of_date}"):
                    if write_decision(as_of_date, "approve"):
                        st.rerun()
                if col_reject.button("Reject / skip today", key=f"reject_{as_of_date}"):
                    if write_decision(as_of_date, "reject"):
                        st.rerun()

    st.markdown("---")

    # ================================================================
    # Exposure -- panels I and J, merged
    #
    # I and J each computed sum(|signed_notional|) / NAV, so the same number was on
    # screen twice under two names. Gross exposure IS leverage: one quantity, one
    # place. What is worth showing is the pair that says different things, net (how
    # directional the book is) and gross (how big it is), each against the stored
    # target rather than against nothing.
    # ================================================================
    st.markdown("### Exposure - current vs target")

    positions_data = _get_positions()
    from signals.trend_signal import G_MAX_DEFAULT

    snapshot_date = str(positions_data[0].get("trade_date", "—")) if positions_data else "—"
    net_usd = sum(float(p.get("signed_notional") or 0.0) for p in positions_data)
    gross_usd = sum(abs(float(p.get("signed_notional") or 0.0)) for p in positions_data)
    net_pct = net_usd / nav * 100 if nav else 0.0
    gross_x = gross_usd / nav if nav else 0.0
    target_net_pct = sum(float(r.get("target wt") or 0.0) for r in proposed_rows) * 100
    target_gross_x = sum(abs(float(r.get("target wt") or 0.0)) for r in proposed_rows)

    st.markdown(
        f"Long/short book. **Net near zero is the intent** (no directional bet); "
        f"**gross near {float(G_MAX_DEFAULT):.2f}x NAV is the intent** (full deployment). "
        f"Current: net {net_pct:+.1f}% of NAV vs target {target_net_pct:+.1f}%; "
        f"gross {gross_x:.2f}x vs target {target_gross_x:.2f}x. "
        f"Snapshot {snapshot_date}."
    )

    col_net, col_gross = st.columns(2)
    col_net.metric(
        "Net exposure",
        f"${net_usd:+,.0f}",
        delta=f"{net_pct:+.1f}% of NAV vs target {target_net_pct:+.1f}%",
        delta_color="off",
    )
    col_gross.metric(
        "Gross exposure (= leverage)",
        f"{gross_x:.2f}x NAV",
        delta=f"target {target_gross_x:.2f}x, hard cap {float(G_MAX_DEFAULT):.2f}x",
        delta_color="off",
    )

    off_target: list[str] = []
    if abs(net_pct) > NET_BAND_PCT:
        off_target.append(
            f"net is {net_pct:+.1f}% of NAV, outside the +/-{NET_BAND_PCT:.0f}% band"
        )
    if gross_x > GROSS_FLAG_X:
        off_target.append(
            f"gross is {gross_x:.2f}x, above the {GROSS_FLAG_X:.2f}x overshoot mark"
        )
    if gross_x < GROSS_FLOOR_X:
        off_target.append(
            f"gross is {gross_x:.2f}x, below the {GROSS_FLOOR_X:.2f}x floor"
        )
    if off_target:
        st.error("Off target: " + "; ".join(off_target) + ".")
    else:
        st.success(
            f"On target: net {net_pct:+.1f}% of NAV (band +/-{NET_BAND_PCT:.0f}%), "
            f"gross {gross_x:.2f}x (floor {GROSS_FLOOR_X:.2f}x, "
            f"overshoot mark {GROSS_FLAG_X:.2f}x)."
        )

    st.caption(
        f"{len(positions_data)} positions, market values as of the {snapshot_date} "
        f"snapshot. The target is the signal weights stored for {as_of_date}. "
        f"Leverage is the same number as gross: exposure per dollar of equity."
    )

    if positions_data:
        import matplotlib.pyplot as plt

        current, target = _sleeve_exposure(positions_data, proposed_rows, nav)
        fig, ax = plt.subplots(figsize=(10, 3.2))
        width = 0.38
        for i, sleeve in enumerate(SLEEVE_ORDER):
            cur = current[sleeve] / nav * 100 if nav else 0.0
            tgt = target[sleeve] / nav * 100 if nav else 0.0
            color = SLEEVE_COLORS[sleeve]
            ax.bar(i - width / 2, cur, width, color=color, hatch=_fill_for(cur),
                   edgecolor="white", linewidth=0.8)
            ax.bar(i + width / 2, tgt, width, color=color, alpha=0.35,
                   edgecolor=NEUTRAL_COLOR, linewidth=0.8)
            ax.text(i - width / 2, cur, f"{cur:+.0f}%", ha="center",
                    va="bottom" if cur >= 0 else "top", fontsize=8)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xticks(range(len(SLEEVE_ORDER)))
        ax.set_xticklabels([s.capitalize() for s in SLEEVE_ORDER])
        ax.set_ylabel("net exposure, % of NAV")
        ax.set_title("Net exposure by sleeve: current vs target")
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)
        st.caption(
            f"Full bar = the sleeve's current net exposure ({LONG_SHORT_NOTE}); "
            f"pale bar = the signal's target for {as_of_date}."
        )
    else:
        st.info("No positions yet -- the sleeve view appears after the first execution run.")

    st.markdown("---")

    # ================================================================
    # Panel K -- account value, and what trading cost
    #
    # The old panel plotted nav - cumulative(net_pnl), and pnl_log.net_pnl is minus the
    # day's turnover cost, so the "NAV" line was reconstructed out of costs: across the
    # whole recorded history it spans about $106 on a $102k account, so it read as flat,
    # and because cost only ever subtracts it sloped the wrong way while the account
    # rose. Equity now comes from pnl_log.live_nav, which run_execution writes each run,
    # and cost is charted as cost, on its own chart.
    # ================================================================
    st.markdown("### K - Account value and turnover cost")
    pnl_rows = _get_pnl_log()
    df_pnl = pd.DataFrame(pnl_rows).sort_values("trade_date") if pnl_rows else pd.DataFrame()
    equity_rows = (
        df_pnl[df_pnl["live_nav"].notna()]
        if "live_nav" in df_pnl.columns
        else pd.DataFrame()
    )

    st.markdown("**Account value over time.** The account's equity as recorded at each run.")
    if not equity_rows.empty:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        eq_dates = pd.to_datetime(equity_rows["trade_date"])
        eq_values = equity_rows["live_nav"].astype(float)
        fig, ax = plt.subplots(figsize=(14, 3))
        ax.plot(eq_dates, eq_values, color=NEUTRAL_COLOR, lw=2.0, marker="o", markersize=3)
        ax.set_ylabel("account equity, $")
        ax.set_title("Account value over time")
        ax.grid(alpha=0.2)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)
        st.caption(
            f"Last ${float(eq_values.iloc[-1]):,.2f} on "
            f"{str(equity_rows['trade_date'].iloc[-1])}. Recorded high "
            f"${float(eq_values.max()):,.2f}, low ${float(eq_values.min()):,.2f}, "
            f"over {len(equity_rows)} recorded run(s)."
        )
    else:
        st.info(
            "No account equity recorded yet. `pnl_log.live_nav` starts filling from the "
            "next execution run, and the chart then shows the account's real equity "
            "instead of a line rebuilt out of turnover costs."
        )

    if not df_pnl.empty and "turnover_cost" in df_pnl.columns:
        import matplotlib.pyplot as plt

        costs = df_pnl["turnover_cost"].fillna(0.0).astype(float)
        total_cost = float(costs.sum())
        st.markdown(
            f"**Turnover cost per run.** Total to date ${total_cost:,.2f} over "
            f"{len(df_pnl)} runs."
        )
        fig, ax = plt.subplots(figsize=(14, 2.2))
        ax.bar(pd.to_datetime(df_pnl["trade_date"]), costs, color=NEUTRAL_COLOR, width=0.8)
        ax.set_ylabel("$")
        ax.set_title("Turnover cost per run")
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)
        st.caption(
            "Cost is a drag on the account value above, not the result: a single-digit "
            "dollar run ($0 to $3, 0.00-0.02% of NAV) is normal. "
            "(`pnl_log.gross_pnl` is the day's traded notional, not a P&L.)"
        )
    else:
        st.info("No turnover-cost rows yet -- the cost chart appears after the first run.")

    st.markdown("---")

    # ================================================================
    # Panel L -- the book's P&L by sleeve, carry vs price
    #
    # Written by the signal cron, because it is the only job that runs after the close.
    # One row per sleeve per session, each carrying the session's book total, so the
    # reconciliation (the four sleeves sum to the book) is checkable from the table
    # itself and is restated in the caption below rather than taken on trust.
    # ================================================================
    st.markdown("### L - Book P&L by sleeve (carry vs price)")

    sleeve_rows = _get_daily_sleeve_pnl()
    if sleeve_rows:
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch

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
            "session, stacked, and the black line is the book's total."
        )

        xs = pd.to_datetime(list(price_cum.index))
        fig, ax = plt.subplots(figsize=(14, 4))
        stack = np.zeros(len(xs))
        for sleeve in SLEEVE_ORDER:
            colour = SLEEVE_COLORS[sleeve]
            price = price_cum[sleeve].to_numpy(dtype="float64")
            ax.fill_between(xs, stack, stack + price, color=colour, alpha=0.85,
                            linewidth=0)
            stack = stack + price
            carry = carry_cum[sleeve].to_numpy(dtype="float64")
            ax.fill_between(xs, stack, stack + carry, color=colour, alpha=0.45,
                            hatch=SHORT_HATCH, edgecolor="white", linewidth=0)
            stack = stack + carry

        total = book_cum.to_numpy(dtype="float64")
        ax.plot(xs, total, color="black", lw=1.6)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_ylabel("cumulative P&L, $")
        ax.set_title("Book P&L by sleeve: carry and price, stacked")
        ax.grid(alpha=0.2)
        handles = [
            Patch(facecolor=SLEEVE_COLORS[s], label=s.capitalize()) for s in SLEEVE_ORDER
        ]
        handles.append(Line2D([0], [0], color="black", lw=1.6, label="Book total"))
        ax.legend(handles=handles, fontsize=8, loc="upper left")
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)

        gap = float(total[-1] - stack[-1]) if len(total) else 0.0
        st.caption(
            f"Solid band = price move, hatched band = distributions received (carry). "
            f"Measured on the book held at the previous close, so a trade contributes "
            f"from the session after it. The four sleeves sum to the book total to "
            f"within ${abs(gap):,.6f} over {len(sessions)} session(s), which is the "
            f"gate the writer applies before storing a session at all."
        )
        if float(df_sleeve["carry_pnl"].abs().sum()) == 0.0:
            st.caption(
                "Carry is $0.00 in every recorded session: no distribution is recorded "
                "for those dates."
            )
    else:
        st.info(
            "No sleeve P&L recorded yet. The signal cron writes one row per sleeve per "
            "session, so this fills from its next run."
        )

    st.markdown("---")

    # ================================================================
    # Panel M-A -- risk contribution by sleeve
    # ================================================================
    st.markdown("### M-A - Risk contribution (MCTR/PCTR)")

    with st.spinner("Computing risk decomposition..."):
        if positions_data:
            _render_mctr_pctr(nav, positions_data)
        else:
            st.info("No positions -- risk contribution needs an open book.")

    st.markdown("---")

    # ================================================================
    # Panel M-B -- the run's three dollars, kept apart
    #
    # Three different quantities used to share one label. The daily email keeps book
    # P&L and turnover cost apart; this panel now does the same, and names the third
    # for what it is: the mark from fill to close on the legs filled that day, which
    # is not the book's result.
    # ================================================================
    st.markdown("### M-B - Today's result")

    last_run = df_pnl.iloc[-1] if not df_pnl.empty else None
    run_date = str(last_run["trade_date"]) if last_run is not None else "—"
    book_pnl = last_run.get("book_pnl") if last_run is not None else None
    cost_today = last_run.get("turnover_cost") if last_run is not None else None
    nav_at_run = last_run.get("live_nav") if last_run is not None else None

    col_book, col_cost = st.columns(2)

    if book_pnl is None or pd.isna(book_pnl):
        col_book.metric(
            "Book P&L (equity move since previous run)", "not recorded yet"
        )
    else:
        book_pct = (float(book_pnl) / float(nav_at_run) * 100) if nav_at_run else None
        col_book.metric(
            "Book P&L (equity move since previous run)",
            f"${float(book_pnl):+,.2f}",
            delta=f"{book_pct:+.2f}% of NAV" if book_pct is not None else None,
            delta_color="off",
        )

    if cost_today is None or pd.isna(cost_today):
        col_cost.metric("Turnover cost today", "—")
    else:
        col_cost.metric("Turnover cost today", _fmt_dollars(float(cost_today)))

    cost_total = (
        float(df_pnl["turnover_cost"].fillna(0.0).sum())
        if "turnover_cost" in df_pnl.columns
        else 0.0
    )
    st.caption(
        f"Book P&L is the account's equity move since the previous run, on the last run "
        f"({run_date}): tens to hundreds of dollars a day is ordinary noise on this "
        f"account. Turnover cost today is a drag on it, not part of it -- "
        f"${cost_total:,.2f} cumulative over {len(df_pnl)} runs, and a single-digit "
        f"dollar day ($0 to $3, 0.00-0.02% of NAV) is normal. The two are different "
        f"quantities and neither is derived from the other."
    )

    attr_rows = _get_live_attribution(60)
    if attr_rows:
        import matplotlib.pyplot as plt

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
            fig, ax = plt.subplots(figsize=(9, 2.8))
            bars = ax.bar(
                [s.capitalize() for s in mark.index], mark.values,
                color=[SLEEVE_COLORS.get(s, NEUTRAL_COLOR) for s in mark.index],
                alpha=0.9,
            )
            ax.axhline(0, color="black", lw=0.6)
            ax.set_ylabel("$")
            ax.set_title("Fill-to-close mark on the day's filled legs, by sleeve")
            for bar, val in zip(bars, mark.values):
                ax.text(bar.get_x() + bar.get_width() / 2, val, f"${val:+,.2f}",
                        ha="center", va="bottom" if val >= 0 else "top", fontsize=9)
            ax.grid(axis="y", alpha=0.2)
            fig.tight_layout()
            st.pyplot(fig, width="stretch")
            plt.close(fig)
            st.caption(
                f"${total_mark:+,.2f} across {len(df_latest)} filled leg(s) on "
                f"{attr_date}, net of their turnover cost. One day, filled legs only, so "
                f"it is not a period return and not the book's P&L: single digits to low "
                f"tens of dollars is normal here."
            )
        else:
            st.info("No attribution rows for the latest run.")
    else:
        st.info("No live_attribution rows yet -- they populate after an execution run.")
