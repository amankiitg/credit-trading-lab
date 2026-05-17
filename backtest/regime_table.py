"""Regime performance table — every strategy × every regime label.

Restricts each strategy's daily P&L to the days carrying a given
regime label and reports Sharpe / Sortino / maxDD / hit_rate.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.engine import BacktestResult
from backtest.metrics import max_drawdown, sharpe, sortino

REGIME_CLASSIFIERS: tuple[str, ...] = (
    "vol_regime", "equity_regime", "equity_credit_lag",
)

REGIME_TABLE_COLUMNS: tuple[str, ...] = (
    "strategy", "regime_classifier", "regime_label",
    "sharpe", "sortino", "max_drawdown", "hit_rate", "n_days", "n_trades",
)


def _regime_hit_rate(trades: pd.DataFrame, regime_days: pd.Series) -> tuple[float, int]:
    """Hit rate over trades whose entry signal fell on a regime day."""
    if trades.empty:
        return 0.0, 0
    entry_in = trades["entry_signal_date"].map(
        lambda d: bool(regime_days.get(d, False))
    )
    sub = trades[entry_in]
    n = int(len(sub))
    if n == 0:
        return 0.0, 0
    return float((sub["net_pnl"] > 0).mean()), n


def regime_performance(
    strategies: dict[str, BacktestResult],
    features: pd.DataFrame,
) -> pd.DataFrame:
    """One row per (strategy × regime_classifier × regime_label)."""
    rows: list[dict[str, object]] = []
    for strat_name, result in strategies.items():
        daily = result.daily_pnl
        for classifier in REGIME_CLASSIFIERS:
            labels = features[classifier].astype("object")
            for label in sorted(labels.dropna().unique()):
                mask = (labels == label)
                regime_pnl = daily[mask.reindex(daily.index, fill_value=False)]
                hit, n_tr = _regime_hit_rate(result.trades, mask)
                rows.append({
                    "strategy": strat_name,
                    "regime_classifier": classifier,
                    "regime_label": str(label),
                    "sharpe": sharpe(regime_pnl),
                    "sortino": sortino(regime_pnl),
                    "max_drawdown": max_drawdown(regime_pnl),
                    "hit_rate": hit,
                    "n_days": int(len(regime_pnl)),
                    "n_trades": n_tr,
                })
    return pd.DataFrame(rows, columns=list(REGIME_TABLE_COLUMNS))


def save_regime_performance(
    table: pd.DataFrame,
    path: str = "data/results/regime_performance.parquet",
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(path, index=False)
