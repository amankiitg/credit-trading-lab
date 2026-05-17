"""Position state machine — z-score + thresholds → position series.

State ∈ {-1 short, 0 flat, +1 long}. Trailing-only: the position at
date t is decided from z known at t. Fills/P&L lag is applied later
by the backtest engine, not here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def run_state_machine(
    z: pd.Series,
    entry: float = 2.0,
    exit_t: float = 0.5,
    stop: float = 4.0,
    regime_gate: pd.Series | None = None,
) -> pd.Series:
    """Return a {-1, 0, +1} position series from a z-score series.

    Transitions, evaluated each day on that day's z:
      flat  → short  when z > +entry      (residual rich, bet it falls)
      flat  → long   when z < -entry      (residual cheap, bet it rises)
      held  → flat   when |z| < exit_t    (reverted — take profit)
      held  → flat   when |z| > stop      (blew through — stop loss)
    Only one position open at a time; an open position persists until
    an exit or stop fires.

    ``regime_gate`` (a boolean Series aligned to ``z``) restricts
    *entries* to True days; exits and stops are never gated. NaN z or
    NaN gate is treated as "no entry" / position held flat.

    The exit band is checked before the stop band, but they are
    mutually exclusive (exit_t < stop) so order does not matter.
    """
    if entry <= exit_t:
        raise ValueError("entry must be > exit_t")
    if stop <= entry:
        raise ValueError("stop must be > entry")
    if regime_gate is not None and not regime_gate.index.equals(z.index):
        regime_gate = regime_gate.reindex(z.index)

    zv = z.to_numpy(dtype="float64")
    n = len(zv)
    pos = np.zeros(n, dtype="int64")

    if regime_gate is None:
        gate = np.ones(n, dtype="bool")
    else:
        gate = regime_gate.fillna(False).to_numpy(dtype="bool")

    state = 0
    for t in range(n):
        zt = zv[t]
        if np.isnan(zt):
            pos[t] = state
            continue
        abs_zt = abs(zt)
        if state == 0:
            # entries — gated
            if gate[t]:
                if zt > entry:
                    state = -1          # short the residual
                elif zt < -entry:
                    state = 1           # long the residual
        else:
            # exits / stops — never gated
            if abs_zt < exit_t or abs_zt > stop:
                state = 0
        pos[t] = state

    return pd.Series(pos, index=z.index, name="position")
