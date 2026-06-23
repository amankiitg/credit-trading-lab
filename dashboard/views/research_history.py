"""Research History tab — honest record of the v1-v6.6 programme.

Shows the full research arc: the HY/IG RV hypothesis, the backtest that
looked promising (Sharpe 0.59, 81% hit rate), the accounting audit that
corrected it (Sharpe 0.20), and the IC test that closed the programme
(49-51% hit rate = coin flip). All findings are pre-registered and
documented in sprints/v5-v6.6.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import streamlit as st

VERDICT_CAPTION = (
    "**Verdict: R1 FAIL.** The equity-credit lag is a real statistical effect "
    "but not a tradeable filter. All Tier-1 signal admissions were withdrawn "
    "after the v6.5 accounting audit. Programme closed at v6.6."
)

FRAMING_CAPTION = (
    "Backtest covers 2008-2026. Pre-registered falsification criteria. "
    "Results reflect corrected fixed-entry accounting (v6.5)."
)


@st.cache_data(ttl=3600)
def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bt  = pd.read_parquet("data/results/backtest_trades.parquet")
    rp  = pd.read_parquet("data/results/regime_performance.parquet")
    fa  = pd.read_parquet("data/results/failure_analysis.parquet")
    feat = pd.read_parquet("data/processed/features.parquet")
    bt["exit_fill_date"]  = pd.to_datetime(bt["exit_fill_date"])
    bt["entry_fill_date"] = pd.to_datetime(bt["entry_fill_date"])
    return bt, rp, fa, feat


def _fmt_dollar(ax):
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"${x/1e3:.0f}k"))


def _equity_curve(trades: pd.DataFrame) -> pd.Series:
    """Daily cumulative net P&L, P&L booked on exit date."""
    s = trades.groupby("exit_fill_date")["net_pnl"].sum()
    idx = pd.date_range(s.index.min(), s.index.max(), freq="B")
    return s.reindex(idx, fill_value=0).cumsum()


def render() -> None:
    bt, rp, fa, feat = _load()

    st.subheader("Research History — HY/IG Credit RV Programme (v1–v6.6)")
    st.info(VERDICT_CAPTION)
    st.caption(
        "Seven sprints, two accounting audits, one honest conclusion. "
        "The tabs below show what the data said at each stage."
    )

    st.markdown("---")

    # -------------------------------------------------------- R1: equity curves
    st.markdown("### R1 — Strategy A vs B: Equity Curves")
    st.caption(
        "Strategy A trades the HY/IG RV1 residual unconditionally. "
        "Strategy B gates on the equity-credit lag regime ('equity_first' days only). "
        "The thesis was that B would outperform A — it didn't."
    )

    strat_a = bt[bt["strategy"] == "A_no_filter"]
    strat_b = bt[bt["strategy"] == "B_equity_first"]

    eq_a = _equity_curve(strat_a)
    eq_b = _equity_curve(strat_b)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(eq_a.index, eq_a.values, label="Strategy A — unconditional (94 trades)",
            color="#1b5e8a", lw=1.5)
    ax.plot(eq_b.index, eq_b.values, label="Strategy B — equity_first only (14 trades)",
            color="#cc3300", lw=1.5)
    ax.axhline(0, color="black", lw=0.5)
    _fmt_dollar(ax)
    ax.set_title("Cumulative net P&L: Strategy A vs B (pre-registered A/B test)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    col1, col2, col3 = st.columns(3)
    col1.metric("Strategy A net P&L", f"${strat_a['net_pnl'].sum()/1e3:.0f}k")
    col2.metric("Strategy A hit rate", f"{(strat_a['net_pnl']>0).mean():.1%}")
    col3.metric("Strategy B net P&L", f"${strat_b['net_pnl'].sum()/1e3:.0f}k",
                delta=f"ΔSharpe = −0.41 (C27 FAIL)", delta_color="inverse")

    st.caption(
        "**C27 pre-registered gate:** ΔSharpe > 0 with bootstrap CI excluding 0. "
        "Observed: ΔSharpe = −0.41, CI [−0.82, −0.01]. "
        "Gating discards 85% of trades; lost diversification outweighs faster reversion."
    )
    st.caption(FRAMING_CAPTION)

    st.markdown("---")

    # -------------------------------------------------------- R2: accounting correction
    st.markdown("### R2 — v6.5 Accounting Audit: Corrected Sharpe")
    st.caption(
        "Strategy A's backtest Sharpe of 0.59 relied on rolling OLS residuals. "
        "The OLS intercept re-centres daily, absorbing the 2007-2026 rate cycle as phantom P&L. "
        "Fixed-entry accounting (parameters locked at trade entry) corrects this."
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Reported Sharpe (v5)", "0.591", help="Rolling OLS residual, not fixed-entry")
    col2.metric("Corrected Sharpe (v6.5)", "0.202", delta="−0.389", delta_color="inverse",
                help="Fixed-entry accounting: OLS α and β locked at entry date")
    col3.metric("R1 gate threshold", "≥ 0.5", help="Pre-registered falsification criterion")

    st.error(
        "**R1 FAIL.** Corrected Sharpe 0.202 < 0.5 gate. "
        "57% of gross P&L was OLS intercept re-centring (model drift), not market moves. "
        "RV2_A and RV3_A corrected P&L: −$13M and −$11M respectively (rate cycle absorption). "
        "All Tier-1 admissions withdrawn."
    )

    # Show the three signals side by side
    signals = {
        "RV1_A (HY/IG)":      {"reported": 0.591, "corrected": 0.202},
        "RV2_A (credit/rates)":{"reported": 0.693, "corrected": -0.108},
        "RV3_A (cross-term)": {"reported": 0.856, "corrected": -0.187},
    }
    df_signals = pd.DataFrame(signals).T
    df_signals.index.name = "Signal"
    df_signals.columns = ["Reported Sharpe (v5)", "Corrected Sharpe (v6.5)"]
    df_signals["Pass R1 (≥0.5)?"] = df_signals["Corrected Sharpe (v6.5)"].apply(
        lambda x: "FAIL" if x < 0.5 else "PASS"
    )
    st.dataframe(
        df_signals.style
        .format({"Reported Sharpe (v5)": "{:.3f}", "Corrected Sharpe (v6.5)": "{:.3f}"})
        .map(lambda v: "color: red; font-weight: bold" if v == "FAIL" else "",
             subset=["Pass R1 (≥0.5)?"]),
        hide_index=False,
    )

    st.markdown("---")

    # -------------------------------------------------------- R3: regime breakdown
    st.markdown("### R3 — Regime Breakdown (Strategy A)")
    st.caption(
        "The equity-credit lag (C22) is a real statistical effect — 67% faster mean-reversion "
        "on equity_first days (OLS residual). But it is not a tradeable filter: "
        "gating on equity_first gives lower Sharpe than trading unconditionally."
    )

    rp_a = rp[rp["strategy"] == "A_no_filter"].copy()
    rp_a["max_drawdown"] = rp_a["max_drawdown"].apply(lambda x: f"${x/1e3:.0f}k")
    rp_a["hit_rate"] = rp_a["hit_rate"].apply(lambda x: f"{x:.1%}")
    rp_a["sharpe"] = rp_a["sharpe"].apply(lambda x: f"{x:.2f}")
    rp_a = rp_a.rename(columns={
        "regime_classifier": "classifier",
        "regime_label": "regime",
        "sharpe": "Sharpe",
        "hit_rate": "hit rate",
        "n_trades": "trades",
        "max_drawdown": "max DD",
    })
    st.dataframe(
        rp_a[["classifier", "regime", "Sharpe", "hit rate", "trades", "max DD"]],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "equity_first Sharpe (0.64) < neither Sharpe (0.56) is not a meaningful difference. "
        "Strategy B (trades only equity_first) had just 14 trades and Sharpe 0.09 overall."
    )

    st.markdown("---")

    # -------------------------------------------------------- R4: IC test
    st.markdown("### R4 — v6.6 IC Test: Entry Signal Is a Coin Flip")
    st.caption(
        "After all Tier-1 signals failed the accounting audit, v6.6 tested Option B: "
        "the raw hy_ig z-score (no OLS hedging). Pre-registered C36 gate: hit rate > 52% at any horizon."
    )

    horizons = [5, 10, 20]
    hit_rates = [49.6, 50.7, 49.9]  # from v6.6 PRD pre-registered result

    fig, ax = plt.subplots(figsize=(7, 3))
    bars = ax.bar(
        [f"{h}d" for h in horizons], hit_rates,
        color=["#cc3300" if hr < 52 else "#2e7d32" for hr in hit_rates],
        width=0.5,
    )
    ax.axhline(52, color="black", lw=1.0, linestyle="--", label="C36 gate (52%)")
    ax.axhline(50, color="grey", lw=0.5, linestyle=":")
    ax.set_ylim(45, 57)
    ax.set_ylabel("Directional hit rate (%)")
    ax.set_title("hy_ig z-score: forward hit rate at 5 / 10 / 20 day horizons (v6.6, C36)")
    ax.legend(fontsize=9)
    for bar, hr in zip(bars, hit_rates):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f"{hr:.1f}%", ha="center", va="bottom", fontsize=10)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    st.error(
        "**C36 FAIL.** Hit rate 49.6 / 50.7 / 49.9% at 5 / 10 / 20d — statistically "
        "indistinguishable from 50% (coin flip). The 252-day rolling mean catches up "
        "to an I(1) level, driving z-score reversion — not actual price mean-reversion. "
        "Programme closed."
    )

    st.markdown("---")

    # -------------------------------------------------------- retained findings
    st.markdown("### What We Actually Learned")
    st.markdown(
        """
1. **Fixed-entry P&L accounting is mandatory.** Rolling OLS residuals cannot be marked to
   market unless α and β are locked at entry. Re-centring during the hold is not tradeable P&L.

2. **Rolling OLS residuals embed a drift artifact.** The intercept absorbs secular trends
   (the full 2007-2026 rate cycle), inflating backtest Sharpe by ~0.4 in this sample.

3. **Rolling z-scores on I(1) levels need IC tests, not just stationarity diagnostics.**
   A z-score can be stationary while having zero directional predictive content.

4. **The equity-credit lag effect is real but not a filter.**
   ~67% faster mean-reversion on equity_first days (OLS, C22) — but gating on it
   discards 85% of trades and incremental Sharpe is −0.41 (C27).

5. **When a backtest fails auditing, shut it down.** The v8 programme started fresh
   with a mechanical trend signal, honest no-edge framing, and live execution.
        """
    )
