"""Tests for the dashboard's plot downsampling and figure hygiene -- sprint v9.5.

Two separate concerns live here:

  1. `downsample` must shrink a series without changing what the chart says. These
     are financial charts, so the test that matters is that an extreme value still
     reaches the axis and the plotted series still starts and ends at the real data.
  2. Every matplotlib figure the dashboard creates must be closed. `plt.close` was
     already called at all 14 sites, so the second half of this file pins that
     rather than adding it.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboard.components.downsample import MAX_PLOT_POINTS, downsample

DASHBOARD = Path("dashboard")


# ------------------------------------------------------------------ downsampling

def test_a_small_series_is_returned_untouched() -> None:
    """Below the budget there must be no change at all, not a re-sampled copy."""
    x = pd.date_range("2026-01-01", periods=50, freq="D")
    y = pd.Series(np.arange(50, dtype=float), index=x)

    rx, ry = downsample(x, y)

    assert rx is x and ry is y


def test_a_large_series_shrinks_to_the_budget() -> None:
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    y = pd.Series(np.random.default_rng(7).normal(size=4800).cumsum(), index=x)

    rx, ry = downsample(x, y)

    assert len(ry) <= MAX_PLOT_POINTS
    assert len(rx) == len(ry), "x and y must stay aligned"
    assert len(ry) < len(y) / 2, "must be a real reduction, not a trim"


def test_the_extremes_survive() -> None:
    """Min/max bucketing, not striding: a spike is often the point of the chart."""
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    values = np.zeros(4800)
    values[3711] = 1234.0   # the one day that matters
    values[2222] = -987.0
    y = pd.Series(values, index=x)

    _, ry = downsample(x, y)

    assert ry.max() == pytest.approx(1234.0), "a peak must not be smoothed away"
    assert ry.min() == pytest.approx(-987.0), "a trough must not be smoothed away"


def test_the_series_still_starts_and_ends_where_it_really_does() -> None:
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    y = pd.Series(np.arange(4800, dtype=float), index=x)

    rx, ry = downsample(x, y)

    assert rx[0] == x[0], "first plotted point must be the real first point"
    assert rx[-1] == x[-1], "last plotted point must be the real last point"
    assert ry.iloc[-1] == pytest.approx(y.iloc[-1])


def test_positions_are_sorted_and_unique() -> None:
    """A repeated or unordered index would draw a line that doubles back on itself."""
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    y = pd.Series(np.sin(np.arange(4800) / 50.0), index=x)

    rx, _ = downsample(x, y)

    assert rx.is_monotonic_increasing
    assert not rx.has_duplicates


def test_nan_does_not_break_the_reduction() -> None:
    """Real frames have holes; the equity curve is reindexed with fill_value=0."""
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    values = np.arange(4800, dtype=float)
    values[100:4000] = np.nan
    y = pd.Series(values, index=x)

    rx, ry = downsample(x, y)

    assert len(ry) <= MAX_PLOT_POINTS
    assert len(rx) == len(ry)
    assert np.isnan(ry).any(), "the hole must still read as a hole, not as zero"


def test_a_plain_array_and_range_still_work() -> None:
    """x is not always a pandas index."""
    x = np.arange(5000)
    y = np.arange(5000, dtype=float)

    rx, ry = downsample(x, y)

    assert len(ry) <= MAX_PLOT_POINTS
    assert rx[0] == 0 and rx[-1] == 4999


def test_downsampling_is_deterministic() -> None:
    x = pd.date_range("2007-01-01", periods=4800, freq="B")
    y = pd.Series(np.random.default_rng(3).normal(size=4800), index=x)

    assert downsample(x, y)[1].equals(downsample(x, y)[1]), "same input, same output"


# ------------------------------------------------------------------ figure hygiene

def _view_files() -> list[Path]:
    return sorted(DASHBOARD.rglob("views/*.py")) + [DASHBOARD / "app.py"]


def test_every_matplotlib_figure_is_closed() -> None:
    """Pins the existing behaviour: 14 figures were created and 14 were closed.

    A figure left open is never garbage collected while matplotlib's global state
    holds it, and on a long-lived dashboard session that is a guaranteed leak. The
    check is per file so the failure names the file that regressed.
    """
    offenders: list[str] = []
    for path in _view_files():
        if not path.exists():
            continue
        source = path.read_text()
        created = len(re.findall(r"plt\.subplots?\(", source)) + len(
            re.findall(r"plt\.figure\(", source)
        )
        closed = len(re.findall(r"plt\.close\(", source))
        if created and closed < created:
            offenders.append(f"{path}: {created} figures created, {closed} closed")

    assert not offenders, "unclosed matplotlib figures:\n" + "\n".join(offenders)


def test_the_dashboard_no_longer_uses_the_legacy_html_component() -> None:
    """st.components.v1.html is scheduled for removal and app.py was its only user.

    This looks for an import of the legacy namespace or a call into it, not for the
    bare string: app.py now explains in a comment why it uses st.iframe instead, and
    a substring check would fail on that prose.
    """
    legacy_import = re.compile(r"^\s*(?:import|from)\s+streamlit\.components", re.M)
    legacy_call = re.compile(r"\bcomponents\.v1\.\w+\s*\(")

    offenders = [
        str(path.relative_to(DASHBOARD))
        for path in DASHBOARD.rglob("*.py")
        if legacy_import.search(path.read_text())
        or legacy_call.search(path.read_text())
    ]
    assert not offenders, f"legacy components.v1 still used in: {offenders}"
