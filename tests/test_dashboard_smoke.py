"""D8 — smoke / snapshot test via Streamlit's AppTest.

Asserts:
  (a) The app imports + runs without exceptions.
  (b) Today View renders 6 cards on the last date.
  (c) On a known historical HIGH-conviction date, the corresponding
      card's HTML contains the HIGH tier label and the green border
      color.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return pd.read_parquet(ROOT / "data" / "processed" / "features.parquet")


@pytest.fixture(scope="module")
def app() -> AppTest:
    cwd = os.getcwd()
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    os.chdir(cwd)
    return at


def test_app_runs_without_exceptions(app: AppTest) -> None:
    assert not app.exception, f"AppTest exception: {app.exception}"


def test_today_view_renders_six_cards(app: AppTest) -> None:
    """Each of the 6 signal names must appear in some rendered markdown element."""
    from dashboard.signal_specs import CARD_SPECS

    all_markdown = " ".join(m.value for m in app.markdown)
    missing = [s.name for s in CARD_SPECS if s.name not in all_markdown]
    assert not missing, f"Today View missing cards for: {missing}"


def test_high_card_renders_with_green_border(features: pd.DataFrame) -> None:
    """Render the Today View directly on a known HIGH date and inspect HTML."""
    from dashboard.signal_specs import CARD_SPECS
    from dashboard.views.today import _card_html
    from dashboard.conviction import BORDER_HIGH, conviction

    # Pick the first historical date where any of the 6 signals would be HIGH.
    high_dates: list[tuple[pd.Timestamp, str]] = []
    for spec in CARD_SPECS:
        z = features[spec.z_col]
        r = features[spec.regime_col].astype(str)
        mask = (z.abs() > 2.0) & (r == "equity_first")
        if mask.any():
            high_dates.append((features.index[mask][0], spec.name))
    assert high_dates, "no HIGH-conviction date found in features.parquet"

    when, signal_name = high_dates[0]
    spec = next(s for s in CARD_SPECS if s.name == signal_name)
    row = features.loc[when]
    z_val = float(row[spec.z_col])
    regime = str(row[spec.regime_col])
    tier = conviction(z_val, regime)
    assert tier == "HIGH", (when, signal_name, z_val, regime, tier)

    html = _card_html(spec, z_val, regime, tier, entry_threshold=2.0)
    assert "HIGH" in html
    assert BORDER_HIGH.lower() in html.lower(), (BORDER_HIGH, html[:400])
    assert "4px solid" in html  # HIGH border width
