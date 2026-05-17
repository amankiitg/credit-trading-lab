"""A/B comparison — Strategy A (no filter) vs Strategy B (equity_first).

The headline Sprint-5 test (C27): is the incremental Sharpe
ΔS = Sharpe(B) − Sharpe(A) positive, and does its bootstrap 95% CI
exclude zero?
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult, run
from backtest.metrics import Summary, sharpe, summary
from execution.position import run_state_machine
from signals.rv_signals import build_all_residuals, trailing_zscore

WARMUP: int = 378
Z_WINDOW: int = 63
NOTIONAL: float = 1_000_000.0
FILL_LAG: int = 1
BOOTSTRAP_SEED: int = 20260516

# pair → the regime column used as the Strategy-B entry gate
EQUITY_FIRST = "equity_first"


@dataclass(frozen=True)
class StrategySpec:
    pair: str            # rv_hy_ig | rv_credit_rates | rv_xterm
    method: str          # ols | kalman | dv01
    gated: bool          # True = Strategy-B style (equity_first gate)
    entry: float = 2.0
    exit_t: float = 0.5
    stop: float = 4.0


def build_strategy(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    spec: StrategySpec,
    notional: float = NOTIONAL,
    fill_lag: int = FILL_LAG,
    warmup: int = WARMUP,
) -> BacktestResult:
    """Build + backtest one strategy from a pre-computed residual set."""
    residual, hedge_ratio = residuals[spec.pair][spec.method]
    z = trailing_zscore(residual, window=Z_WINDOW)

    gate: pd.Series | None = None
    if spec.gated:
        gate = features["equity_credit_lag"].astype("object").astype(str) == EQUITY_FIRST

    positions = run_state_machine(
        z, entry=spec.entry, exit_t=spec.exit_t, stop=spec.stop, regime_gate=gate
    )
    # Block all entries during the warmup window (residual is NaN there
    # anyway, but make it explicit so no warmup trade slips through).
    if warmup > 0:
        positions.iloc[:warmup] = 0

    return run(residual, positions, hedge_ratio, notional=notional, fill_lag=fill_lag)


# ----------------------------------------------------------- bootstrap


def _stationary_indices(length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano stationary bootstrap index vector (circular)."""
    restart = rng.random(length) < (1.0 / block)
    starts = rng.integers(0, length, size=length)
    idx = np.empty(length, dtype="int64")
    cur = int(starts[0])
    for t in range(length):
        if restart[t]:
            cur = int(starts[t])
        idx[t] = cur
        cur = (cur + 1) % length
    return idx


def block_bootstrap_delta_sharpe(
    pnl_a: pd.Series,
    pnl_b: pd.Series,
    n: int = 1000,
    block: int = 21,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, float]:
    """Stationary block bootstrap of ΔS = Sharpe(B) − Sharpe(A).

    A and B are resampled jointly (same index vector each draw) so the
    distribution of ΔS accounts for their correlation.

    Returns the point estimate (on the original series), the 2.5/97.5
    percentile CI, and the fraction of resamples with ΔS > 0.
    """
    a = pnl_a.to_numpy(dtype="float64")
    b = pnl_b.to_numpy(dtype="float64")
    if len(a) != len(b):
        raise ValueError("pnl_a and pnl_b must be the same length")
    rng = np.random.default_rng(seed)
    L = len(a)
    root = np.sqrt(252.0)

    def _sharpe(x: np.ndarray) -> float:
        sd = x.std(ddof=1)
        return 0.0 if sd == 0.0 else float(x.mean() / sd * root)

    point = _sharpe(b) - _sharpe(a)
    deltas = np.empty(n, dtype="float64")
    for r in range(n):
        idx = _stationary_indices(L, block, rng)
        deltas[r] = _sharpe(b[idx]) - _sharpe(a[idx])

    return {
        "delta_sharpe": point,
        "ci_lo": float(np.percentile(deltas, 2.5)),
        "ci_hi": float(np.percentile(deltas, 97.5)),
        "frac_positive": float(np.mean(deltas > 0.0)),
    }


# ----------------------------------------------------------- A/B driver


@dataclass(frozen=True)
class ABResult:
    strategy_a: BacktestResult
    strategy_b: BacktestResult
    summary_a: Summary
    summary_b: Summary
    bootstrap: dict[str, float]

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"A (no filter)": self.summary_a.as_dict(),
             "B (equity_first)": self.summary_b.as_dict()}
        ).T


def compare(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str = "rv_hy_ig",
    method: str = "ols",
    entry: float = 2.0,
    exit_t: float = 0.5,
    stop: float = 4.0,
    n_boot: int = 1000,
    block: int = 21,
    seed: int = BOOTSTRAP_SEED,
) -> ABResult:
    """Run the A/B comparison and bootstrap the incremental Sharpe."""
    spec_a = StrategySpec(pair, method, gated=False, entry=entry, exit_t=exit_t, stop=stop)
    spec_b = StrategySpec(pair, method, gated=True, entry=entry, exit_t=exit_t, stop=stop)
    res_a = build_strategy(features, residuals, spec_a)
    res_b = build_strategy(features, residuals, spec_b)
    boot = block_bootstrap_delta_sharpe(
        res_a.daily_pnl, res_b.daily_pnl, n=n_boot, block=block, seed=seed
    )
    return ABResult(
        strategy_a=res_a,
        strategy_b=res_b,
        summary_a=summary(res_a.daily_pnl, res_a.trades),
        summary_b=summary(res_b.daily_pnl, res_b.trades),
        bootstrap=boot,
    )


# ----------------------------------------------------------- walk-forward

# Pre-registered grid (PRD §C30) — reused for the OOS calibration.
GRID_ENTRY: tuple[float, ...] = (1.5, 2.0, 2.5)
GRID_EXIT: tuple[float, ...] = (0.25, 0.5, 1.0)
GRID_STOP: tuple[float, ...] = (3.0, 4.0, 5.0)
OOS_SPLIT: str = "2018-12-31"


@dataclass(frozen=True)
class WalkForwardResult:
    train_entry: float
    train_exit: float
    train_stop: float
    train_b_sharpe: float
    oos_delta_sharpe: float
    oos_summary_a: Summary
    oos_summary_b: Summary


def walk_forward(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str = "rv_hy_ig",
    method: str = "ols",
    oos_split: str = OOS_SPLIT,
) -> WalkForwardResult:
    """Grid-calibrate thresholds on the train window, lock, test OOS.

    Thresholds are chosen to maximise Strategy B's net Sharpe on
    2007-01-01 → ``oos_split``. They are then frozen and the A/B
    incremental Sharpe is reported on ``oos_split`` → end. No
    test-window data touches the calibration.
    """
    split = pd.Timestamp(oos_split)

    best: tuple[float, float, float, float] | None = None  # (entry, exit, stop, train_sharpe)
    for entry in GRID_ENTRY:
        for exit_t in GRID_EXIT:
            for stop in GRID_STOP:
                spec_b = StrategySpec(pair, method, gated=True,
                                      entry=entry, exit_t=exit_t, stop=stop)
                res_b = build_strategy(features, residuals, spec_b)
                train_pnl = res_b.daily_pnl.loc[:split]
                s = sharpe(train_pnl)
                if best is None or s > best[3]:
                    best = (entry, exit_t, stop, s)

    assert best is not None
    entry, exit_t, stop, train_sharpe = best

    # Locked thresholds → A/B on the OOS window.
    spec_a = StrategySpec(pair, method, gated=False, entry=entry, exit_t=exit_t, stop=stop)
    spec_b = StrategySpec(pair, method, gated=True, entry=entry, exit_t=exit_t, stop=stop)
    res_a = build_strategy(features, residuals, spec_a)
    res_b = build_strategy(features, residuals, spec_b)

    oos = features.index > split
    pnl_a_oos = res_a.daily_pnl.loc[oos]
    pnl_b_oos = res_b.daily_pnl.loc[oos]
    trades_a_oos = res_a.trades[res_a.trades["entry_fill_date"] > split]
    trades_b_oos = res_b.trades[res_b.trades["entry_fill_date"] > split]

    return WalkForwardResult(
        train_entry=entry,
        train_exit=exit_t,
        train_stop=stop,
        train_b_sharpe=train_sharpe,
        oos_delta_sharpe=sharpe(pnl_b_oos) - sharpe(pnl_a_oos),
        oos_summary_a=summary(pnl_a_oos, trades_a_oos),
        oos_summary_b=summary(pnl_b_oos, trades_b_oos),
    )


# ----------------------------------------------------------- robustness

SUBPERIOD_SPLIT: str = "2016-09-15"


def _delta_sharpe(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str,
    method: str,
    entry: float,
    exit_t: float,
    stop: float,
    window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> float:
    """Point-estimate ΔS = Sharpe(B) − Sharpe(A) for one threshold cell."""
    spec_a = StrategySpec(pair, method, gated=False, entry=entry, exit_t=exit_t, stop=stop)
    spec_b = StrategySpec(pair, method, gated=True, entry=entry, exit_t=exit_t, stop=stop)
    pnl_a = build_strategy(features, residuals, spec_a).daily_pnl
    pnl_b = build_strategy(features, residuals, spec_b).daily_pnl
    if window is not None:
        pnl_a = pnl_a.loc[window[0]:window[1]]
        pnl_b = pnl_b.loc[window[0]:window[1]]
    return sharpe(pnl_b) - sharpe(pnl_a)


def parameter_grid(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str = "rv_hy_ig",
    method: str = "ols",
) -> pd.DataFrame:
    """ΔS over the 27-cell entry × exit × stop grid (C30)."""
    rows = []
    for entry in GRID_ENTRY:
        for exit_t in GRID_EXIT:
            for stop in GRID_STOP:
                rows.append({
                    "entry": entry, "exit": exit_t, "stop": stop,
                    "delta_sharpe": _delta_sharpe(
                        features, residuals, pair, method, entry, exit_t, stop
                    ),
                })
    return pd.DataFrame(rows)


def subperiod_split(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str = "rv_hy_ig",
    method: str = "ols",
    entry: float = 2.0,
    exit_t: float = 0.5,
    stop: float = 4.0,
    split: str = SUBPERIOD_SPLIT,
) -> dict[str, float]:
    """ΔS in each half of the sample (C30)."""
    sp = pd.Timestamp(split)
    first = (features.index[0], sp)
    second = (sp + pd.Timedelta(days=1), features.index[-1])
    return {
        "first_half": _delta_sharpe(features, residuals, pair, method,
                                    entry, exit_t, stop, window=first),
        "second_half": _delta_sharpe(features, residuals, pair, method,
                                     entry, exit_t, stop, window=second),
    }


def hedge_method_panel(
    features: pd.DataFrame,
    residuals: dict[str, dict[str, tuple[pd.Series, pd.Series]]],
    pair: str = "rv_hy_ig",
) -> pd.DataFrame:
    """A/B summary under each of the three hedge methods (reported only)."""
    rows = []
    for method in ("ols", "kalman", "dv01"):
        ab = compare(features, residuals, pair=pair, method=method, n_boot=200)
        rows.append({
            "method": method,
            "sharpe_a": ab.summary_a.sharpe,
            "sharpe_b": ab.summary_b.sharpe,
            "delta_sharpe": ab.bootstrap["delta_sharpe"],
            "ci_lo": ab.bootstrap["ci_lo"],
            "ci_hi": ab.bootstrap["ci_hi"],
        })
    return pd.DataFrame(rows)


def load_inputs() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load features + credit data and compute all 9 residual sets."""
    import pycredit

    features = pd.read_parquet("data/processed/features.parquet")
    credit = pd.read_parquet("data/raw/credit_market_data.parquet")
    residuals = build_all_residuals(features, credit, pycredit)
    return features, residuals


def save_trade_ledger(ab: ABResult, path: str = "data/results/backtest_trades.parquet") -> None:
    a = ab.strategy_a.trades.copy()
    a.insert(0, "strategy", "A_no_filter")
    b = ab.strategy_b.trades.copy()
    b.insert(0, "strategy", "B_equity_first")
    out = pd.concat([a, b], ignore_index=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)


if __name__ == "__main__":
    feats, resid = load_inputs()
    ab = compare(feats, resid)
    print(ab.table().to_string())
    print()
    print("bootstrap ΔS:", ab.bootstrap)
    save_trade_ledger(ab)
