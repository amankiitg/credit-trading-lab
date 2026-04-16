"""Feature construction: returns, rolling volatility, spread series.

Pure functions — take dataframes in, return dataframes out. No disk I/O.
All rolling stats are trailing only; no ``center=True``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_VOL_WINDOWS: list[int] = [21, 63, 126]
TRADING_DAYS: int = 252


def _price_cols(df: pd.DataFrame) -> list[tuple[str, str]]:
    return [(c, c[: -len("_adj_close")]) for c in df.columns if c.endswith("_adj_close")]


def compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Append per-ticker ``{T}_log_ret`` columns.

    Input: wide dataframe with ``{T}_adj_close`` columns. Output: the
    same frame with ``{T}_log_ret = ln(P_t / P_{t-1})`` appended. The
    first row is NaN by construction.
    """
    out = df.copy()
    for col, t in _price_cols(df):
        out[f"{t}_log_ret"] = np.log(df[col] / df[col].shift(1))
    return out


def compute_vol(
    df: pd.DataFrame,
    windows: list[int] = DEFAULT_VOL_WINDOWS,
) -> pd.DataFrame:
    """Append annualized rolling vol columns.

    For each window ``w`` and ticker ``T``:
    ``{T}_vol_{w} = std(log_ret, w) * sqrt(252)``. Uses ``min_periods=w``
    so early rows are NaN until the window is full.
    """
    out = df.copy()
    ret_cols = [c for c in df.columns if c.endswith("_log_ret")]
    for col in ret_cols:
        t = col[: -len("_log_ret")]
        for w in windows:
            out[f"{t}_vol_{w}"] = (
                df[col].rolling(w, min_periods=w).std() * np.sqrt(TRADING_DAYS)
            )
    return out


def compute_spreads(df: pd.DataFrame) -> pd.DataFrame:
    """Return a dataframe with columns ``hy_spread, ig_spread, hy_ig``.

    Formulas (PRD §Signal Definition):
      hy_spread = ln(P(HYG) / P(IEF))
      ig_spread = ln(P(LQD) / P(IEF))
      hy_ig     = ln(P(HYG) / P(LQD))
    """
    out = pd.DataFrame(index=df.index)
    out["hy_spread"] = np.log(df["HYG_adj_close"] / df["IEF_adj_close"])
    out["ig_spread"] = np.log(df["LQD_adj_close"] / df["IEF_adj_close"])
    out["hy_ig"] = np.log(df["HYG_adj_close"] / df["LQD_adj_close"])
    return out
