"""Relative-value signals — OLS / Kalman / DV01 hedge methods.

For each (y, x) pair, returns ``(residual, hedge_ratio)`` aligned to
``y``'s index. All methods are trailing-only — at time t, β_t uses
only data {..., t-1, t}.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

OLS_WINDOW = 126

# DV01 proxy parameters (fixed per PRD)
HYG_PROXY_MATURITY = 4.0
LQD_PROXY_MATURITY = 9.0
PROXY_COUPON = 0.05
PROXY_FREQ = 2  # semi-annual
DAY_COUNT_THIRTY360 = 2

# CDS / Treasury proxies for pair 2
CDS_PROXY_MATURITY = 5.0
CDS_RECOVERY = 0.40
CDS_NOTIONAL = 1_000_000.0
RATES_PROXY_MATURITY = 10.0

# DGS curve tenors
DGS_TENORS = np.array([1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0])
DGS_COLS = ["dgs1", "dgs2", "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30"]


def ols_hedge(y: pd.Series, x: pd.Series, window: int = OLS_WINDOW) -> tuple[pd.Series, pd.Series]:
    """Rolling OLS hedge: y_t = α_t + β_t · x_t + ε_t over a trailing window.

    Returns (residual, hedge_ratio) Series aligned to y.index. Both
    are NaN until the first full window is available.
    """
    if not y.index.equals(x.index):
        x = x.reindex(y.index)

    mean_x = x.rolling(window, min_periods=window).mean()
    mean_y = y.rolling(window, min_periods=window).mean()
    var_x = x.rolling(window, min_periods=window).var(ddof=0)
    cov_xy = x.rolling(window, min_periods=window).cov(y, ddof=0)

    beta = cov_xy / var_x
    alpha = mean_y - beta * mean_x
    residual = y - (alpha + beta * x)

    # Mask warmup explicitly (rolling already returns NaN, but clarify)
    residual = residual.where(beta.notna())
    return residual, beta


def kalman_hedge(
    y: pd.Series,
    x: pd.Series,
    Q: float = 1e-5,
    init_window: int = 63,
) -> tuple[pd.Series, pd.Series]:
    """Two-state Kalman hedge with random-walk α, β.

    State s_t = [α_t, β_t]^T ; s_t = s_{t-1} + η_t,  η ~ N(0, Q·I).
    Observation: y_t = [1, x_t] · s_t + ε_t,  ε ~ N(0, R).

    Initial state from OLS on the first ``init_window`` valid rows.
    R estimated from that OLS residual variance.

    Returns (residual, β_t) Series aligned to y.index. Values prior
    to init_window are NaN.
    """
    if not y.index.equals(x.index):
        x = x.reindex(y.index)

    n = len(y)
    yv = y.to_numpy(dtype="float64")
    xv = x.to_numpy(dtype="float64")
    valid = np.isfinite(yv) & np.isfinite(xv)
    if valid.sum() < init_window:
        raise ValueError(f"need ≥{init_window} valid rows for Kalman init")

    cum_valid = np.cumsum(valid)
    init_end = int(np.searchsorted(cum_valid, init_window))
    init_mask = valid.copy()
    init_mask[init_end + 1 :] = False
    A = np.column_stack([np.ones(init_mask.sum()), xv[init_mask]])
    coef, *_ = np.linalg.lstsq(A, yv[init_mask], rcond=None)
    s = coef.astype("float64")  # [α, β]
    R = float(np.var(yv[init_mask] - A @ coef, ddof=1)) or 1e-12

    P = np.eye(2, dtype="float64")
    Qm = Q * np.eye(2, dtype="float64")
    beta = np.full(n, np.nan, dtype="float64")
    resid = np.full(n, np.nan, dtype="float64")

    for t in range(init_end + 1, n):
        if not valid[t]:
            beta[t] = s[1]
            continue
        P_pred = P + Qm
        H = np.array([1.0, xv[t]])
        S = float(H @ P_pred @ H + R)
        K = (P_pred @ H) / S
        innov = yv[t] - H @ s
        s = s + K * innov
        P = (np.eye(2) - np.outer(K, H)) @ P_pred
        beta[t] = s[1]
        resid[t] = yv[t] - H @ s

    return (
        pd.Series(resid, index=y.index, name="kalman_residual"),
        pd.Series(beta, index=y.index, name="kalman_beta"),
    )


def dv01_hedge(
    features_df: pd.DataFrame,
    credit_data_df: pd.DataFrame,
    pycredit: Any,
) -> dict[str, tuple[pd.Series, pd.Series]]:
    """DV01-based hedge ratios for the three RV families.

    For each trading day in ``features_df.index``:
      - Bootstrap a discount curve from that day's DGS yields.
      - Price 4y and 9y semi-annual 5% bullets → DV01_4y, DV01_9y.
      - Price a 10y bullet → DV01_10y (rates proxy for pair 2).
      - If synth_cds_hy is available, bootstrap a flat survival curve
        and price a 5y CDS → CS01_5y (credit proxy for pair 2).

    Hedge ratios:
      pair 1 (HY/IG):       β = DV01_4y / DV01_9y
      pair 2 (Credit/Rates): β = (CS01_5y / 1e4) / DV01_10y     (per-1bp)
      pair 3 (X-term):       β = DV01_4y / DV01_9y              (slope adj.)

    Residuals follow each pair's formula:
      rv_hy_ig    = hy_spread - β · ig_spread
      rv_credit_rates = hy_spread - β · dgs10/100
      rv_xterm    = hy_ig    - β · (dgs10-dgs2)/100

    Returns a dict keyed by pair name.
    Falls back to NaN for dates lacking DGS or (for pair 2) CDS data.
    """
    idx = features_df.index
    cmd = credit_data_df.reindex(idx).ffill()

    n = len(idx)
    dv01_4y = np.full(n, np.nan)
    dv01_9y = np.full(n, np.nan)
    dv01_10y = np.full(n, np.nan)
    cs01_5y = np.full(n, np.nan)

    # Per-day pricing loop. The C++ pricer is fast (~0.1ms / bond);
    # 4784 days × (1 bootstrap + 3 bonds + 1 CDS) ≈ 5s.
    coupons = np.array([PROXY_COUPON, PROXY_COUPON, PROXY_COUPON], dtype="float64")
    freqs = np.array([PROXY_FREQ, PROXY_FREQ, PROXY_FREQ], dtype="int32")
    mats = np.array(
        [HYG_PROXY_MATURITY, LQD_PROXY_MATURITY, RATES_PROXY_MATURITY], dtype="float64"
    )
    dcs = np.array([DAY_COUNT_THIRTY360] * 3, dtype="int32")
    cds_mats = np.array([CDS_PROXY_MATURITY], dtype="float64")
    cds_coups = np.array([0.05], dtype="float64")  # standard 5% running coupon
    cds_recs = np.array([CDS_RECOVERY], dtype="float64")
    cds_nots = np.array([CDS_NOTIONAL], dtype="float64")

    dgs = cmd[DGS_COLS].to_numpy() / 100.0  # decimal
    cds = cmd["synth_cds_hy"].to_numpy() / 1e4  # bps → decimal

    valid_curve = np.isfinite(dgs).all(axis=1)
    valid_cds = np.isfinite(cds)

    for i in range(n):
        if not valid_curve[i]:
            continue
        try:
            curve = pycredit.bootstrap_discount(DGS_TENORS, dgs[i])
        except Exception:
            continue
        try:
            res = pycredit.price_bonds(curve, coupons, freqs, mats, dcs)
            dv01_4y[i] = float(res["dv01"][0])
            dv01_9y[i] = float(res["dv01"][1])
            dv01_10y[i] = float(res["dv01"][2])
        except Exception:
            pass
        if valid_cds[i]:
            try:
                surv = pycredit.bootstrap_survival(
                    cds_mats, np.array([cds[i]]), CDS_RECOVERY, curve
                )
                cds_res = pycredit.price_cds(
                    surv, curve, cds_mats, cds_coups, cds_recs, cds_nots
                )
                cs01_5y[i] = float(cds_res["cs01"][0])
            except Exception:
                pass

    dv01_4y_s = pd.Series(dv01_4y, index=idx)
    dv01_9y_s = pd.Series(dv01_9y, index=idx)
    dv01_10y_s = pd.Series(dv01_10y, index=idx)
    cs01_5y_s = pd.Series(cs01_5y, index=idx)

    # Pair 1: HY/IG
    hr1 = dv01_4y_s / dv01_9y_s
    res1 = features_df["hy_spread"] - hr1 * features_df["ig_spread"]

    # Pair 2: Credit vs Rates. CS01 is per 1bp on notional; DV01 is
    # per 1bp on $100 of bond price. Normalize CS01 by notional and
    # DV01 by 100 to get comparable per-1bp dollar sensitivities.
    cs01_norm = cs01_5y_s / CDS_NOTIONAL
    dv01_norm = dv01_10y_s / 100.0
    hr2 = cs01_norm / dv01_norm
    res2 = features_df["hy_spread"] - hr2 * (cmd["dgs10"] / 100.0)

    # Pair 3: X-term — duration adjustment ratio
    hr3 = dv01_4y_s / dv01_9y_s
    res3 = features_df["hy_ig"] - hr3 * ((cmd["dgs10"] - cmd["dgs2"]) / 100.0)

    return {
        "rv_hy_ig": (res1, hr1),
        "rv_credit_rates": (res2, hr2),
        "rv_xterm": (res3, hr3),
    }


# ---------------------------------------------------------------- V6


PAIR_NAMES = ("rv_hy_ig", "rv_credit_rates", "rv_xterm")
HEDGE_METHODS = ("ols", "kalman", "dv01")


def build_all_residuals(
    features_df: pd.DataFrame,
    credit_data_df: pd.DataFrame,
    pycredit: Any,
) -> dict[str, dict[str, tuple[pd.Series, pd.Series]]]:
    """Compute all 3 pairs × 3 methods = 9 (residual, hedge_ratio) results.

    Returns a nested dict: results[pair][method] = (resid, hr).
    """
    cmd = credit_data_df.reindex(features_df.index).ffill()
    pairs = {
        "rv_hy_ig": (features_df["hy_spread"], features_df["ig_spread"]),
        "rv_credit_rates": (features_df["hy_spread"], cmd["dgs10"] / 100.0),
        "rv_xterm": (features_df["hy_ig"], (cmd["dgs10"] - cmd["dgs2"]) / 100.0),
    }

    results: dict[str, dict[str, tuple[pd.Series, pd.Series]]] = {}
    for name, (y, x) in pairs.items():
        results[name] = {
            "ols": ols_hedge(y, x),
            "kalman": kalman_hedge(y, x),
        }

    dv = dv01_hedge(features_df, credit_data_df, pycredit)
    for name in pairs:
        results[name]["dv01"] = dv[name]
    return results


def select_best_method(
    results: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    warmup: int = 252,
) -> dict[str, tuple[str, dict[str, float]]]:
    """For each pair pick the method whose residual has the lowest ADF p-value
    on post-warmup data. Returns ``{pair: (best_method, {method: p_value})}``.
    """
    from statsmodels.tsa.stattools import adfuller

    best: dict[str, tuple[str, dict[str, float]]] = {}
    for pair, methods in results.items():
        scores: dict[str, float] = {}
        for m, (resid, _) in methods.items():
            r = resid.iloc[warmup:].dropna()
            if len(r) < 50:
                scores[m] = 1.0
                continue
            try:
                scores[m] = float(adfuller(r, autolag="AIC")[1])
            except Exception:
                scores[m] = 1.0
        best_m = min(scores, key=scores.get)
        best[pair] = (best_m, scores)
    return best


def trailing_zscore(series: pd.Series, window: int = 63) -> pd.Series:
    mu = series.rolling(window, min_periods=window).mean()
    sd = series.rolling(window, min_periods=window).std()
    return (series - mu) / sd


# ---------------------------------------------------------------- V8


def build_regime_quality_table(
    features_df: pd.DataFrame,
    all_residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    regime_cols: tuple[str, ...] = ("vol_regime", "equity_regime", "equity_credit_lag"),
    z_window: int = 63,
    warmup: int = 252,
    signal_threshold: float = 1.5,
) -> pd.DataFrame:
    """Per (signal, method, regime_classifier, regime_label) quality stats."""
    from statsmodels.tsa.stattools import adfuller

    from signals.halflife import ou_halflife

    rows = []
    for signal, methods in all_residuals.items():
        for method, (resid, _hr) in methods.items():
            z = trailing_zscore(resid, window=z_window)
            for classifier in regime_cols:
                labels = features_df[classifier]
                for label in labels.dropna().unique():
                    mask = (labels == label)
                    sub_resid = resid.iloc[warmup:][mask.iloc[warmup:]].dropna()
                    sub_z = z.iloc[warmup:][mask.iloc[warmup:]].dropna()
                    n = int(len(sub_resid))
                    if n < 30:
                        rows.append(
                            dict(
                                signal=signal,
                                hedge_method=method,
                                regime_classifier=classifier,
                                regime_label=str(label),
                                half_life=float("inf"),
                                z_magnitude=float("nan"),
                                signal_freq=float("nan"),
                                n_obs=n,
                                adf_pvalue=float("nan"),
                            )
                        )
                        continue
                    hl = ou_halflife(sub_resid)
                    z_mag = float(sub_z.abs().mean()) if len(sub_z) else float("nan")
                    z_freq = (
                        float((sub_z.abs() > signal_threshold).mean()) if len(sub_z) else float("nan")
                    )
                    try:
                        adf_p = float(adfuller(sub_resid, autolag="AIC")[1])
                    except Exception:
                        adf_p = float("nan")
                    rows.append(
                        dict(
                            signal=signal,
                            hedge_method=method,
                            regime_classifier=classifier,
                            regime_label=str(label),
                            half_life=hl,
                            z_magnitude=z_mag,
                            signal_freq=z_freq,
                            n_obs=n,
                            adf_pvalue=adf_p,
                        )
                    )
    return pd.DataFrame(rows)


def hedge_ratio_cv(
    hedge_ratio: pd.Series,
    window: int = 63,
) -> pd.Series:
    """Rolling 63-day coefficient of variation of a hedge-ratio series."""
    mu = hedge_ratio.rolling(window, min_periods=window).mean()
    sd = hedge_ratio.rolling(window, min_periods=window).std()
    return sd / mu.abs()
