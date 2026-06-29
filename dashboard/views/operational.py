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
    get_auto_approve,
    get_setting,
    set_auto_approve,
    write_decision,
)

FRAMING_CAPTION = (
    "Historical P&L shown for 2007-2026. "
    "This sample contains one secular rate cycle. "
    "Results are not forward-looking."
)


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


def render(
    user_email: str,
    is_authenticated: bool = False,
    secrets_configured: bool = False,
) -> None:
    """Render all operational panels. Auth is only required for approve/reject."""

    # ---------------------------------------------------------------- Panel H
    st.markdown("### H - Proposed Next Trade")

    with st.spinner("Computing today's signal..."):
        proposed_rows, as_of_date, nav = _get_proposed_trade()

    if as_of_date == "—" or not proposed_rows:
        st.warning("Signal not yet available — run_signal cron has not fired today.")
    st.caption(f"Signal as-of: {as_of_date}  |  NAV: ${nav:,.0f}  |  Delta = target minus current position")

    df_trade = pd.DataFrame(proposed_rows)
    st.dataframe(
        df_trade.style.map(
            lambda v: "color: green" if v == "buy"
            else ("color: red" if "sell" in str(v) else "color: grey"),
            subset=["action"],
        ),
        width="stretch",
        hide_index=True,
    )

    # ---- approve / reject: requires sign-in ----
    supabase_ok = bool(os.environ.get("SUPABASE_SECRET_KEY"))

    if not is_authenticated and secrets_configured:
        st.info("Sign in to approve or reject today's trades.")
        if st.button("Sign in with Google", key="signin_btn"):
            st.login("google")
        st.markdown("---")
        # skip decision/auto-approve UI for unauthenticated visitors
    else:
        existing = fetch_decision_for_date(as_of_date)

        auto_approve = get_auto_approve()
        new_val = st.toggle(
            "Auto-approve: execute every day unless I explicitly reject",
            value=auto_approve,
            disabled=not supabase_ok,
            help="When ON, the v8.6 cron runs each morning without needing a daily approval. "
                 "Turn OFF to require an explicit approve each day.",
        )
        if new_val != auto_approve:
            set_auto_approve(new_val)
            if new_val:
                st.success("Auto-approve ON -- trades will execute daily unless you reject.")
            else:
                st.info("Auto-approve OFF -- you must approve each morning to trade.")

        if auto_approve:
            st.caption(
                "Cron logic: execute unless `decision = reject` for today. "
                "No row or `decision = approve` both trigger execution."
            )
        else:
            st.caption(
                "Cron logic: execute only if `decision = approve` for today. "
                "No row or `decision = reject` both skip execution."
            )

        st.markdown("**Today's decision:**")

        if not supabase_ok:
            st.warning(
                "Supabase credentials not configured -- decisions cannot be saved. "
                "Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env and restart."
            )

        if existing == "approve":
            st.success(f"Approved for {as_of_date} -- trades will execute at next cron run.")
            if st.button("Change to: Reject / skip today", disabled=not supabase_ok, key=f"reject_{as_of_date}"):
                if write_decision(as_of_date, "reject"):
                    st.rerun()

        elif existing == "reject":
            st.error(f"Rejected for {as_of_date} -- no trades will execute.")
            if st.button("Change to: Approve all trades", type="primary", disabled=not supabase_ok, key=f"approve_{as_of_date}"):
                if write_decision(as_of_date, "approve"):
                    st.rerun()

        else:
            col_approve, col_reject = st.columns(2)
            if col_approve.button("Approve all trades", type="primary", disabled=not supabase_ok, key=f"approve_{as_of_date}"):
                if write_decision(as_of_date, "approve"):
                    st.rerun()
            if col_reject.button("Reject / skip today", disabled=not supabase_ok, key=f"reject_{as_of_date}"):
                if write_decision(as_of_date, "reject"):
                    st.rerun()

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
        st.pyplot(fig, width="stretch")
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
        st.dataframe(df_pos, width="stretch", hide_index=True)
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
            width="stretch",
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
