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
    fetch_stop_states,
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
def _get_stop_states() -> list[dict]:
    """Fetch v9.1 stop-ladder state cache from Supabase (advisory mode only)."""
    return fetch_stop_states()


@st.cache_data(ttl=3600)
def _get_mtm_equity() -> tuple[pd.Series, pd.Series, float] | None:
    """Compute mark-to-market equity curve from signal weights + close prices.

    Returns (equity_mtm, daily_pnl_mtm, gmv_latest) or None if data unavailable.
    Wrapped broadly: Render's web service has no yfinance cache on disk, and
    the cron services write to Supabase, not the web filesystem.
    """
    try:
        from signals.etf_universe import load_universe_close
        from signals.trend_signal import (
            apply_rebalance_control,
            compute_trend,
            shift_to_next_day,
            to_position_matrix,
        )
        from backtest.multi_asset import run_multi_asset
        from execution.costs import CostParams

        close = load_universe_close()
        tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
        desired = to_position_matrix(tidy)
        held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
        target = shift_to_next_day(held)

        result = run_multi_asset(target, close, notional=1_000_000.0, cost_params=CostParams())

        latest_target = target.iloc[-1]
        gmv = float(latest_target.abs().sum() * 1_000_000.0)

        return result.equity, result.daily_pnl, gmv
    except Exception:
        return None


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


def _render_panel_m(nav: float, proposed_rows: list[dict]) -> None:
    """Render Panel M: Live Risk & Attribution (v9.1 T9).

    Three blocks:
      1. MCTR/PCTR bar chart from live weights + 63d covariance
      2. P&L contribution table (1d / 5d / since-entry)
      3. Factor attribution (beta_explained vs residual, rolling R²)
    """
    import numpy as np
    from signals.etf_universe import UNIVERSE, load_universe_close

    # --- Block 1: MCTR/PCTR ---
    positions = fetch_positions(latest_only=True)
    if not positions:
        st.info("No live positions — MCTR/PCTR requires open positions.")
        return

    # Build live weight vector
    w_dict: dict[str, float] = {}
    for pos in positions:
        t = pos["ticker"]
        notional = float(pos.get("signed_notional", 0))
        w_dict[t] = notional / nav if nav > 0 else 0.0

    live_tickers = [t for t in UNIVERSE if t in w_dict]
    if not live_tickers:
        st.info("No positions in universe — MCTR/PCTR skipped.")
        return

    weights = pd.Series({t: w_dict[t] for t in live_tickers})

    # Get 63d returns from cached closes
    try:
        close = load_universe_close()
    except Exception:
        st.info("Close data not available — MCTR/PCTR skipped.")
        return

    rets = close[live_tickers].pct_change().dropna(how="all").tail(63)
    if len(rets) < 20:
        st.info(f"Only {len(rets)} days of returns — need ≥20 for covariance estimate.")
        return

    from risk.live_risk import mctr_pctr
    try:
        risk_df = mctr_pctr(weights, rets)
    except Exception as exc:
        st.warning(f"MCTR/PCTR computation failed: {exc}")
        return

    # MCTR/PCTR bar chart
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

    colors = ["#2ecc71" if w > 0 else "#e74c3c" for w in risk_df["weight"]]
    ax1.bar(risk_df.index, risk_df["pctr"] * 100, color=colors, alpha=0.8)
    ax1.axhline(0, color="black", lw=0.5)
    ax1.set_title("PCTR (% of Total Risk)")
    ax1.set_ylabel("%")
    ax1.tick_params(axis="x", rotation=45)

    ax2.bar(risk_df.index, risk_df["mctr"], color=colors, alpha=0.8)
    ax2.axhline(0, color="black", lw=0.5)
    ax2.set_title("MCTR (Annualized Marginal Contribution)")
    ax2.set_ylabel("Annualized σ")
    ax2.tick_params(axis="x", rotation=45)

    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    with st.expander("MCTR/PCTR detail"):
        st.dataframe(
            risk_df.style.map(
                lambda v: "color: #2ecc71" if v > 0 else "color: #e74c3c",
                subset=["weight", "pctr", "mctr"],
            ),
            width="stretch",
        )
        st.caption(f"PCTR sum: {risk_df['pctr'].sum():.6f} (must = 1.0 within 1e-6)")

    # --- Block 2: P&L contribution ---
    st.markdown("**P&L Contribution (from pnl_log)**")
    pnl_rows = fetch_pnl_log(limit=30)
    if pnl_rows:
        df_pnl = pd.DataFrame(pnl_rows).sort_values("trade_date")
        latest = df_pnl.iloc[-1] if len(df_pnl) > 0 else None
        if latest is not None:
            st.metric("Latest Daily P&L", f"${latest.get('net_pnl', 0):,.0f}")
        st.caption("Per-ticker contribution requires live_attribution data (v8.6+).")
    else:
        st.info("No P&L log data yet.")

    # --- Block 3: Factor attribution (full-history reconstruction) ---
    st.markdown("**Factor Attribution (betas from full-history reconstruction)**")
    try:
        from risk.attribution import factor_returns, rolling_factor_regression

        # Reconstruct book returns from signal weights over full history
        from signals.trend_signal import (
            apply_rebalance_control,
            compute_trend,
            shift_to_next_day,
            to_position_matrix,
        )
        tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
        desired = to_position_matrix(tidy)
        held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
        target = shift_to_next_day(held)

        # Book daily return = sum_i(target_i * ret_i)
        common_cols = [c for c in close.columns if c in target.columns]
        common_idx = target.index.intersection(close.index)
        t_aligned = target.loc[common_idx, common_cols]
        c_aligned = close.loc[common_idx, common_cols]
        ret_aligned = c_aligned.pct_change()
        book_ret = (t_aligned * ret_aligned).sum(axis=1, skipna=True)

        # Factor returns: eq=SPY, rates=IEF, credit=HYG-IEF, gold=GLD
        factor_tickers = ["SPY", "IEF", "HYG", "GLD"]
        available_factors = [f for f in factor_tickers if f in close.columns]
        if len(available_factors) >= 2:
            factor_rets = pd.DataFrame({
                f: close[f].pct_change() for f in available_factors
            }, index=close.index).dropna()

            # Rolling factor regression: 252d window through t-1
            reg_result = rolling_factor_regression(
                book_ret, factor_rets, window=252,
            )
            betas = reg_result.betas
            r2 = reg_result.r_squared
            explained = reg_result.beta_explained
            residual = reg_result.residual

            if betas is not None and len(betas) > 0:
                latest_betas = betas.iloc[-1] if len(betas) > 0 else None
                if latest_betas is not None:
                    st.markdown("**Latest factor betas (252d rolling, through t-1):**")
                    beta_display = pd.DataFrame({
                        "factor": latest_betas.index,
                        "beta": [f"{b:.3f}" for b in latest_betas.values],
                    })
                    st.dataframe(beta_display, hide_index=True, width="stretch")

                # Rolling R²
                if r2 is not None and len(r2) > 0:
                    fig_r2, ax_r2 = plt.subplots(figsize=(10, 3))
                    ax_r2.plot(r2.index, r2.values, lw=1.0, color="#1b5e8a")
                    ax_r2.set_title("Rolling R² (252d window)")
                    ax_r2.set_ylabel("R²")
                    ax_r2.set_ylim(0, 1)
                    ax_r2.grid(alpha=0.25)
                    fig_r2.tight_layout()
                    st.pyplot(fig_r2, width="stretch")
                    plt.close(fig_r2)

                st.caption("Betas from full-history signal reconstruction, not the days-old live series.")
            else:
                st.info("Factor regression produced no results.")
        else:
            st.info(f"Need ≥2 factor tickers available; got {available_factors}.")
    except Exception as exc:
        st.info(f"Factor attribution unavailable: {exc}")


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
        drifted_tickers = ", ".join(
            f"{t} (cached ${d['cached']:,.0f} vs live ${d['live']:,.0f})"
            for t, d in drift["detail"].items()
        )
        st.error(
            f"⚠️ Position drift detected at {drift['detected_at']}: the cached "
            f"position snapshot disagreed with live Alpaca before that run "
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

    if not df_trade.empty and "action" in df_trade.columns:
        st.dataframe(
            df_trade.style.map(
                lambda v: "color: green" if v == "buy"
                else ("color: red" if "sell" in str(v) else "color: grey"),
                subset=["action"],
            ),
            width="stretch",
            hide_index=True,
        )
    elif not df_trade.empty:
        st.dataframe(df_trade, width="stretch", hide_index=True)

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

    # ================================================================
    # Panel M -- Live Risk & Attribution (v9.1 T9)
    # ================================================================
    st.markdown("### M - Live Risk & Attribution")

    with st.spinner("Computing live risk decomposition..."):
        try:
            _render_panel_m(nav, proposed_rows)
        except Exception as exc:
            st.warning(f"Panel M unavailable: {exc}")

    st.markdown("---")

    # ---------------------------------------------------------------- Panel I: equity curve
    st.markdown("### I - Equity Curve — Realized + Mark-to-Market")

    pnl_rows = fetch_pnl_log()
    mtm_data = _get_mtm_equity()

    # --- GMV & NAV summary row ---
    col_gmv, col_nav, col_unreal = st.columns(3)
    if mtm_data is not None:
        _, _, gmv_latest = mtm_data
        col_gmv.metric("GMV (deployed capital)", f"${gmv_latest:,.0f}",
                        help="Sum of absolute position notionals — total capital at risk")
    else:
        col_gmv.metric("GMV", "—")
    col_nav.metric("Live NAV (Alpaca)", f"${nav:,.0f}",
                   help="Live account equity from Alpaca")
    # Unrealized P&L = live NAV - total invested (approximated from positions)
    positions_data = fetch_positions(latest_only=True)
    if positions_data:
        total_cost = sum(float(p.get("signed_notional", 0)) for p in positions_data)
        unrealized = nav - total_cost if total_cost != 0 else 0
        col_unreal.metric("Unrealized P&L (est.)", f"${unrealized:+,.0f}",
                          delta=f"{unrealized/total_cost*100:.1f}%" if total_cost != 0 else None)
    else:
        col_unreal.metric("Unrealized P&L", "—")

    # --- Equity curve chart ---
    if pnl_rows or mtm_data is not None:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, ax = plt.subplots(figsize=(14, 5))

        # Fills-based (realized) equity
        if pnl_rows:
            df_pnl = pd.DataFrame(pnl_rows).sort_values("trade_date")
            df_pnl["cumulative_net_pnl"] = df_pnl["net_pnl"].cumsum()
            ax.plot(pd.to_datetime(df_pnl["trade_date"]), df_pnl["cumulative_net_pnl"],
                    color="#1b5e8a", lw=2.0, label="Realized P&L (fills)", zorder=3)
            realized_last = df_pnl["cumulative_net_pnl"].iloc[-1]
            ax.scatter(pd.to_datetime(df_pnl["trade_date"].iloc[-1]), realized_last,
                       color="#1b5e8a", s=40, zorder=5)

        # MTM (strategy book) equity
        if mtm_data is not None:
            equity_mtm, _, _ = mtm_data
            # Align to recent window for readability (last ~2 years)
            recent = equity_mtm[equity_mtm.index >= "2024-01-01"]
            ax.plot(recent.index, recent.values,
                    color="#e67e22", lw=1.5, ls="--", alpha=0.85,
                    label="MTM strategy book (signal-based)", zorder=2)
            if len(recent) > 0:
                ax.scatter(recent.index[-1], recent.values[-1],
                           color="#e67e22", s=40, zorder=5)

        ax.axhline(0, color="black", lw=0.5)
        ax.set_title("Equity Curve — Realized Fills vs Mark-to-Market Strategy Book")
        ax.set_ylabel("Cumulative P&L ($)")
        ax.legend(loc="upper left")
        ax.grid(alpha=0.2)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        fig.autofmt_xdate()
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)
        st.caption(
            "**Blue** = realized P&L from executed Alpaca fills.  "
            "**Orange dashed** = mark-to-market P&L if the signal book were "
            "held continuously (backtest-style, v6.5 costs).  "
            "Differences come from execution timing, fill prices vs closes, "
            "and discrete trade rounding."
        )
        st.caption(FRAMING_CAPTION)
    else:
        st.warning(
            "No equity data yet — both pnl_log and close data are empty. "
            "Run the signal & execution crons to populate."
        )

    st.markdown("---")

    # ---------------------------------------------------------------- Panel J: open positions
    st.markdown("### J - Open Positions")
    positions_data = fetch_positions()
    if positions_data:
        df_pos = pd.DataFrame(positions_data)

        # Add weight column (% of NAV) and GMV contribution
        if "signed_notional" in df_pos.columns and nav > 0:
            df_pos["weight %"] = (df_pos["signed_notional"].astype(float) / nav * 100).round(2)
            df_pos["|notional|"] = df_pos["signed_notional"].astype(float).abs()

        # Summary row
        total_gmv = df_pos["|notional|"].sum() if "|notional|" in df_pos.columns else 0
        net_exposure = df_pos["signed_notional"].astype(float).sum() if "signed_notional" in df_pos.columns else 0

        col1, col2, col3 = st.columns(3)
        col1.metric("GMV (gross deployed)", f"${total_gmv:,.0f}")
        col2.metric("Net exposure", f"${net_exposure:+,.0f}")
        col3.metric("Leverage", f"{total_gmv/nav:.2f}x" if nav > 0 else "—")

        st.dataframe(df_pos, width="stretch", hide_index=True)
        st.caption("Weights shown as % of live NAV. Long = positive, short = negative.")
    else:
        st.warning(
            "No open positions — positions table is empty. "
            "Run the execution cron to populate."
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
