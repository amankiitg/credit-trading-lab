"""Universe data-quality gates for the signal pipeline -- sprint v9.4.

The 2026-09-24 signal run built a signal from a close matrix in which EFA, EEM,
IEF, HYG and LQD were each missing the 2026-09-23 session (4895 rows against 4896
for SPY, TLT and GLD). `load_universe_close` outer-joins the per-ticker parquets,
so a missing row becomes NaN for that ticker on a date the others have.

Nothing downstream flagged it. Instead:

  - sigma went NaN from 2026-09-23 for those five (a missing row makes the return
    NaN, and the 63 day rolling window then holds a NaN);
  - `compute_episodes` left those days unset;
  - `apply_rebalance_control` carried the previous held weights forward, because a
    data gap is handled as "not a true exit";
  - the joint gross cap re-scaled the whole row at the end.

The result was a book whose five weights had all moved by exactly 0.765 while the
other three were genuinely recomputed, with no warning anywhere. These functions
exist so that run_signal refuses to write such a signal.

Pure functions, no I/O.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def find_row_gaps(close: pd.DataFrame) -> list[dict]:
    """Dates where a ticker has no observation but the universe does.

    Leading NaN is legitimate and is not a gap: staggered inception (GLD from
    2004, EFA/EEM/TLT later) means a column can start after the others. Only NaN
    AFTER a ticker's first valid observation is a hole, because from that point
    the ticker is expected to trade every session the universe trades.

    Returns [{"ticker": str, "date": Timestamp or None}], where date is None for a
    column that has no observations at all.
    """
    gaps: list[dict] = []
    for ticker in close.columns:
        col = close[ticker]
        first = col.first_valid_index()
        if first is None:
            gaps.append({"ticker": ticker, "date": None})
            continue
        after = col.loc[first:]
        for date in after.index[after.isna()]:
            gaps.append({"ticker": ticker, "date": date})
    return gaps


def check_signal_ready(
    close: pd.DataFrame,
    sigma: pd.DataFrame,
    as_of_date,
) -> list[str]:
    """Return the reasons the signal must NOT be written. Empty list means ready.

    Two conditions, both observed in the 2026-09-24 run:

    1. Any ticker missing a row that the others have (`find_row_gaps`).
    2. Any ticker whose sigma is NaN on `as_of_date`, which is what a missing row
       actually does to the pipeline.

    Note this blocks on a gap anywhere in the history, not only a recent one. That
    is the safe default for a book that trades every session. If an ancient,
    immaterial hole ever blocks a run, the log line names the ticker and the date,
    and `scripts/backfill_raw_gap.py` is the repair.
    """
    problems: list[str] = []

    for gap in find_row_gaps(close):
        if gap["date"] is None:
            problems.append(
                f"missing close: {gap['ticker']} has no observations at all"
            )
        else:
            problems.append(
                f"missing close: {gap['ticker']} has no observation on "
                f"{gap['date'].date()} that the rest of the universe has"
            )

    if as_of_date not in sigma.index:
        problems.append(
            f"as_of_date {as_of_date} is not in the sigma index, so the signal "
            f"cannot be checked"
        )
        return problems

    row = sigma.loc[as_of_date]
    for ticker in sigma.columns:
        if pd.isna(row[ticker]):
            problems.append(
                f"NaN sigma on as_of_date: {ticker} on {as_of_date}"
            )

    return problems
