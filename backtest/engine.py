"""Backtest engine — positions → trade ledger + daily net P&L.

Turns a {-1, 0, +1} position series into round-trip trades, fills
each leg ``fill_lag`` trading days after the signal, and accrues
mark-to-market P&L daily so the resulting series is usable for an
annualised Sharpe.

P&L convention (PRD §Signal Definition):
    gross_pnl = position_sign · (rv_exit − rv_entry) · notional
A long-residual trade (position +1) profits when rv rises; a
short-residual trade (position −1) profits when rv falls.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from execution.costs import CostParams, trade_cost

TRADE_COLUMNS: tuple[str, ...] = (
    "entry_signal_date",
    "exit_signal_date",
    "entry_fill_date",
    "exit_fill_date",
    "side",                 # +1 long residual, -1 short residual
    "rv_entry",
    "rv_exit",
    "hedge_ratio_entry",
    "hedge_ratio_exit",
    "holding_days",
    "gross_pnl",
    "cost",
    "net_pnl",
    "closed_at_end",
)


@dataclass(frozen=True)
class BacktestResult:
    """Container for one strategy's backtest output."""

    trades: pd.DataFrame        # one row per round-trip, schema = TRADE_COLUMNS
    daily_pnl: pd.Series        # net P&L per calendar bar, indexed like the input
    equity: pd.Series           # cumulative sum of daily_pnl


def _runs(positions: np.ndarray) -> list[tuple[int, int, int]]:
    """Return (entry_idx, exit_idx, sign) for each contiguous non-zero run.

    ``entry_idx`` is the first index of the run, ``exit_idx`` is the
    first index *after* the run returns to flat (or len-1 if the run
    extends to the end of the series).
    """
    runs: list[tuple[int, int, int]] = []
    n = len(positions)
    i = 0
    while i < n:
        if positions[i] == 0:
            i += 1
            continue
        sign = int(positions[i])
        j = i
        while j < n and positions[j] == sign:
            j += 1
        # run occupies [i, j-1]; the flat bar (if any) is at j
        runs.append((i, j, sign))
        i = j
    return runs


def run(
    residual: pd.Series,
    positions: pd.Series,
    hedge_ratio: pd.Series,
    notional: float = 1_000_000.0,
    fill_lag: int = 1,
    cost_params: CostParams = CostParams(),
) -> BacktestResult:
    """Backtest a single strategy.

    Parameters
    ----------
    residual : the RV residual series (log-ratio units).
    positions : {-1, 0, +1} from ``execution.position.run_state_machine``.
    hedge_ratio : leg-2 weight at each date (for the ledger only).
    notional : HY-leg notional, constant per trade.
    fill_lag : trading days between signal and fill (>= 1, no same-bar).

    Returns
    -------
    BacktestResult with the trade ledger, daily net-P&L series, and
    cumulative equity curve.
    """
    if fill_lag < 1:
        raise ValueError(f"fill_lag must be >= 1 (no same-bar fills), got {fill_lag}")
    if not positions.index.equals(residual.index):
        positions = positions.reindex(residual.index)
    if not hedge_ratio.index.equals(residual.index):
        hedge_ratio = hedge_ratio.reindex(residual.index)

    idx = residual.index
    n = len(idx)
    rv = residual.to_numpy(dtype="float64")
    hr = hedge_ratio.to_numpy(dtype="float64")
    pos = positions.fillna(0).to_numpy(dtype="int64")

    daily = np.zeros(n, dtype="float64")
    borrow_per_day = cost_params.borrow_annual / 252.0 * notional
    spread_slippage = trade_cost(notional, holding_days=0, params=cost_params)

    rows: list[dict[str, object]] = []
    for sig_entry, sig_exit, sign in _runs(pos):
        entry_fill = sig_entry + fill_lag
        if entry_fill >= n:
            continue  # cannot fill the entry — drop the trade
        # exit signal index is sig_exit (first flat bar); clamp fill to series end
        closed_at_end = False
        exit_fill = sig_exit + fill_lag
        if exit_fill >= n:
            exit_fill = n - 1
            closed_at_end = True
        if exit_fill <= entry_fill:
            continue  # degenerate (e.g. 1-bar run at the very end)

        rv_entry = rv[entry_fill]
        rv_exit = rv[exit_fill]
        holding_days = exit_fill - entry_fill

        gross = sign * (rv_exit - rv_entry) * notional
        borrow = borrow_per_day * holding_days
        cost = spread_slippage + borrow
        net = gross - cost

        # daily mark-to-market over (entry_fill, exit_fill]
        for d in range(entry_fill + 1, exit_fill + 1):
            daily[d] += sign * (rv[d] - rv[d - 1]) * notional
            daily[d] -= borrow_per_day
        # spread + slippage lumped on the entry fill bar
        daily[entry_fill] -= spread_slippage

        rows.append({
            "entry_signal_date": idx[sig_entry],
            "exit_signal_date": idx[min(sig_exit, n - 1)],
            "entry_fill_date": idx[entry_fill],
            "exit_fill_date": idx[exit_fill],
            "side": sign,
            "rv_entry": rv_entry,
            "rv_exit": rv_exit,
            "hedge_ratio_entry": hr[entry_fill],
            "hedge_ratio_exit": hr[exit_fill],
            "holding_days": holding_days,
            "gross_pnl": gross,
            "cost": cost,
            "net_pnl": net,
            "closed_at_end": closed_at_end,
        })

    trades = pd.DataFrame(rows, columns=list(TRADE_COLUMNS))
    daily_pnl = pd.Series(daily, index=idx, name="daily_pnl")
    equity = daily_pnl.cumsum().rename("equity")
    return BacktestResult(trades=trades, daily_pnl=daily_pnl, equity=equity)
