"""Attribution Lab panels for sprint v8.5.

Reads data/processed/attribution.parquet (v8.3 daily attribution frame)
and data/processed/attribution_mctr_by_sleeve.parquet.

No edge claim. This is a forensic accounting layer on a carry-dominated book.
The factor regression residual is confounded with carry (Finding 3 from
sprints/v8.3/notes.md) and is NOT standalone alpha. Every P&L panel carries
the single-rate-cycle framing caption required by v8 House Rule 10.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import streamlit as st

FRAMING_CAPTION = (
    "Historical P&L shown for 2007-2026. "
    "This sample contains one secular rate cycle. "
    "Results are not forward-looking."
)

FINDING3_CAVEAT = (
    "**Finding-3 caveat (sprints/v8.3/notes.md):** "
    "The four daily-return factors (SPY, IEF, HYG-IEF, GLD) do not span "
    "carry accrual. Coupon income appears in the book's daily return but not "
    "in the factors, so the regression labels it 'unexplained.' "
    "**The residual is exposure-timing confounded with carry -- "
    "it is not standalone alpha, and it is not security selection** "
    "(House Rule 7)."
)

SLEEVE_COLORS = {
    "commodity": "#d4a017",
    "credit": "#cc3300",
    "rates": "#1b5e8a",
    "equity": "#2e7d32",
}


@st.cache_data(ttl=3600)
def _load_attribution() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load and pre-process the attribution frames. Cached for 1 hour."""
    attr = pd.read_parquet("data/processed/attribution.parquet")
    attr["date"] = pd.to_datetime(attr["date"])

    # Day-level broadcast columns (same value for all tickers on a given date)
    day_cols = [
        "gross_pnl", "net_pnl", "directional", "selection",
        "net_exposure", "beta_explained", "residual", "r_squared",
        "turnover_cost", "borrow_cost",
    ]
    daily = attr.groupby("date", as_index=False)[day_cols].mean()

    # Sleeve-level per-ticker columns
    sleeve = (
        attr.groupby(["date", "asset_class"], as_index=False)
        [["pnl", "carry", "price_change"]]
        .sum()
    )

    mctr = pd.read_parquet("data/processed/attribution_mctr_by_sleeve.parquet")
    mctr["date"] = pd.to_datetime(mctr["date"])

    return daily, sleeve, mctr


def _fmt_dollar(ax):
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"${x/1e3:.0f}k"))


def render() -> None:
    """Render all attribution panels in the current Streamlit context."""
    daily, sleeve, mctr = _load_attribution()

    # ---------------------------------------------------------------- header
    st.subheader("Attribution Lab -- carry-dominated book, single rate cycle")
    st.caption(
        "v8.3 forensic attribution on the v8.2 long/short ETF trend book. "
        "70% of gross P&L is carry (HYG/LQD coupons). "
        "GLD alone is 49% of gross P&L. "
        "Not a signal validation; not an edge claim."
    )

    # ---------------------------------------------------------------- Panel A: sleeve P&L
    st.markdown("### A - Sleeve P&L")
    sleeve_cum = (
        sleeve.pivot_table(index="date", columns="asset_class", values="pnl", aggfunc="sum")
        .sort_index()
        .fillna(0)
        .cumsum()
    )

    fig, ax = plt.subplots(figsize=(12, 4))
    for col in sleeve_cum.columns:
        ax.plot(sleeve_cum.index, sleeve_cum[col], label=col,
                color=SLEEVE_COLORS.get(col, "#888"))
    _fmt_dollar(ax)
    ax.set_title("Cumulative gross P&L by asset-class sleeve")
    ax.legend(fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    totals = sleeve_cum.iloc[-1].sort_values(ascending=False)
    st.caption(
        "  |  ".join(f"{k}: ${v/1e3:.0f}k" for k, v in totals.items())
        + f"  |  total: ${totals.sum()/1e3:.0f}k"
    )
    st.caption(FRAMING_CAPTION)

    # ---------------------------------------------------------------- Panel B: carry vs price
    st.markdown("### B - Carry vs Price Change")
    carry_price = (
        sleeve.groupby("date", as_index=False)[["carry", "price_change"]].sum()
        .sort_values("date")
    )
    carry_price_cum = carry_price[["carry", "price_change"]].cumsum()

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(carry_price["date"], carry_price_cum["carry"],
            label="Carry (coupon/distribution accrual)", color="#1b8a3a", lw=1.5)
    ax.plot(carry_price["date"], carry_price_cum["price_change"],
            label="Price change", color="#888", lw=1.0)
    _fmt_dollar(ax)
    ax.set_title("Cumulative carry vs price change -- carry is ~70% of gross P&L")
    ax.legend(fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    total_carry = carry_price_cum["carry"].iloc[-1]
    total_price = carry_price_cum["price_change"].iloc[-1]
    total_gross = total_carry + total_price
    carry_pct = total_carry / total_gross * 100 if total_gross else 0
    st.caption(
        f"Carry total: ${total_carry/1e3:.0f}k ({carry_pct:.0f}% of gross) | "
        f"Price change total: ${total_price/1e3:.0f}k"
    )
    st.caption(FRAMING_CAPTION)

    # ---------------------------------------------------------------- Panel C: long vs short
    st.markdown("### C - Long vs Short P&L")

    # Derive long/short from per-ticker pnl and weight sign
    attr_raw = pd.read_parquet("data/processed/attribution.parquet")
    attr_raw["date"] = pd.to_datetime(attr_raw["date"])
    attr_raw["pnl_long"] = attr_raw["pnl"].where(attr_raw["weight"].fillna(0) > 0, 0)
    attr_raw["pnl_short"] = attr_raw["pnl"].where(attr_raw["weight"].fillna(0) < 0, 0)

    ls_daily = (
        attr_raw.groupby("date", as_index=False)[["pnl_long", "pnl_short"]].sum()
        .sort_values("date")
    )
    ls_cum = ls_daily[["pnl_long", "pnl_short"]].cumsum()

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(ls_daily["date"], ls_cum["pnl_long"], label="Long bucket", color="#1b5e8a")
    ax.plot(ls_daily["date"], ls_cum["pnl_short"], label="Short bucket", color="#cc3300")
    ax.axhline(0, color="black", lw=0.5)
    _fmt_dollar(ax)
    ax.set_title("Cumulative long vs short gross P&L")
    ax.legend(fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    st.caption(
        f"Long total: ${ls_cum['pnl_long'].iloc[-1]/1e3:.0f}k | "
        f"Short total: ${ls_cum['pnl_short'].iloc[-1]/1e3:.0f}k"
    )
    st.caption(FRAMING_CAPTION)

    # ---------------------------------------------------------------- Panel D: directional vs selection
    st.markdown("### D - Directional vs Selection")
    dir_cum = daily.set_index("date")[["directional", "selection"]].cumsum()

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(dir_cum.index, dir_cum["directional"], label="Directional (net x market)", color="#1b5e8a")
    ax.plot(dir_cum.index, dir_cum["selection"], label="Selection (relative positioning)", color="#cc3300")
    ax.axhline(0, color="black", lw=0.5)
    _fmt_dollar(ax)
    ax.set_title("Cumulative directional vs selection P&L")
    ax.legend(fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    st.caption(
        f"Directional total: ${dir_cum['directional'].iloc[-1]/1e3:.0f}k | "
        f"Selection total: ${dir_cum['selection'].iloc[-1]/1e3:.0f}k"
    )
    st.caption(FRAMING_CAPTION)

    st.markdown("---")

    # ---------------------------------------------------------------- Panel E: factor betas + residual
    st.markdown("### E - Factor Betas and Residual")

    # Factor betas -- need to load raw attribution for beta columns
    # These are in the tidy frame -- get a per-date beta frame from a separate source
    # The betas were stored in the rolling_factor_regression result, not in attribution.parquet
    # We reconstruct them here from the risk.attribution module
    try:
        from signals.etf_universe import load_universe_close
        from risk.attribution import (
            build_v82_book, factor_returns, gross_pnl_series,
            rolling_factor_regression, NOTIONAL_DEFAULT
        )
        close = load_universe_close()
        _, result = build_v82_book(close)
        gross = gross_pnl_series(result)
        book_ret = gross / NOTIONAL_DEFAULT
        fret = factor_returns(close)
        fr = rolling_factor_regression(book_ret, fret)

        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        ax1 = axes[0]
        for col, color, label in [
            ("beta_eq", "#2e7d32", "equity (SPY)"),
            ("beta_rates", "#1b5e8a", "rates (IEF)"),
            ("beta_credit", "#cc3300", "credit (HYG-IEF)"),
            ("beta_gold", "#d4a017", "gold (GLD)"),
        ]:
            series = fr.betas[col].dropna()
            ax1.plot(series.index, series.values, label=label, color=color, lw=0.9)
        ax1.axhline(0, color="black", lw=0.5)
        ax1.set_title("Rolling 252d factor betas (point-in-time, no look-ahead)")
        ax1.legend(fontsize=8); ax1.grid(alpha=0.25)

        ax2 = axes[1]
        be_cum = fr.beta_explained.cumsum().dropna()
        res_cum = fr.residual.cumsum().dropna()
        ax2.plot(be_cum.index, be_cum.values, label="Beta-explained (aggregate factor exposure)", color="#888")
        ax2.plot(res_cum.index, res_cum.values, label="Residual (exposure-timing + carry)", color="#cc3300")
        ax2.axhline(0, color="black", lw=0.5)
        _fmt_dollar(ax2)
        ax2.set_title("Cumulative beta-explained P&L vs residual")
        ax2.legend(fontsize=8); ax2.grid(alpha=0.25)
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)

        r2_mean = fr.r_squared.dropna().mean()
        be_total = fr.beta_explained.sum()
        res_total = fr.residual.sum()
        st.caption(
            f"Rolling R-squared mean: {r2_mean:.2f} | "
            f"Beta-explained total: ${be_total/1e3:.0f}k | "
            f"Residual total: ${res_total/1e3:.0f}k"
        )
    except Exception as exc:
        st.warning(f"Factor beta panel could not be computed: {exc}")

    # Finding-3 caveat -- required by U2
    st.info(FINDING3_CAVEAT)
    st.caption(FRAMING_CAPTION)

    st.markdown("---")

    # ---------------------------------------------------------------- Panel F: cost drag
    st.markdown("### F - Cost Drag")
    gross_cum = daily.set_index("date")[["gross_pnl", "net_pnl"]].cumsum()

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(gross_cum.index, gross_cum["gross_pnl"], label="Gross P&L", color="#1b5e8a", lw=1.0)
    ax.plot(gross_cum.index, gross_cum["net_pnl"], label="Net P&L (after cost)", color="#cc3300", lw=1.5)
    ax.fill_between(gross_cum.index, gross_cum["net_pnl"], gross_cum["gross_pnl"],
                    color="#cc3300", alpha=0.1, label="Cost drag")
    _fmt_dollar(ax)
    ax.set_title("Gross vs net P&L -- cost drag shaded")
    ax.legend(fontsize=9); ax.grid(alpha=0.25)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")
    plt.close(fig)

    total_tc = daily["turnover_cost"].sum()
    total_bc = daily["borrow_cost"].sum()
    mean_tc_bps = daily["turnover_cost"].mean() / NOTIONAL_DEFAULT * 1e4
    mean_bc_bps = daily["borrow_cost"].mean() / NOTIONAL_DEFAULT * 1e4
    st.caption(
        f"Turnover cost total: ${total_tc/1e3:.0f}k ({mean_tc_bps:.2f} bps/day avg) | "
        f"Borrow cost total: ${total_bc/1e3:.0f}k ({mean_bc_bps:.2f} bps/day avg)"
    )
    st.caption(FRAMING_CAPTION)

    st.markdown("---")

    # ---------------------------------------------------------------- Panel G: MCTR by sleeve
    st.markdown("### G - Marginal Risk Contribution by Sleeve")
    sleeve_cols = ["mctr_equity", "mctr_rates", "mctr_credit", "mctr_commodity"]
    mctr_latest = (
        mctr.sort_values("date", ascending=False)
        .drop_duplicates(subset="mode")
        .set_index("mode")
    )

    if len(mctr_latest) > 0:
        labels = ["equity", "rates", "credit", "commodity"]
        x = np.arange(len(labels))
        width = 0.35

        fig, ax = plt.subplots(figsize=(8, 4))
        for i, (mode, color) in enumerate([("ex_ante", "#1b5e8a"), ("realized", "#cc3300")]):
            if mode in mctr_latest.index:
                vals = [mctr_latest.loc[mode, f"mctr_{l}"] * 100 for l in labels]
                ax.bar(x + (i - 0.5) * width, vals, width, label=mode, color=color, alpha=0.7)

        ax.set_xticks(x); ax.set_xticklabels(labels)
        ax.set_ylabel("MCTR (% daily)")
        ax.set_title("Marginal contribution to portfolio vol by sleeve (most recent date)")
        ax.legend(fontsize=9); ax.grid(alpha=0.25, axis="y")
        fig.tight_layout()
        st.pyplot(fig, width="stretch")
        plt.close(fig)

        st.caption("MCTR reconciles to portfolio sigma (Euler identity), not to P&L.")
    else:
        st.info("MCTR data not available.")


try:
    from risk.attribution import NOTIONAL_DEFAULT
except ImportError:
    NOTIONAL_DEFAULT = 1_000_000.0
