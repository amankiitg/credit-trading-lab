"""Failure analysis — worst trades, post-mortems, C31 concentration check."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NOTIONAL: float = 1_000_000.0

# Known credit-stress windows — the 4th "regime" label per PRD §W8.
CRISIS_WINDOWS: tuple[tuple[str, str], ...] = (
    ("2008-09-01", "2009-06-30"),   # GFC
    ("2011-08-01", "2011-12-31"),   # euro crisis / US downgrade
    ("2020-02-15", "2020-05-31"),   # COVID crash
    ("2022-01-01", "2022-10-31"),   # rate shock
)

FAILURE_COLUMNS: tuple[str, ...] = (
    "strategy", "entry_fill_date", "exit_fill_date", "side",
    "net_pnl", "net_pnl_bps", "holding_days",
    "z_entry", "z_exit",
    "vol_regime", "equity_regime", "equity_credit_lag", "crisis",
    "hedge_ratio_entry", "hedge_ratio_exit",
    "exit_reason", "post_mortem",
)


def crisis_flag(index: pd.DatetimeIndex) -> pd.Series:
    """Boolean Series — True on dates inside a known crisis window."""
    flag = pd.Series(False, index=index)
    for start, end in CRISIS_WINDOWS:
        flag |= (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    return flag


def _exit_reason(z_exit: float, exit_t: float, stop: float, closed_at_end: bool) -> str:
    if closed_at_end:
        return "open_at_end"
    if pd.isna(z_exit):
        return "unknown"
    if abs(z_exit) > stop:
        return "stop"
    if abs(z_exit) < exit_t:
        return "take_profit"
    return "ambiguous"


def _post_mortem(row: pd.Series) -> str:
    """One-sentence explanation of why a (losing) trade went wrong."""
    z0, z1 = row["z_entry"], row["z_exit"]
    reason = row["exit_reason"]
    if reason == "stop":
        return (f"Stopped out — residual diverged from z={z0:.1f} to z={z1:.1f} "
                f"instead of reverting.")
    if reason == "open_at_end":
        return (f"Still open at sample end (z={z0:.1f}→{z1:.1f}); marked to the "
                f"last bar before reverting.")
    if bool(row["crisis"]):
        return (f"Entered in a crisis regime ({row['vol_regime']} vol); the "
                f"dislocation widened before any mean-reversion.")
    hr0, hr1 = row["hedge_ratio_entry"], row["hedge_ratio_exit"]
    if pd.notna(hr0) and pd.notna(hr1) and abs(hr1 - hr0) > 0.15 * max(abs(hr0), 1e-9):
        return (f"Hedge ratio drifted {hr0:.2f}→{hr1:.2f} during the hold; the "
                f"hedge slipped and the residual was no longer clean.")
    return (f"Residual moved against the position (z={z0:.1f}→{z1:.1f}); "
            f"mean-reversion did not materialise within {int(row['holding_days'])} days.")


def enrich_trades(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    z: pd.Series,
    strategy_name: str,
    exit_t: float = 0.5,
    stop: float = 4.0,
) -> pd.DataFrame:
    """Attach z-scores, regime labels, exit reason and post-mortem to a ledger."""
    if trades.empty:
        return pd.DataFrame(columns=list(FAILURE_COLUMNS))
    t = trades.copy()
    crisis = crisis_flag(features.index)

    def at(series: pd.Series, dates: pd.Series) -> pd.Series:
        return series.reindex(dates).to_numpy()

    t["strategy"] = strategy_name
    t["net_pnl_bps"] = t["net_pnl"] / NOTIONAL * 1e4
    # z and regimes are read at the *signal* dates — the bars on which
    # the entry/exit decision was made (|z| crossed a threshold there).
    # The fill happens fill_lag days later at whatever z is then.
    t["z_entry"] = at(z, t["entry_signal_date"])
    t["z_exit"] = at(z, t["exit_signal_date"])
    t["vol_regime"] = at(features["vol_regime"].astype("object"), t["entry_signal_date"])
    t["equity_regime"] = at(features["equity_regime"].astype("object"), t["entry_signal_date"])
    t["equity_credit_lag"] = at(
        features["equity_credit_lag"].astype("object"), t["entry_signal_date"]
    )
    t["crisis"] = at(crisis, t["entry_signal_date"])
    t["exit_reason"] = [
        _exit_reason(zx, exit_t, stop, bool(ce))
        for zx, ce in zip(t["z_exit"], t["closed_at_end"])
    ]
    t["post_mortem"] = t.apply(_post_mortem, axis=1)
    return t[list(FAILURE_COLUMNS)]


def worst_n(enriched: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The n trades with the lowest net P&L."""
    return enriched.nsmallest(n, "net_pnl").reset_index(drop=True)


def max_pnl_share(trades: pd.DataFrame) -> float:
    """Largest single-trade |net_pnl| as a fraction of the strategy's
    total net P&L. C31 fails if this exceeds 0.25."""
    total = trades["net_pnl"].sum()
    if total == 0 or trades.empty:
        return 0.0
    return float((trades["net_pnl"].abs() / abs(total)).max())


def build_failure_analysis(
    trades_a: pd.DataFrame,
    trades_b: pd.DataFrame,
    features: pd.DataFrame,
    z: pd.Series,
    out_path: str = "data/results/failure_analysis.parquet",
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Worst-5 per strategy → parquet; return the table and C31 shares."""
    ea = enrich_trades(trades_a, features, z, "A_no_filter")
    eb = enrich_trades(trades_b, features, z, "B_equity_first")
    worst = pd.concat([worst_n(ea, 5), worst_n(eb, 5)], ignore_index=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    worst.to_parquet(out_path, index=False)
    c31 = {
        "A_no_filter": max_pnl_share(trades_a),
        "B_equity_first": max_pnl_share(trades_b),
    }
    return worst, c31
