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


# `load_close_matrix` lived here and read `data/raw/*.parquet`, the committed price
# snapshot. The risk panel was its only consumer, and it could never be current: the
# refresh runs in the signal cron's own container, not this one. The panel now fetches
# through `dashboard.live_prices`, so the loader is gone rather than left as a trap for
# the next caller who wants a covariance.

def as_of_date(df: pd.DataFrame) -> pd.Timestamp:
    return df.index[-1]
