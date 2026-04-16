"""Rolling z-score computation.

Strictly trailing — ``z_w_t = (x_t - mean_{t-w+1..t}) / std_{t-w+1..t}``.
Values before ``min_periods=w`` are NaN.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_Z_WINDOWS: list[int] = [63, 126, 252]


def compute_zscores(
    df: pd.DataFrame,
    cols: list[str],
    windows: list[int] = DEFAULT_Z_WINDOWS,
) -> pd.DataFrame:
    """Return a dataframe of z-score columns named ``{col}_z{w}``."""
    out = pd.DataFrame(index=df.index)
    for col in cols:
        x = df[col]
        for w in windows:
            mu = x.rolling(w, min_periods=w).mean()
            sd = x.rolling(w, min_periods=w).std()
            out[f"{col}_z{w}"] = (x - mu) / sd
    return out
