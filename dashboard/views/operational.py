"""Operational panels for sprint v8.5: proposed trades, positions, P&L.

Panel H (proposed next trade): ONE approve/reject decision per day for the
whole book. The cron job in v8.6 reads the decisions table each morning and
executes only when decision = 'approve'.

Panels I-L are stubbed with TODO v8.6 markers -- the live Alpaca fill and
position feed is not connected in v8.5.

No live Alpaca calls are made from this module (U6 gate from the v8.5 PRD).
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from dashboard.supabase_client import (
    fetch_decision_for_date,
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
def _get_proposed_trade() -> tuple[list[dict], str]:
    """Compute today's proposed target weights using yesterday's close.

    No look-ahead: close.index[-1] is yesterday's closing date.
    Returns (rows, as_of_date) where rows is a list of per-ticker dicts.
    """
    from signals.etf_universe import UNIVERSE
    from signals.trend_signal import (
        apply_rebalance_control,
        compute_trend,
        shift_to_next_day,
        to_position_matrix,
    )
    from signals.etf_universe import load_universe_close

    close = load_universe_close()
    as_of_date = str(close.index[-1].date())

    desired = to_position_matrix(
        compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    )
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    target = shift_to_next_day(held)
    weights = target.iloc[-1]  # proposed weights for the next trading day

    rows = []
    for ticker in UNIVERSE:
        w = float(weights.get(ticker) or 0.0)
        if w != w:
            w = 0.0
        notional = w * 100_000.0
        side = "long" if w > 0 else ("short" if w < 0 else "flat")
        rows.append({
            "ticker": ticker,
            "weight": round(w, 4),
            "notional ($)": round(notional, 0),
            "side": side,
        })
    return rows, as_of_date


def render(user_email: str) -> None:
    """Render all operational panels."""

    # ---------------------------------------------------------------- Panel H
    st.markdown("### H - Proposed Next Trade")

    with st.spinner("Computing today's signal..."):
        proposed_rows, as_of_date = _get_proposed_trade()

    st.caption(
        f"As-of date (yesterday's close): **{as_of_date}** -- "
        "No look-ahead: positions for tomorrow computed from data through this date only."
    )

    df_trade = pd.DataFrame(proposed_rows)
    st.dataframe(df_trade, use_container_width=True, hide_index=True)

    # Check existing decision for today
    existing = fetch_decision_for_date(as_of_date)
    supabase_ok = bool(os.environ.get("SUPABASE_SECRET_KEY"))

    st.markdown("**Approve or reject ALL trades for today:**")
    st.caption(
        "One decision covers the entire book. "
        "The v8.6 cron reads this each morning before executing."
    )

    if existing:
        if existing == "approve":
            st.success(f"Decision: APPROVE (recorded for {as_of_date})")
        else:
            st.error(f"Decision: REJECT (recorded for {as_of_date})")
        st.caption("To change the decision, click the opposite button below.")

    if not supabase_ok:
        st.warning(
            "Supabase credentials not configured -- decisions cannot be saved. "
            "Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env and restart."
        )

    col_approve, col_reject = st.columns(2)

    if col_approve.button(
        "Approve all trades",
        type="primary",
        disabled=not supabase_ok,
        key=f"approve_{as_of_date}",
    ):
        ok = write_decision(as_of_date, "approve")
        if ok:
            st.success("Decision saved: APPROVE")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("Failed to write to Supabase. Check credentials and table schema.")

    if col_reject.button(
        "Reject / skip today",
        disabled=not supabase_ok,
        key=f"reject_{as_of_date}",
    ):
        ok = write_decision(as_of_date, "reject")
        if ok:
            st.info("Decision saved: REJECT")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("Failed to write to Supabase. Check credentials and table schema.")

    st.markdown("---")

    # ---------------------------------------------------------------- Panel I: equity curve (stub)
    st.markdown("### I - Net Equity Curve")
    pnl_rows = fetch_pnl_log()
    if pnl_rows:
        df_pnl = pd.DataFrame(pnl_rows).sort_values("trade_date")
        df_pnl["cumulative_net_pnl"] = df_pnl["net_pnl"].cumsum()

        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(pd.to_datetime(df_pnl["trade_date"]), df_pnl["cumulative_net_pnl"],
                color="#1b5e8a", lw=1.5)
        ax.axhline(0, color="black", lw=0.5)
        ax.set_title("Cumulative net P&L (from paper fills)")
        ax.set_ylabel("Cumulative net P&L ($)")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        st.caption(FRAMING_CAPTION)
    else:
        st.warning(
            "**TODO v8.6**: Net equity curve from paper fills. "
            "No fills recorded yet -- pnl_log table is empty."
        )

    st.markdown("---")

    # ---------------------------------------------------------------- Panel J: open positions
    st.markdown("### J - Open Positions")
    positions = fetch_positions()
    if positions:
        df_pos = pd.DataFrame(positions)
        st.dataframe(df_pos, use_container_width=True, hide_index=True)
    else:
        st.warning(
            "**TODO v8.6**: Open positions will be populated from Alpaca "
            "after the first live paper execution session."
        )

    st.markdown("---")

    # ---------------------------------------------------------------- Panel K: daily P&L table
    st.markdown("### K - Daily P&L Log")
    if pnl_rows:
        df_log = pd.DataFrame(pnl_rows)
        st.dataframe(
            df_log[["trade_date", "gross_pnl", "net_pnl", "turnover_cost", "borrow_cost"]],
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            f"Net P&L total: ${df_log['net_pnl'].sum():,.0f}  |  "
            f"Turnover cost total: ${df_log['turnover_cost'].sum():,.0f}"
        )
        st.caption(FRAMING_CAPTION)
    else:
        st.warning(
            "**TODO v8.6**: Daily P&L log from paper fills. "
            "No fills recorded yet -- pnl_log table is empty."
        )
