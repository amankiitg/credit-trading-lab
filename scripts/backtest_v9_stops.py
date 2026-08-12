"""v9.1 T2: Stop-ladder overlay backtest -- baseline vs default-parameter ladder.

Runs the v8.2 pipeline (trend -> band -> shift_to_next_day) as baseline,
then the same with the v9.1 T1 stop overlay applied post-band, both net
of the existing cost model. Saves equity curves, drawdown curves, and a
metrics table to sprints/v9.1/artifacts/.

Usage:
    python scripts/backtest_v9_stops.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest.multi_asset import (
    MultiAssetResult,
    annualized_return,
    annualized_turnover,
    annualized_vol,
    run_multi_asset,
)
from backtest.metrics import max_drawdown, sharpe
from execution.costs import CostParams
from risk.stop_loss import apply_stop_overlay
from signals.etf_universe import UNIVERSE, load_universe_close
from signals.trend_signal import (
    apply_rebalance_control,
    compute_trend,
    shift_to_next_day,
    to_position_matrix,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest_v9_stops")

ARTIFACT_DIR = Path("sprints/v9.1/artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

NOTIONAL = 1_000_000.0
COST_PARAMS = CostParams()  # v6.5 defaults
TRADING_DAYS = 252

# Evaluation window
EVAL_START = "2015-01-01"
# Subperiods for gate F3
SUBPERIODS = [
    ("2015-01-01", "2018-12-31"),
    ("2019-01-01", "2022-12-31"),
    ("2023-01-01", None),  # None = to end
]

SEED = 42  # logged for reproducibility


def _calmar(daily_pnl: pd.Series, notional: float = NOTIONAL) -> float:
    """Calmar ratio: annualized return / |max drawdown| (in return units)."""
    ann_ret = annualized_return(daily_pnl, notional)
    dd = max_drawdown(daily_pnl)
    dd_return = dd / notional
    if abs(dd_return) < 1e-12:
        return float("inf") if ann_ret > 0 else 0.0
    return ann_ret / abs(dd_return)


def _net_sharpe(daily_pnl: pd.Series) -> float:
    """Net Sharpe on dollar P&L (same as backtest.metrics.sharpe but named clearly)."""
    return sharpe(daily_pnl)


def _metrics_row(
    label: str,
    result: MultiAssetResult,
    target: pd.DataFrame,
    notional: float = NOTIONAL,
) -> dict:
    """Compute one row of the metrics table."""
    return {
        "label": label,
        "net_sharpe": round(_net_sharpe(result.daily_pnl), 4),
        "max_dd": round(max_drawdown(result.daily_pnl), 2),
        "max_dd_return": round(max_drawdown(result.daily_pnl) / notional, 6),
        "calmar": round(_calmar(result.daily_pnl, notional), 4),
        "ann_return": round(annualized_return(result.daily_pnl, notional), 6),
        "ann_vol": round(annualized_vol(result.daily_pnl, notional), 6),
        "ann_turnover": round(annualized_turnover(target), 4),
    }


def _subset_metrics(
    result: MultiAssetResult,
    target: pd.DataFrame,
    daily_pnl: pd.Series,
    start: str,
    end: str | None,
    label_prefix: str,
) -> dict:
    """Compute metrics for a subperiod slice."""
    mask = daily_pnl.index >= start
    if end is not None:
        mask &= daily_pnl.index <= end
    sub_pnl = daily_pnl.loc[mask]
    sub_target = target.loc[target.index.isin(sub_pnl.index)]
    sub_result = MultiAssetResult(
        daily_pnl=sub_pnl,
        equity=sub_pnl.cumsum(),
        turnover_cost=result.turnover_cost.reindex(sub_pnl.index, fill_value=0.0),
        borrow_cost=result.borrow_cost.reindex(sub_pnl.index, fill_value=0.0),
        daily_return=sub_pnl / NOTIONAL,
    )
    row = _metrics_row(label_prefix, sub_result, sub_target)
    return row


def _hit_rate_stat(held: pd.DataFrame, close: pd.DataFrame, stop_mult: pd.DataFrame) -> dict:
    """Compute hit-rate: fraction of REDUCED/STOPPED days where the next 21d return
    (had the position been held at full size) was negative."""
    common_idx = held.index.intersection(close.index)
    common_cols = [c for c in held.columns if c in close.columns]
    
    held_aligned = held.loc[common_idx, common_cols]
    close_aligned = close.loc[common_idx, common_cols]
    mult_aligned = stop_mult.reindex(index=common_idx, columns=common_cols)
    
    fwd_21d = close_aligned.pct_change(21).shift(-21)  # 21d forward return
    
    triggers = 0
    successful = 0
    
    for ticker in common_cols:
        for i in range(len(common_idx)):
            m = mult_aligned.iloc[i][ticker]
            if pd.isna(m) or m == 1.0:
                continue
            # Trigger day: multiplier < 1.0 (REDUCED or STOPPED)
            triggers += 1
            # Check: was the 21d forward return negative?
            fwd_ret = fwd_21d.iloc[i][ticker]
            if pd.notna(fwd_ret) and fwd_ret < 0:
                successful += 1
    
    if triggers == 0:
        return {"triggers": 0, "successful": 0, "hit_rate": None}
    
    hit_rate = successful / triggers
    # Binomial standard error
    se = np.sqrt(hit_rate * (1 - hit_rate) / triggers)
    ci_lower = max(0, hit_rate - 1.96 * se)
    ci_upper = min(1, hit_rate + 1.96 * se)
    
    return {
        "triggers": triggers,
        "successful": successful,
        "hit_rate": round(hit_rate, 4),
        "ci_95": (round(ci_lower, 4), round(ci_upper, 4)),
    }


def main() -> int:
    logger.info("=== v9.1 T2: Stop-ladder overlay backtest ===")
    logger.info("seed=%d", SEED)
    np.random.seed(SEED)

    # -- 1. Load data --
    logger.info("Loading universe closes...")
    try:
        close = load_universe_close()
    except FileNotFoundError:
        logger.error("No cached close data. Run `python scripts/run_signal.py` first to ingest.")
        return 1
    
    close = close.sort_index()
    logger.info("Close data: %d rows, %d tickers, %s -> %s",
                 len(close), len(close.columns),
                 close.index[0].date(), close.index[-1].date())
    logger.info("Universe: %s", list(close.columns))

    # -- 2. Run v8.2 pipeline (baseline) --
    logger.info("Computing trend signal (L=120, long_short=True, k_dead_zone=0.5)...")
    tidy = compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)
    
    # Extract sigma matrix for stop overlay
    sigma_matrix = tidy.pivot(index="date", columns="ticker", values="sigma").sort_index()
    
    desired = to_position_matrix(tidy)
    held = apply_rebalance_control(desired, rebal_freq=1, band_pct=0.20)
    target_baseline = shift_to_next_day(held)
    
    logger.info("Desired weights: %d rows, held: %d rows, target (baseline): %d rows",
                 len(desired), len(held), len(target_baseline))

    # -- 3. Run stop overlay --
    logger.info("Applying stop overlay (k1=1.5, k2=2.5, h=0.5, m_reduced=0.5)...")
    multipliers, states, z_scores = apply_stop_overlay(held, close, sigma_matrix)
    
    # Final weights = multipliers * held_weights (elementwise)
    # For days where multiplier is NaN (pre-entry), weight stays as held weight
    mult_filled = multipliers.fillna(1.0)
    overlay_held = held * mult_filled
    target_overlay = shift_to_next_day(overlay_held)
    
    logger.info("Overlay: multiplier non-1.0 fraction: %.4f",
                 (mult_filled != 1.0).sum().sum() / mult_filled.notna().sum().sum()
                 if mult_filled.notna().sum().sum() > 0 else 0)
    
    # Episode/state stats
    state_counts = states.apply(pd.Series.value_counts).fillna(0).astype(int)
    logger.info("State distribution:\n%s", state_counts.to_string())

    # -- 4. Backtest both --
    logger.info("Running backtest (baseline)...")
    result_baseline = run_multi_asset(target_baseline, close, NOTIONAL, COST_PARAMS)
    
    logger.info("Running backtest (overlay)...")
    result_overlay = run_multi_asset(target_overlay, close, NOTIONAL, COST_PARAMS)

    # -- 5. Restrict to evaluation window --
    baseline_pnl = result_baseline.daily_pnl.loc[EVAL_START:]
    overlay_pnl = result_overlay.daily_pnl.loc[EVAL_START:]
    baseline_target = target_baseline.loc[target_baseline.index >= EVAL_START]
    overlay_target = target_overlay.loc[target_overlay.index >= EVAL_START]
    
    logger.info("Evaluation window: %s -> %s, %d days",
                 baseline_pnl.index[0].date(), baseline_pnl.index[-1].date(),
                 len(baseline_pnl))

    # -- 6. Build metrics table --
    rows = []
    rows.append(_metrics_row("baseline", result_baseline, baseline_target))
    rows.append(_metrics_row("overlay", result_overlay, overlay_target))
    
    # Subperiods
    for sp_start, sp_end in SUBPERIODS:
        rows.append(_subset_metrics(result_baseline, baseline_target, baseline_pnl,
                                    sp_start, sp_end, f"baseline_{sp_start[:4]}-{sp_end[:4] if sp_end else 'end'}"))
    for sp_start, sp_end in SUBPERIODS:
        rows.append(_subset_metrics(result_overlay, overlay_target, overlay_pnl,
                                    sp_start, sp_end, f"overlay_{sp_start[:4]}-{sp_end[:4] if sp_end else 'end'}"))

    metrics_df = pd.DataFrame(rows)
    metrics_path = ARTIFACT_DIR / "stops_metrics.csv"
    metrics_df.to_csv(metrics_path, index=False)
    logger.info("Metrics saved to %s", metrics_path)
    print("\n=== Metrics Table ===")
    print(metrics_df.to_string(index=False))

    # -- 7. Hit-rate stat --
    hit = _hit_rate_stat(held, close, multipliers)
    logger.info("Hit-rate: %d triggers, %d successful, rate=%.4f, CI=(%.4f, %.4f)",
                 hit["triggers"], hit["successful"],
                 hit["hit_rate"] if hit["hit_rate"] is not None else float("nan"),
                 hit["ci_95"][0] if hit["ci_95"] is not None else float("nan"),
                 hit["ci_95"][1] if hit["ci_95"] is not None else float("nan"))

    # -- 8. Gate inputs (F1-F3) --
    b_net_sharpe = rows[0]["net_sharpe"]
    o_net_sharpe = rows[1]["net_sharpe"]
    b_max_dd = rows[0]["max_dd_return"]
    o_max_dd = rows[1]["max_dd_return"]
    b_calmar = rows[0]["calmar"]
    o_calmar = rows[1]["calmar"]

    print("\n=== Gate Inputs ===")
    print(f"F1: Overlay Sharpe / Baseline Sharpe = {o_net_sharpe:.4f} / {b_net_sharpe:.4f} = {o_net_sharpe/b_net_sharpe:.4f} (reject if < 0.80)")
    print(f"F2: Baseline max DD = {b_max_dd:.6f}, Overlay max DD = {o_max_dd:.6f}")
    dd_pct_reduction = (abs(b_max_dd) - abs(o_max_dd)) / abs(b_max_dd) * 100 if abs(b_max_dd) > 1e-12 else 0
    print(f"     DD reduction = {dd_pct_reduction:.1f}% (reject if < 10%)")
    print(f"     Baseline Calmar = {b_calmar:.4f}, Overlay Calmar = {o_calmar:.4f}")
    print(f"F3: Subperiod Sharpe rows: see metrics table above")

    # -- 9. Plots --
    # Equity curves
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    b_eq = result_baseline.equity.loc[EVAL_START:]
    o_eq = result_overlay.equity.loc[EVAL_START:]
    
    ax1.plot(b_eq.index, b_eq.values / NOTIONAL, label="Baseline (v8.2)", linewidth=1.0, alpha=0.85)
    ax1.plot(o_eq.index, o_eq.values / NOTIONAL, label="Overlay (stop ladder)", linewidth=1.0, alpha=0.85)
    ax1.set_ylabel("Cumulative Return")
    ax1.set_title(f"Equity Curves: Baseline vs Stop-Ladder Overlay\n{EVAL_START} to {close.index[-1].date()}, {len(close.columns)}-name universe")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=0, color="black", linewidth=0.5)

    # Drawdown curves
    b_dd = b_eq / NOTIONAL
    o_dd = o_eq / NOTIONAL
    b_dd_curve = b_dd - b_dd.cummax()
    o_dd_curve = o_dd - o_dd.cummax()
    
    ax2.fill_between(b_dd_curve.index, 0, b_dd_curve.values, alpha=0.3, label="Baseline DD", color="tab:blue")
    ax2.fill_between(o_dd_curve.index, 0, o_dd_curve.values, alpha=0.3, label="Overlay DD", color="tab:orange")
    ax2.plot(b_dd_curve.index, b_dd_curve.values, linewidth=0.8, color="tab:blue")
    ax2.plot(o_dd_curve.index, o_dd_curve.values, linewidth=0.8, color="tab:orange")
    ax2.set_ylabel("Drawdown (return units)")
    ax2.set_xlabel("Date")
    ax2.set_title("Drawdown Curves")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    equity_path = ARTIFACT_DIR / "stops_equity_drawdown.png"
    fig.savefig(equity_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Equity/drawdown plot saved to %s", equity_path)

    # Histogram of daily returns
    fig2, ax_hist = plt.subplots(figsize=(10, 5))
    b_rets = result_baseline.daily_pnl.loc[EVAL_START:] / NOTIONAL
    o_rets = result_overlay.daily_pnl.loc[EVAL_START:] / NOTIONAL
    ax_hist.hist(b_rets, bins=80, alpha=0.5, label="Baseline", density=True)
    ax_hist.hist(o_rets, bins=80, alpha=0.5, label="Overlay", density=True)
    ax_hist.set_xlabel("Daily Return")
    ax_hist.set_ylabel("Density")
    ax_hist.set_title(f"Daily Return Distribution\n{EVAL_START} to {close.index[-1].date()}")
    ax_hist.legend()
    ax_hist.grid(True, alpha=0.3)
    hist_path = ARTIFACT_DIR / "stops_return_distribution.png"
    fig2.savefig(hist_path, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    logger.info("Return distribution plot saved to %s", hist_path)

    # Signal distribution: z-score histogram
    fig3, ax_z = plt.subplots(figsize=(10, 5))
    z_flat = z_scores.to_numpy().flatten()
    z_flat = z_flat[np.isfinite(z_flat)]
    ax_z.hist(z_flat, bins=100, alpha=0.7, color="tab:green")
    ax_z.axvline(x=-1.5, color="red", linestyle="--", label="REDUCED threshold (k1=-1.5)")
    ax_z.axvline(x=-2.5, color="darkred", linestyle="--", label="STOPPED threshold (k2=-2.5)")
    ax_z.set_xlabel("Drawdown z-score")
    ax_z.set_ylabel("Frequency")
    ax_z.set_title(f"Drawdown z-Score Distribution\nFull history, {len(close.columns)} tickers")
    ax_z.legend()
    ax_z.grid(True, alpha=0.3)
    z_path = ARTIFACT_DIR / "stops_zscore_distribution.png"
    fig3.savefig(z_path, dpi=150, bbox_inches="tight")
    plt.close(fig3)
    logger.info("z-score distribution plot saved to %s", z_path)

    # =================================================================
    # T4: Sanity baselines -- naive ladder and shuffled-vol ladder
    # =================================================================
    logger.info("=== T4: Sanity baselines ===")

    # -- Naive ladder: fixed -5%/-10% thresholds in return units --
    # Same hysteresis logic as vol-ladder but r_i(t) compared directly
    # to fixed thresholds instead of z-scores.
    NAIVE_REDUCE_THRESH = -0.05  # -5%
    NAIVE_STOP_THRESH   = -0.10  # -10%

    def _naive_episode_state_machine(
        r: np.ndarray,
        reduce_thresh: float,
        stop_thresh: float,
        h_return: float = 0.01,  # 1% hysteresis in return units
        m_reduced: float = 0.5,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Same state logic but on raw r (not z). h_return maps the hysteresis.
        For consistency with the vol-ladder: z-based h=0.5 ≈ return-based h=sigma_m*0.5."""
        n = len(r)
        multipliers = np.ones(n)
        states = np.zeros(n, dtype=np.int32)
        current_M = 0.0
        current_state = 0

        for t in range(n):
            current_M = max(current_M, r[t]) if t > 0 else max(0.0, r[t])
            D = r[t] - current_M  # drawdown in return units

            if current_state == 0:  # NORMAL
                if D <= stop_thresh:
                    current_state = 2
                elif D <= reduce_thresh:
                    current_state = 1
            elif current_state == 1:  # REDUCED
                if D <= stop_thresh:
                    current_state = 2
                elif D >= reduce_thresh + h_return:
                    current_state = 0
            else:  # STOPPED
                if D >= stop_thresh + h_return:
                    current_state = 1

            states[t] = current_state
            if current_state == 0:
                multipliers[t] = 1.0
            elif current_state == 1:
                multipliers[t] = m_reduced
            else:
                multipliers[t] = 0.0

        return multipliers, states

    # Build naive multiplier DataFrame
    naive_mult = pd.DataFrame(np.nan, index=held.index, columns=held.columns)
    for ticker in held.columns:
        w = held[ticker].to_numpy(dtype="float64")
        p = close[ticker].to_numpy(dtype="float64")
        n = len(w)
        mult_col = np.full(n, np.nan)
        i = 0
        while i < n:
            if np.isnan(w[i]) or w[i] == 0.0:
                i += 1
                continue
            entry_idx = i
            entry_side = np.sign(w[i])
            entry_price = p[i]
            if np.isnan(entry_price) or entry_price <= 0:
                i += 1
                continue
            ep_indices = []
            ep_r = []
            while i < n:
                if np.isnan(p[i]):
                    break
                if i > entry_idx:
                    cur_side = np.sign(w[i]) if (not np.isnan(w[i]) and w[i] != 0.0) else 0.0
                    if cur_side != entry_side:
                        break
                r_val = entry_side * (p[i] / entry_price - 1.0)
                ep_indices.append(i)
                ep_r.append(r_val)
                i += 1
            if len(ep_indices) == 0:
                continue
            ep_mult, _ = _naive_episode_state_machine(
                np.array(ep_r), NAIVE_REDUCE_THRESH, NAIVE_STOP_THRESH,
            )
            for j, idx in enumerate(ep_indices):
                mult_col[idx] = ep_mult[j]
        naive_mult[ticker] = mult_col

    naive_filled = naive_mult.fillna(1.0)
    target_naive = shift_to_next_day(held * naive_filled)
    result_naive = run_multi_asset(target_naive, close, NOTIONAL, COST_PARAMS)
    rows.append(_metrics_row("naive_-5%_-10%", result_naive,
                              target_naive.loc[target_naive.index >= EVAL_START]))

    # -- Placebo: shuffled-vol ladder --
    # Each ticker uses another ticker's sigma (derangement, seed=123).
    # Assert the mapping is a derangement before running.
    rng_placebo = np.random.RandomState(123)
    tickers = list(sigma_matrix.columns)
    sigma_shuffled_vals = sigma_matrix.to_numpy().copy()
    # Shuffle columns (derangement)
    perm = rng_placebo.permutation(len(tickers))
    # Ensure it's a derangement (no element stays in place)
    while any(perm[i] == i for i in range(len(tickers))):
        perm = rng_placebo.permutation(len(tickers))
    sigma_shuffled = pd.DataFrame(
        sigma_shuffled_vals[:, perm],
        index=sigma_matrix.index,
        columns=tickers,
    )
    # Assert the mapping is a derangement in code
    for i, orig_ticker in enumerate(tickers):
        assert perm[i] != i, f"Placebo sigma for {orig_ticker} maps to itself -- not a derangement!"
    logger.info("Placebo sigma derangement recorded (seed=123): %s",
                 {tickers[i]: tickers[perm[i]] for i in range(len(tickers))})

    mult_placebo, _, _ = apply_stop_overlay(held, close, sigma_shuffled)
    placebo_filled = mult_placebo.fillna(1.0)
    target_placebo = shift_to_next_day(held * placebo_filled)
    result_placebo = run_multi_asset(target_placebo, close, NOTIONAL, COST_PARAMS)
    rows.append(_metrics_row("placebo_shuffled_vol", result_placebo,
                              target_placebo.loc[target_placebo.index >= EVAL_START]))

    # Rewrite metrics CSV with all rows (baseline, overlay, naive, placebo + subperiods)
    metrics_df = pd.DataFrame(rows)
    metrics_df.to_csv(metrics_path, index=False)
    print("\n=== Updated Metrics Table (T4) ===")
    print(metrics_df.to_string(index=False))

    # F5 comparison
    dd_baseline = abs(rows[0]["max_dd_return"])
    dd_overlay = abs(rows[1]["max_dd_return"])
    dd_naive = abs(rows[2]["max_dd_return"])
    dd_improvement_overlay = dd_baseline - dd_overlay
    dd_improvement_naive = dd_baseline - dd_naive
    print(f"\n=== F5 Comparison ===")
    print(f"Vol-ladder DD improvement: {dd_improvement_overlay:.6f}")
    print(f"Naive -5%/-10% DD improvement: {dd_improvement_naive:.6f}")
    if dd_improvement_overlay > 0:
        ratio = dd_improvement_naive / dd_improvement_overlay if dd_improvement_overlay != 0 else float("inf")
        print(f"Naive / Vol-ladder DD improvement ratio: {ratio:.4f}")
        print(f"F5: Naive achieves {ratio*100:.1f}% of vol-ladder DD benefit (reject if >= 90%)")
    else:
        print("F5: Vol-ladder did not improve DD -- naive comparison not meaningful")

    # =================================================================
    # T5: Parameter sensitivity grid (falsification input F4, not tuning)
    # =================================================================
    logger.info("=== T5: Parameter sensitivity grid ===")
    k1_vals = [1.0, 1.5, 2.0]
    k2_vals = [2.0, 2.5, 3.0]
    h_vals = [0.25, 0.5]
    m_reduced_val = 0.5

    grid_results = []
    for k1 in k1_vals:
        for k2 in k2_vals:
            if k2 <= k1:
                continue  # k2 > k1 cells only
            for h_val in h_vals:
                logger.info("Grid cell: k1=%.1f, k2=%.1f, h=%.2f", k1, k2, h_val)
                mult_grid, _, _ = apply_stop_overlay(held, close, sigma_matrix,
                                                     k1=k1, k2=k2, h=h_val,
                                                     m_reduced=m_reduced_val)
                grid_filled = mult_grid.fillna(1.0)
                target_grid = shift_to_next_day(held * grid_filled)
                result_grid = run_multi_asset(target_grid, close, NOTIONAL, COST_PARAMS)
                grid_pnl = result_grid.daily_pnl.loc[EVAL_START:]
                grid_target = target_grid.loc[target_grid.index >= EVAL_START]

                grid_results.append({
                    "k1": k1,
                    "k2": k2,
                    "h": h_val,
                    "net_sharpe": round(_net_sharpe(grid_pnl), 4),
                    "max_dd_return": round(max_drawdown(grid_pnl) / NOTIONAL, 6),
                    "calmar": round(_calmar(grid_pnl), 4),
                    "ann_return": round(annualized_return(grid_pnl), 6),
                    "ann_vol": round(annualized_vol(grid_pnl), 6),
                    "ann_turnover": round(annualized_turnover(grid_target), 4),
                })

    grid_df = pd.DataFrame(grid_results)
    grid_path = ARTIFACT_DIR / "stops_grid.csv"
    grid_df.to_csv(grid_path, index=False)
    print("\n=== Parameter Sensitivity Grid (T5) ===")
    print(grid_df.to_string(index=False))

    # F4 plateau verdict
    default_sharpe = grid_df[
        (grid_df["k1"] == 1.5) & (grid_df["k2"] == 2.5) & (grid_df["h"] == 0.5)
    ]["net_sharpe"].values[0]
    default_dd = grid_df[
        (grid_df["k1"] == 1.5) & (grid_df["k2"] == 2.5) & (grid_df["h"] == 0.5)
    ]["max_dd_return"].values[0]

    # Neighboring cells: cells where |k1-1.5| <= 0.5, |k2-2.5| <= 0.5, |h-0.5| <= 0.25
    neighbors = grid_df[
        (grid_df["k1"] != 1.5) | (grid_df["k2"] != 2.5) | (grid_df["h"] != 0.5)
    ]
    # Check if each neighbor has the SAME qualitative result (DD improvement without F1-level Sharpe loss)
    b_sharpe = rows[0]["net_sharpe"]
    f1_threshold = 0.80 * b_sharpe
    dd_baseline_val = rows[0]["max_dd_return"]

    same_qualitative = 0
    total_neighbors = len(neighbors)
    for _, row in neighbors.iterrows():
        sharpe_ok = row["net_sharpe"] >= f1_threshold
        dd_better = abs(row["max_dd_return"]) < abs(dd_baseline_val)
        if sharpe_ok and dd_better:
            same_qualitative += 1

    plateau_fraction = same_qualitative / total_neighbors if total_neighbors > 0 else 0
    print(f"\n=== F4 Plateau Verdict ===")
    print(f"Default (k1=1.5, k2=2.5, h=0.5): Sharpe={default_sharpe:.4f}, DD={default_dd:.6f}")
    print(f"Neighboring cells with same qualitative result: {same_qualitative}/{total_neighbors} = {plateau_fraction:.2%}")
    print(f"F4: reject if fewer than half ({total_neighbors//2 + 1}) show same result")
    print(f"F4 verdict: {'PASS (plateau)' if plateau_fraction >= 0.5 else 'FAIL (spike)'}")

    # Heatmap PNG for the grid
    fig4, axes = plt.subplots(1, len(h_vals), figsize=(7 * len(h_vals), 5))
    if len(h_vals) == 1:
        axes = [axes]
    for ax_idx, h_val in enumerate(h_vals):
        ax = axes[ax_idx]
        h_data = grid_df[grid_df["h"] == h_val]
        pivot = h_data.pivot(index="k1", columns="k2", values="net_sharpe")
        im = ax.imshow(pivot.values, aspect="auto", origin="lower",
                        extent=[min(k2_vals)-0.25, max(k2_vals)+0.25,
                                min(k1_vals)-0.25, max(k1_vals)+0.25])
        ax.set_title(f"Net Sharpe (h={h_val})")
        ax.set_xlabel("k2")
        ax.set_ylabel("k1")
        # Annotate cells
        for i, k1 in enumerate(pivot.index):
            for j, k2 in enumerate(pivot.columns):
                val = pivot.iloc[i, j]
                if pd.notna(val):
                    ax.text(k2, k1, f"{val:.3f}", ha="center", va="center",
                            fontsize=8, color="white" if val < pivot.values.mean() else "black")
        plt.colorbar(im, ax=ax)

    plt.suptitle(f"Parameter Sensitivity Grid -- Net Sharpe\nBaseline Sharpe = {b_sharpe:.4f}", fontsize=12)
    plt.tight_layout()
    heatmap_path = ARTIFACT_DIR / "stops_grid_heatmap.png"
    fig4.savefig(heatmap_path, dpi=150, bbox_inches="tight")
    plt.close(fig4)
    logger.info("Grid heatmap saved to %s", heatmap_path)

    logger.info("=== T4 + T5 complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
