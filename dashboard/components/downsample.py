"""Bounded-point series for dashboard plots -- sprint v9.5.

Every chart in this dashboard plots a full daily series: the attribution curves and
the research equity curves are about 4,800 points per line, and the factor-beta
panel is four lines of the same length. Two things make that expensive:

  - matplotlib's Agg renderer walks every point, and the vertex buffer it allocates
    is transient, which is what drives the process high-water mark; and
  - the encoded PNG that Streamlit ships to the browser grows with the vertex count.

A 12x4 inch figure is roughly 1,200 px wide, so past about one point per pixel extra
points cannot change the picture. Measured peak RSS for the dashboard pages was
250-380 MiB against a 512 MiB Render instance, and rendering was the part that moved.

This downsamples with min/max bucketing rather than plain striding. Striding would
drop the spikes, and a spike is often the point of the chart (a single bad day in an
equity curve). Each bucket contributes its minimum and its maximum, so the envelope
survives, the endpoints are always kept, and the series still starts and ends where
it really starts and ends.

Downsampling affects the plotted polyline only. It never touches the frames the
captions, metrics and tables read from, so no number shown on the page changes.
"""

from __future__ import annotations

import numpy as np

# One point per pixel for the widest figure in the dashboard (12 in at dpi 100).
MAX_PLOT_POINTS: int = 1200


def _kept_positions(values: np.ndarray, max_points: int) -> np.ndarray:
    """Indices to keep: per-bucket min and max, plus the final point."""
    n = values.size
    # Two points per bucket, so half as many buckets as the point budget.
    buckets = max(1, (max_points - 1) // 2)
    edges = np.linspace(0, n, buckets + 1).astype(int)

    keep: list[int] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        chunk = values[start:stop]
        if np.all(np.isnan(chunk)):
            keep.append(start)
            continue
        lo = start + int(np.nanargmin(chunk))
        hi = start + int(np.nanargmax(chunk))
        keep.extend(sorted((lo, hi)))

    positions = np.unique(np.asarray(keep, dtype=int))
    # Always end on the real last point, so the series cannot appear to stop early.
    if positions[-1] != n - 1:
        positions = np.append(positions, n - 1)
    return positions


def downsample(x, y, max_points: int = MAX_PLOT_POINTS):
    """Return `x` and `y` reduced to at most `max_points`, keeping the shape.

    `x` may be a DatetimeIndex, an Index or an array; it is reduced with the same
    positions as `y`. Returns the inputs untouched when the series is already small,
    so a short series is bit-for-bit what it was before.
    """
    values = np.asarray(y, dtype=float)
    if max_points < 3 or values.size <= max_points:
        return x, y

    positions = _kept_positions(values, max_points)
    taken = values[positions]

    try:
        reduced_x = x.take(positions)  # pandas Index / DatetimeIndex
    except AttributeError:
        reduced_x = np.asarray(x)[positions]

    if hasattr(y, "iloc"):  # a Series: keep it a Series so ax.plot labels/lw still apply
        return reduced_x, y.iloc[positions]
    return reduced_x, taken


def downsample_xy(ax, x, y, **plot_kwargs):
    """`ax.plot` with the series downsampled. Convenience for the common case."""
    return ax.plot(*downsample(x, y), **plot_kwargs)
