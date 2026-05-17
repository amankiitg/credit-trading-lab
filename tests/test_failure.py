"""W8 — failure analysis (C31)."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.failure import (
    FAILURE_COLUMNS,
    crisis_flag,
    enrich_trades,
    max_pnl_share,
    worst_n,
)


def test_crisis_flag_marks_known_windows() -> None:
    idx = pd.to_datetime(
        ["2007-06-01", "2008-10-15", "2020-03-20", "2021-07-01", "2022-06-01"]
    )
    flag = crisis_flag(pd.DatetimeIndex(idx))
    assert flag.tolist() == [False, True, True, False, True]


def test_max_pnl_share_concentration() -> None:
    # one trade is 50 / (50+30+20) = 50% of total
    trades = pd.DataFrame({"net_pnl": [50.0, 30.0, 20.0]})
    assert max_pnl_share(trades) == pytest.approx(0.5)


def test_max_pnl_share_even_book_is_low() -> None:
    trades = pd.DataFrame({"net_pnl": [25.0, 25.0, 25.0, 25.0]})
    assert max_pnl_share(trades) == pytest.approx(0.25)


def test_max_pnl_share_empty_is_zero() -> None:
    assert max_pnl_share(pd.DataFrame({"net_pnl": []})) == 0.0


def _toy_trades() -> pd.DataFrame:
    idx = pd.to_datetime(["2020-01-06", "2020-02-03"])
    return pd.DataFrame({
        "entry_signal_date": [idx[0]],
        "exit_signal_date": [idx[1]],
        "entry_fill_date": [idx[0]],
        "exit_fill_date": [idx[1]],
        "side": [-1],
        "rv_entry": [0.05], "rv_exit": [0.0],
        "hedge_ratio_entry": [0.5], "hedge_ratio_exit": [0.55],
        "holding_days": [20],
        "gross_pnl": [50000.0], "cost": [800.0], "net_pnl": [-1000.0],
        "closed_at_end": [False],
    })


def _toy_features() -> tuple[pd.DataFrame, pd.Series]:
    idx = pd.date_range("2020-01-01", periods=40, freq="B")
    feats = pd.DataFrame({
        "vol_regime": ["high"] * 40,
        "equity_regime": ["bear"] * 40,
        "equity_credit_lag": ["equity_first"] * 40,
    }, index=idx)
    z = pd.Series([2.5] * 40, index=idx)
    return feats, z


def test_enrich_trades_schema_and_postmortem() -> None:
    feats, z = _toy_features()
    enriched = enrich_trades(_toy_trades(), feats, z, "A_no_filter")
    assert list(enriched.columns) == list(FAILURE_COLUMNS)
    assert enriched["post_mortem"].iloc[0]               # non-empty
    assert enriched["net_pnl_bps"].iloc[0] == pytest.approx(-10.0)  # -1000 / 1e6 * 1e4
    assert enriched["strategy"].iloc[0] == "A_no_filter"


def test_worst_n_picks_lowest_pnl() -> None:
    feats, z = _toy_features()
    base = _toy_trades()
    rows = []
    for pnl in [-5000.0, 100.0, -200.0, -9000.0, 50.0, -1.0]:
        r = base.copy()
        r["net_pnl"] = pnl
        rows.append(r)
    trades = pd.concat(rows, ignore_index=True)
    enriched = enrich_trades(trades, feats, z, "A_no_filter")
    worst = worst_n(enriched, 3)
    assert worst["net_pnl"].tolist() == [-9000.0, -5000.0, -200.0]
