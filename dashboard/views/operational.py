"""Operational panels for sprint v8.5: proposed trades, positions, P&L.

Panels H (proposed next trade with Approve/Reject) write decisions to the
Supabase 'decisions' table. Panels I-L (equity curve, open positions,
daily P&L, closed-trade log) are stubbed with TODO v8.6 markers -- the
live Alpaca fill/position feed is not connected in v8.5.

No live Alpaca calls are made from this module (U6 gate from the v8.5 PRD).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard.supabase_client import (
    fetch_decisions_for_date,
    fetch_pnl_log,
    fetch_positions,
    write_decision,
)

FRAMING_CAPTION = (
    "Historical P&L shown for 2007-2026. "
    "This sample contains one secular rate cycle. "
    "Results are not forward-looking."
)


@st.cache_data(ttl=300)
def _get_proposed_trade() -> tuple[dict, str]:
    """Compute the proposed target weights for today's signal.

    Uses data through yesterday's close only (no look-ahead). Returns a
    dict {ticker: proposed_target_weight} and the as-of date string.
    """
    from signals.etf_universe import load_universe_close, UNIVERSE
    from signals.trend_signal import (
        apply_rebalance_control,
        compute_trend,
        shift_to_next_day,
        to_position_matrix,
    )

    close = load_universe_close()
    as_of_date = str(close.index[-1].date())

    desired = to_position_matrix(
        compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    )
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    target = shift_to_next_day(held)

    # Most recent row is the proposed position for today
    latest_weights = target.iloc[-1].to_dict()
    return {t: latest_weights.get(t, 0.0) for t in UNIVERSE}, as_of_date


@st.cache_data(ttl=60)
def _get_current_positions_from_supabase() -> dict[str, float]:
    """Read current open positions from Supabase positions table.

    Returns {ticker: signed_notional}. Empty dict if no live session yet.
    """
    rows = fetch_positions()
    return {r["ticker"]: float(r["signed_notional"]) for r in rows}


def _compute_delta_notionals(
    target_weights: dict[str, float],
    current_notionals: dict[str, float],
    paper_nav: float = 100_000.0,
) -> dict[str, float]:
    """Return {ticker: delta_notional} for each universe name."""
    from signals.etf_universe import UNIVERSE
    deltas = {}
    for t in UNIVERSE:
        tw = target_weights.get(t) or 0.0
        if tw != tw:  # NaN
            tw = 0.0
        target_n = tw * paper_nav
        current_n = current_notionals.get(t, 0.0)
        deltas[t] = target_n - current_n
    return deltas


def render(user_email: str) -> None:
    """Render all operational panels."""

    # ---------------------------------------------------------------- Panel H: proposed next trade
    st.markdown("### H - Proposed Next Trade")

    with st.spinner("Computing today's signal..."):
        target_weights, as_of_date = _get_proposed_trade()
        current_notionals = _get_current_positions_from_supabase()
        delta_notionals = _compute_delta_notionals(target_weights, current_notionals)

    st.caption(
        f"As-of date (yesterday's close): **{as_of_date}**  --  "
        "No look-ahead: positions for tomorrow computed from data through this date only."
    )

    # Fetch any decisions already submitted for this date
    existing_decisions = {
        r["ticker"]: r for r in fetch_decisions_for_date(as_of_date)
    }

    rows = []
    for ticker, tw in sorted(target_weights.items()):
        if tw is None or tw != tw:
            tw = 0.0
        delta_n = delta_notionals.get(ticker, 0.0)
        if abs(delta_n) < 10.0:
            intent = "hold (no change)"
        elif delta_n > 0:
            intent = "buy"
        else:
            intent = "sell"
        rows.append({
            "ticker": ticker,
            "target_weight": round(tw, 4),
            "target_notional_$": round(tw * 100_000, 0),
            "delta_notional_$": round(delta_n, 0),
            "intent": intent,
        })

    df_trade = pd.DataFrame(rows)
    st.dataframe(df_trade, use_container_width=True, hide_index=True)

    st.markdown("**Approve or Reject each order:**")
    st.caption(
        "Decisions are written to the Supabase decisions table. "
        "The v8.6 mid-morning job reads these before submitting to Alpaca."
    )

    supabase_ok = os.environ.get("SUPABASE_SECRET_KEY", "") != ""

    if not supabase_ok:
        st.warning("Supabase credentials not configured -- decisions cannot be saved. "
                   "Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env and restart.")

    for row in rows:
        ticker = row["ticker"]
        delta_n = row["delta_notional_$"]

        if abs(delta_n) < 10.0:
            continue  # nothing to decide

        with st.container():
            cols = st.columns([2, 2, 1, 1, 3])
            cols[0].write(f"**{ticker}**")
            cols[1].write(f"delta: ${delta_n:,.0f}")
            cols[2].write(row["intent"])

            if ticker in existing_decisions:
                rec = existing_decisions[ticker]
                decision_label = rec["decision"]
                cols[3].success(decision_label) if decision_label == "APPROVE" else cols[3].error(decision_label)
                cols[4].caption(f"by {rec['approved_by']} on {rec['created_at'][:16]}")
            else:
                approve_key = f"approve_{ticker}_{as_of_date}"
                reject_key = f"reject_{ticker}_{as_of_date}"

                if cols[3].button("Approve", key=approve_key, disabled=not supabase_ok):
                    ok = write_decision(
                        signal_date=as_of_date,
                        decision="APPROVE",
                        ticker=ticker,
                        proposed_target_weight=float(row["target_weight"]),
                        proposed_delta_notional=float(delta_n),
                        approved_by=user_email,
                    )
                    if ok:
                        st.success(f"Approved {ticker} -- decision saved to Supabase.")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("Failed to write to Supabase. Check credentials.")

                if cols[4].button("Reject", key=reject_key, disabled=not supabase_ok):
                    ok = write_decision(
                        signal_date=as_of_date,
                        decision="REJECT",
                        ticker=ticker,
                        proposed_target_weight=float(row["target_weight"]),
                        proposed_delta_notional=float(delta_n),
                        approved_by=user_email,
                    )
                    if ok:
                        st.info(f"Rejected {ticker} -- decision saved to Supabase.")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("Failed to write to Supabase. Check credentials.")

    st.markdown("---")

    # ---------------------------------------------------------------- Panel I: equity curve (stub)
    st.markdown("### I - Net Equity Curve")
    st.warning(
        "**TODO v8.6**: Live equity curve from Alpaca paper fills. "
        "The fill feed is not connected in v8.5. "
        "This panel will show cumulative net P&L from the pnl_log Supabase table."
    )
    st.caption(FRAMING_CAPTION)

    # ---------------------------------------------------------------- Panel J: open positions (stub)
    st.markdown("### J - Open Positions")
    positions = fetch_positions()
    if positions:
        st.dataframe(pd.DataFrame(positions), use_container_width=True, hide_index=True)
    else:
        st.warning(
            "**TODO v8.6**: Open positions will be populated from the Alpaca "
            "paper account after the v8.5 smoke session. No live positions yet."
        )

    # ---------------------------------------------------------------- Panel K: daily P&L and drawdown (stub)
    st.markdown("### K - Daily P&L and Drawdown")
    st.warning(
        "**TODO v8.6**: Daily P&L and drawdown from Alpaca paper fills. "
        "The fill feed is not connected in v8.5. "
        "This panel will read from the pnl_log Supabase table."
    )
    st.caption(FRAMING_CAPTION)

    # ---------------------------------------------------------------- Panel L: closed trade log
    st.markdown("### L - Closed Trade Log")
    fills = fetch_pnl_log()
    if fills:
        st.dataframe(pd.DataFrame(fills), use_container_width=True, hide_index=True)
    else:
        st.warning(
            "**TODO v8.6**: Closed trade log will be populated after the first "
            "paper execution session. No fills recorded yet."
        )
