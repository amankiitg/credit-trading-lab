"""Cached features.parquet reader for the Sprint 4 dashboard.

Single I/O boundary. `load_features()` is wrapped in
`st.cache_data` so subsequent reruns (slider moves, view switches)
skip the parquet read.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

FEATURES_PATH = Path("data/processed/features.parquet")


@st.cache_data(show_spinner=False)
def load_features(path: str = str(FEATURES_PATH)) -> pd.DataFrame:
    """Read features.parquet once per session/cache key."""
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_close_matrix() -> pd.DataFrame:
    """The whole-universe close matrix, read once per session.

    Wraps signals.etf_universe.load_universe_close, which reads eight raw parquets
    and rebuilds the matrix on every call. The MCTR panel called it directly from
    inside render(), so it was re-read on every rerun (every widget change and every
    tab switch), along with the ~50 MiB of signals/risk imports it drags in.

    The result is about 0.33 MiB of data, so caching it is cheap and removes the
    repeated read and rebuild entirely.
    """
    from signals.etf_universe import load_universe_close

    return load_universe_close()


def as_of_date(df: pd.DataFrame) -> pd.Timestamp:
    return df.index[-1]
