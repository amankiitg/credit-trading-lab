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


def as_of_date(df: pd.DataFrame) -> pd.Timestamp:
    return df.index[-1]
