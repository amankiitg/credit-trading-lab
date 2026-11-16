"""Does the flat $250 execution minimum earn its keep?

The signal sets a target weight per name; ``apply_rebalance_control`` bands the WEIGHT
(20% of the desired weight), and then ``compute_delta_orders`` refuses any dollar delta
below DELTA_MIN_NOTIONAL = $250. The band is proportional and the execution minimum is
flat, which is an odd pair: $250 is a big tolerance for a $25k name and a rounding error
for a $250k one.

This walks the same book four times, changing ONLY that execution minimum:

    flat $250      -- today's rule
    2%, 5%, 10%    -- of each name's own TARGET notional

Everything else is held fixed: the same signal (L=120, dead zone 0.5), the same 20%
weight band, the same costs (half spread + slippage in bp, borrow annual on shorts), the
same $1m notional, the same unadjusted closes and the same dates.

The book is simulated in SHARES, not weights, because that is what makes an execution
minimum mean anything: between trades the holdings drift with price, so the dollar gap a
rule sees is ``|target_notional - drifted_notional|``, not a weight difference. Costs are
charged on the notional actually traded, and borrow on the short exposure carried.

A trade happens when that dollar gap is at least the minimum. Orders are executed at the
close they are measured against, the same convention the other backtests use.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from execution.costs import CostParams
from signals.etf_universe import UNIVERSE, load_universe_close
from signals.trend_signal import (
    apply_rebalance_control,
    compute_trend,
    shift_to_next_day,
    to_position_matrix,
)

TRADING_DAYS = 252
BP = 1e-4

# The execution minimum under test. ``("flat", 250.0)`` is the rule shipped today.
VARIANTS: tuple[tuple[str, float], ...] = (
    ("flat $250", 250.0),
    ("2% of target", 0.02),
    ("5% of target", 0.05),
    ("10% of target", 0.10),
)


@dataclass(frozen=True)
class Run:
    label: str
    daily_return: pd.Series
    equity: pd.Series
    turnover_cost: pd.Series
    trades: pd.Series          # one entry per name-trade, value = notional traded
    notional: float


def threshold_for(label: str, value: float, target_notional: float) -> float:
    """The dollar minimum for this name today."""
    if label.startswith("flat"):
        return value
    return abs(target_notional) * value


def simulate(
    target_w: pd.DataFrame,
    close: pd.DataFrame,
    label: str,
    value: float,
    *,
    notional: float = 1_000_000.0,
    cost_params: CostParams = CostParams(),
) -> Run:
    """Walk the book in shares, honouring one execution minimum."""
    idx = target_w.index.intersection(close.index)
    tw = target_w.loc[idx]
    px = close.loc[idx].ffill()

    tickers = list(tw.columns)
    prices = px[tickers].to_numpy(dtype=float)
    weights = tw[tickers].to_numpy(dtype=float)

    shares = np.zeros(len(tickers))
    equity = float(notional)
    equity_series = []
    ret_series = []
    cost_series = []
    trades: list[tuple[pd.Timestamp, float]] = []

    for i in range(len(idx)):
        p = prices[i]
        tradable = np.isfinite(p) & (p > 0)
        eq_start = equity

        # Mark the drift: the P&L of yesterday's shares on today's prices.
        if i > 0:
            prev_p = prices[i - 1]
            step = np.where(tradable & np.isfinite(prev_p), shares * (p - prev_p), 0.0)
            pnl = float(np.nansum(step))
        else:
            pnl = 0.0
        equity += pnl

        # Size against today's equity; trade only where the dollar gap clears the band.
        target_notional = np.where(np.isfinite(weights[i]) & tradable, weights[i] * equity, 0.0)
        held_notional = np.where(tradable, shares * p, 0.0)
        gap = target_notional - held_notional
        minimum = np.array([threshold_for(label, value, t) for t in target_notional])

        # A name that goes exactly flat must be closed whatever the minimum says: a
        # residual dust position is not a band decision, it is a failure to exit.
        must_close = (target_notional == 0.0) & (held_notional != 0.0)
        trade = tradable & (must_close | (np.abs(gap) >= minimum))

        traded_notional = float(np.abs(np.where(trade, gap, 0.0)).sum())
        if traded_notional > 0:
            new_shares = np.where(trade, target_notional / np.where(tradable, p, 1.0), shares)
            for j in np.flatnonzero(trade):
                trades.append((idx[i], float(abs(gap[j]))))
            shares = new_shares

        short_exposure = float(np.abs(np.where(held_notional < 0, held_notional, 0.0)).sum())
        cost = (
            (cost_params.half_spread_bp + cost_params.slippage_bp) * BP * traded_notional
            + cost_params.borrow_annual / TRADING_DAYS * short_exposure
        )
        equity -= cost
        equity_series.append(equity)
        cost_series.append(cost)
        # Measured against the equity the session STARTED with, so the series compounds
        # to the equity path exactly. Dividing by post-P&L equity (the first version of
        # this line) quietly shrank every return and made a positive path look negative.
        ret_series.append((equity - eq_start) / eq_start if eq_start else 0.0)

    return Run(
        label=label,
        daily_return=pd.Series(ret_series, index=idx),
        equity=pd.Series(equity_series, index=idx),
        turnover_cost=pd.Series(cost_series, index=idx),
        trades=pd.Series([t[1] for t in trades], index=pd.DatetimeIndex([t[0] for t in trades])),
        notional=notional,
    )


def sharpe(daily: pd.Series) -> float:
    x = daily.to_numpy(dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * np.sqrt(TRADING_DAYS))


def annual_return(daily: pd.Series) -> float:
    years = len(daily) / TRADING_DAYS
    if years <= 0:
        return 0.0
    total = float((1.0 + daily.fillna(0.0)).prod())
    return float(total ** (1.0 / years) - 1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notional", type=float, default=1_000_000.0)
    parser.add_argument("--start", default=None, help="optional ISO start date")
    args = parser.parse_args(argv)

    close = load_universe_close(UNIVERSE, column="close")
    if args.start:
        close = close.loc[args.start:]

    desired = to_position_matrix(
        compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    )
    banded = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    target_w = shift_to_next_day(banded)

    results = [
        simulate(target_w, close, label, value, notional=args.notional)
        for label, value in VARIANTS
    ]

    base = results[0]
    years = len(base.daily_return) / TRADING_DAYS
    print(f"window: {base.daily_return.index[0].date()} -> {base.daily_return.index[-1].date()}"
          f"  ({years:.2f} years, {len(base.daily_return)} sessions)")
    print()
    header = f"{'minimum':<15}{'Sharpe':>8}{'ann ret':>10}{'vs $250':>10}{'trades/yr':>11}{'cost/yr':>10}{'avg trade':>11}"
    print(header)
    print("-" * len(header))
    for run in results:
        trades_per_year = len(run.trades) / years
        cost_per_year = float(run.turnover_cost.mean() * TRADING_DAYS)
        avg_trade = float(run.trades.mean()) if len(run.trades) else 0.0
        print(
            f"{run.label:<15}{sharpe(run.daily_return):>8.2f}"
            f"{annual_return(run.daily_return):>9.2%}"
            f"{annual_return(run.daily_return) - annual_return(base.daily_return):>+10.2%}"
            f"{trades_per_year:>11.0f}{cost_per_year:>10,.0f}{avg_trade:>11,.0f}"
        )
    print()
    print("tracking difference is annual return minus the flat-$250 run, same window and costs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
