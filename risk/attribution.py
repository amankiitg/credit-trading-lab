"""Forensic P&L and risk attribution for the v8.2 closing book, sprint v8.3.

No edge claim. This module explains the v8.2 book's realized P&L; it does
not predict future P&L and does not contain a security-selection layer --
every instrument here is an ETF basket (sprints/v8.3/PRD.md, House Rule 7).

Seven decompositions, each reconciling to the quantity it claims to
decompose within a tight tolerance:
  1. per-instrument / per-asset-class P&L
  2. long vs short P&L
  3. directional (net exposure x market move) vs selection
  4. factor regression: beta-explained vs residual (exposure-timing, never
     security selection, never alpha)
  5. carry (dividend/distribution accrual) vs price change
  6. gross vs net P&L via turnover/borrow cost
  7. marginal contribution to portfolio vol by sleeve, ex-ante vs realized

Decompositions 1-6 reconcile to total daily gross P&L. Decomposition 7
reconciles to total portfolio volatility via the Euler identity, not to
P&L -- a different reconciled quantity, stated explicitly, not glossed
over (sprints/v8.3/PRD.md, Falsification Criteria).

The factor regression (4) is the one genuinely empirical, non-tautological
output of this sprint. Every other "residual" or "selection" term is
defined as whatever total minus the other term leaves over, so it
reconciles by construction. The regression's factors are deliberately
daily and exposure-matched (SPY, IEF, HYG-IEF, GLD) -- no monthly or
lower-frequency factor, including the He-Kelly-Manela intermediary capital
factor, enters any daily computation in this module (House Rule 8).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.multi_asset import MultiAssetResult, run_multi_asset
from signals.dividends import load_dividend_matrix
from signals.etf_universe import ASSET_CLASS, UNIVERSE, load_universe_close
from signals.trend_signal import (
    apply_rebalance_control,
    compute_trend,
    shift_to_next_day,
    to_position_matrix,
)

TRADING_DAYS = 252
NOTIONAL_DEFAULT = 1_000_000.0
FACTOR_WINDOW_DEFAULT = 252
VOL_WINDOW_DEFAULT = 63
RECONCILE_TOL = 1e-6

# v8.2 closing book parameters -- frozen, not re-tuned here (House Rule 4)
L_V82 = 120
K_DEAD_ZONE_V82 = 0.5
BAND_PCT_V82 = 0.20

SLEEVES: list[str] = ["equity", "rates", "credit", "commodity"]


def build_v82_book(
    close: pd.DataFrame, notional: float = NOTIONAL_DEFAULT
) -> tuple[pd.DataFrame, MultiAssetResult]:
    """Reconstruct the exact v8.2 closing book and run it through the
    daily P&L accumulator. L, k_dead_zone, and band_pct are frozen at
    their v8.2 closing values (House Rule 4) -- not parameters here.
    """
    desired = to_position_matrix(
        compute_trend(close, L=L_V82, long_short=True, k_dead_zone=K_DEAD_ZONE_V82)
    )
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=BAND_PCT_V82)
    target = shift_to_next_day(held)
    result = run_multi_asset(target, close, notional=notional)
    return target, result


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    return close.pct_change(fill_method=None)


def gross_pnl_series(result: MultiAssetResult) -> pd.Series:
    """Re-derive gross P&L from run_multi_asset's own net/cost components
    (re-exposure, not an independent recomputation) -- this is the
    authoritative gross P&L every other decomposition reconciles against.
    """
    return (result.daily_pnl + result.turnover_cost + result.borrow_cost).rename("gross_pnl")


# ---------------------------------------------------------------- (1) per-instrument / per-asset-class

def per_instrument_pnl(
    target: pd.DataFrame, close: pd.DataFrame, notional: float = NOTIONAL_DEFAULT
) -> pd.DataFrame:
    """pnl_i(t) = target_i(t) * ret_i(t) * notional, date x ticker matrix."""
    common_cols = [c for c in close.columns if c in target.columns]
    common_idx = target.index.intersection(close.index)
    t = target.loc[common_idx, common_cols].sort_index()
    c = close.loc[common_idx, common_cols].sort_index()
    ret = daily_returns(c)
    return (t * ret * notional).rename_axis(columns="ticker")


def per_asset_class_pnl(pnl_i: pd.DataFrame) -> pd.DataFrame:
    """Group per-instrument P&L into the four asset-class sleeves."""
    out = pd.DataFrame(index=pnl_i.index)
    for sleeve in SLEEVES:
        cols = [c for c in pnl_i.columns if ASSET_CLASS.get(c) == sleeve]
        out[sleeve] = pnl_i[cols].sum(axis=1, skipna=True)
    return out


# ---------------------------------------------------------------- (2) long vs short

def long_short_pnl(target: pd.DataFrame, pnl_i: pd.DataFrame) -> pd.DataFrame:
    common_idx = target.index.intersection(pnl_i.index)
    common_cols = [c for c in pnl_i.columns if c in target.columns]
    t = target.loc[common_idx, common_cols]
    p = pnl_i.loc[common_idx, common_cols]
    long_mask = t > 0
    short_mask = t < 0
    out = pd.DataFrame(index=common_idx)
    out["pnl_long"] = p.where(long_mask, 0.0).sum(axis=1, skipna=True)
    out["pnl_short"] = p.where(short_mask, 0.0).sum(axis=1, skipna=True)
    return out


# ---------------------------------------------------------------- (3) directional vs selection

def directional_selection_pnl(
    target: pd.DataFrame,
    close: pd.DataFrame,
    gross_pnl: pd.Series,
    notional: float = NOTIONAL_DEFAULT,
) -> pd.DataFrame:
    """market_ret(t) is the equal-weighted average return across the
    8-name universe -- the same basket as v8.2's T8 buy-and-hold baseline,
    reused here rather than inventing a second market proxy.
    """
    common_idx = target.index.intersection(close.index).intersection(gross_pnl.index)
    common_cols = [c for c in close.columns if c in target.columns]
    t = target.loc[common_idx, common_cols]
    c = close.loc[common_idx, common_cols]
    ret = daily_returns(c)

    market_ret = ret.mean(axis=1, skipna=True)
    net_exposure = t.sum(axis=1, skipna=True)

    out = pd.DataFrame(index=common_idx)
    out["market_ret"] = market_ret
    out["net_exposure"] = net_exposure
    out["directional"] = net_exposure * market_ret * notional
    out["selection"] = gross_pnl.loc[common_idx] - out["directional"]
    return out


# ---------------------------------------------------------------- (4) factor regression

@dataclass(frozen=True)
class FactorRegressionResult:
    betas: pd.DataFrame          # date x [const, eq, rates, credit, gold]
    r_squared: pd.Series
    beta_explained: pd.Series
    residual: pd.Series


def factor_returns(close: pd.DataFrame) -> pd.DataFrame:
    """The four daily, exposure-matched factors (House Rule 8): equity
    (SPY), rates (IEF), credit spread (HYG - IEF), gold (GLD). No monthly
    or lower-frequency factor enters this computation.
    """
    ret = daily_returns(close)
    out = pd.DataFrame(index=close.index)
    out["eq"] = ret["SPY"]
    out["rates"] = ret["IEF"]
    out["credit"] = ret["HYG"] - ret["IEF"]
    out["gold"] = ret["GLD"]
    return out


def rolling_factor_regression(
    book_ret: pd.Series,
    factors: pd.DataFrame,
    window: int = FACTOR_WINDOW_DEFAULT,
    notional: float = NOTIONAL_DEFAULT,
) -> FactorRegressionResult:
    """Rolling OLS, refit at every date, using only data through t-1 to
    explain day t (House Rule 2 / E1' -- no future leakage). Window =
    FACTOR_WINDOW_DEFAULT trading days, pre-registered, not a grid.

    beta_explained(t) and residual(t) are computed in dollar terms
    (book_ret(t) is itself gross_pnl(t) / notional, so the identity
    residual = gross_pnl - beta_explained holds exactly by construction).
    """
    idx = book_ret.index.intersection(factors.index)
    y = book_ret.loc[idx].to_numpy(dtype="float64")
    x = factors.loc[idx].to_numpy(dtype="float64")
    n = len(idx)
    k = x.shape[1]

    betas = np.full((n, k + 1), np.nan)
    r2 = np.full(n, np.nan)
    fitted = np.full(n, np.nan)

    for i in range(window, n):
        y_fit = y[i - window : i]
        x_fit = x[i - window : i]
        valid = np.isfinite(y_fit) & np.all(np.isfinite(x_fit), axis=1)
        if valid.sum() < window // 2:
            continue
        design = np.column_stack([np.ones(valid.sum()), x_fit[valid]])
        coef, _, _, _ = np.linalg.lstsq(design, y_fit[valid], rcond=None)
        betas[i] = coef

        x_today = x[i]
        if not np.all(np.isfinite(x_today)):
            continue
        fitted[i] = coef[0] + x_today @ coef[1:]

        resid_fit = y_fit[valid] - design @ coef
        ss_res = float(np.sum(resid_fit**2))
        ss_tot = float(np.sum((y_fit[valid] - y_fit[valid].mean()) ** 2))
        r2[i] = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

    betas_df = pd.DataFrame(
        betas, index=idx, columns=["const", "beta_eq", "beta_rates", "beta_credit", "beta_gold"]
    )
    r2_series = pd.Series(r2, index=idx, name="r_squared")
    beta_explained = pd.Series(fitted, index=idx, name="beta_explained") * notional
    residual = (book_ret.loc[idx] * notional - beta_explained).rename("residual")
    return FactorRegressionResult(betas_df, r2_series, beta_explained, residual)


# ---------------------------------------------------------------- (5) carry vs price change

def carry_price_pnl(
    target: pd.DataFrame,
    close: pd.DataFrame,
    dividends: pd.DataFrame,
    pnl_i: pd.DataFrame,
    notional: float = NOTIONAL_DEFAULT,
) -> pd.DataFrame:
    """carry_i(t) = shares_i(t) * div_i(t), price_change_i(t) = pnl_i(t) - carry_i(t).

    shares_i(t) = target_i(t) * notional / adj_close_i(t-1) -- an
    approximation using the back-adjusted close already used throughout
    this pipeline, not the true unadjusted share count (documented
    limitation, sprints/v8.3/PRD.md Data section).
    """
    common_idx = target.index.intersection(close.index).intersection(dividends.index)
    common_cols = [c for c in close.columns if c in target.columns]
    t = target.loc[common_idx, common_cols]
    c = close.loc[common_idx, common_cols]
    d = dividends.loc[common_idx, common_cols]

    prev_close = c.shift(1)
    shares = t * notional / prev_close
    carry = shares * d

    price_change = pnl_i.loc[common_idx, common_cols] - carry
    carry_out = carry.rename(columns=lambda x: f"carry_{x}")
    price_out = price_change.rename(columns=lambda x: f"price_change_{x}")
    return pd.concat([carry_out, price_out], axis=1)


# ---------------------------------------------------------------- (6) gross vs net, cost in bps

def gross_net_cost(result: MultiAssetResult, notional: float = NOTIONAL_DEFAULT) -> pd.DataFrame:
    gross = gross_pnl_series(result)
    out = pd.DataFrame(index=gross.index)
    out["gross_pnl"] = gross
    out["net_pnl"] = result.daily_pnl
    out["turnover_cost"] = result.turnover_cost
    out["borrow_cost"] = result.borrow_cost
    out["turnover_cost_bps"] = result.turnover_cost / notional * 1e4
    out["borrow_cost_bps"] = result.borrow_cost / notional * 1e4
    return out


# ---------------------------------------------------------------- (7) marginal contribution to vol

def mctr_by_sleeve(
    target: pd.DataFrame,
    close: pd.DataFrame,
    window: int = VOL_WINDOW_DEFAULT,
) -> pd.DataFrame:
    """Euler decomposition: sum_sleeve MCTR_sleeve(t) = sigma_portfolio(t),
    exact identity, not an estimate. Ex-ante uses the covariance over
    {t-window, ..., t-1} (point-in-time, a forecast available before day
    t). Realized uses {t-window+1, ..., t} (includes day t -- a
    forensic, after-the-fact diagnostic, not a trading input; this
    hindsight use does not violate House Rule 2 / E1', which governs the
    P&L attribution and the factor betas, not this retrospective risk
    report).
    """
    common_cols = [c for c in close.columns if c in target.columns]
    common_idx = target.index.intersection(close.index)
    t_mat = target.loc[common_idx, common_cols].fillna(0.0)
    ret = daily_returns(close.loc[common_idx, common_cols])

    sleeve_masks = {
        sleeve: np.array([ASSET_CLASS.get(c) == sleeve for c in common_cols]) for sleeve in SLEEVES
    }

    rows = []
    for mode, ret_for_cov in (("ex_ante", ret.shift(1)), ("realized", ret)):
        cov_panel = ret_for_cov.rolling(window).cov()
        valid_dates = set(cov_panel.index.get_level_values(0))
        for date in common_idx:
            if date not in valid_dates:
                continue
            cov = cov_panel.loc[date]
            if cov.isna().any().any():
                continue
            w = t_mat.loc[date].to_numpy(dtype="float64")
            sigma_vec = cov.to_numpy(dtype="float64") @ w
            sigma_p = float(np.sqrt(max(w @ sigma_vec, 0.0)))
            row = {"date": date, "mode": mode, "sigma_portfolio": sigma_p}
            for sleeve, sleeve_mask in sleeve_masks.items():
                w_s = np.where(sleeve_mask, w, 0.0)
                mctr = float(w_s @ sigma_vec) / sigma_p if sigma_p > 0 else 0.0
                row[f"mctr_{sleeve}"] = mctr
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- tidy output

def build_tidy_attribution(
    target: pd.DataFrame,
    close: pd.DataFrame,
    result: MultiAssetResult,
    dividends: pd.DataFrame,
    factor_result: FactorRegressionResult,
    directional: pd.DataFrame,
    notional: float = NOTIONAL_DEFAULT,
) -> pd.DataFrame:
    """One row per (date, ticker), dashboard-ready: per-instrument P&L,
    carry/price split, asset class, plus day-level quantities broadcast
    across every ticker row (gross/net P&L, cost, directional/selection,
    factor-explained/residual), matching the broadcast convention already
    used in signals.trend_signal's tidy frame (gross_exposure/net_exposure
    repeated per ticker row).
    """
    pnl_i = per_instrument_pnl(target, close, notional)
    carry_price = carry_price_pnl(target, close, dividends, pnl_i, notional)
    gnc = gross_net_cost(result, notional)

    frames = []
    for ticker in pnl_i.columns:
        df = pd.DataFrame(
            {
                "date": pnl_i.index,
                "ticker": ticker,
                "asset_class": ASSET_CLASS.get(ticker),
                "weight": target[ticker].reindex(pnl_i.index).to_numpy(),
                "pnl": pnl_i[ticker].to_numpy(),
                "carry": carry_price[f"carry_{ticker}"].reindex(pnl_i.index).to_numpy(),
                "price_change": carry_price[f"price_change_{ticker}"].reindex(pnl_i.index).to_numpy(),
            }
        )
        df["gross_pnl"] = gnc["gross_pnl"].reindex(pnl_i.index).to_numpy()
        df["net_pnl"] = gnc["net_pnl"].reindex(pnl_i.index).to_numpy()
        df["turnover_cost"] = gnc["turnover_cost"].reindex(pnl_i.index).to_numpy()
        df["borrow_cost"] = gnc["borrow_cost"].reindex(pnl_i.index).to_numpy()
        df["directional"] = directional["directional"].reindex(pnl_i.index).to_numpy()
        df["selection"] = directional["selection"].reindex(pnl_i.index).to_numpy()
        df["net_exposure"] = directional["net_exposure"].reindex(pnl_i.index).to_numpy()
        df["beta_explained"] = factor_result.beta_explained.reindex(pnl_i.index).to_numpy()
        df["residual"] = factor_result.residual.reindex(pnl_i.index).to_numpy()
        df["r_squared"] = factor_result.r_squared.reindex(pnl_i.index).to_numpy()
        frames.append(df)

    tidy = pd.concat(frames, ignore_index=True)
    return tidy.sort_values(["date", "ticker"]).reset_index(drop=True)
