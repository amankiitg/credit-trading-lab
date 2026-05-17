# Sprint v5 — Notes

## W1 — Cost model

`execution/costs.py::trade_cost(notional, holding_days)` — pure,
pre-registered constants (half_spread 1.5bp, slippage 0.5bp, borrow
0.40%/yr). Hand-verified: $1M / 20 days = $1017.46
(600 spread + 100 slippage + 317.46 borrow). 8/8 tests.

## W2 — Position state machine

`execution/position.py::run_state_machine(z, entry, exit_t, stop,
regime_gate)` → {-1,0,+1} series. Transitions per PRD; one position
at a time; `regime_gate` blocks entries only (exits/stops always
fire). 12/12 tests.

Design note: no post-stop cooldown. If z is still beyond `entry` the
bar after a stop, the machine re-enters — matches the PRD spec
(flat→short whenever z>entry). Documented with an explicit test
(`test_reentry_after_stop_when_signal_still_extreme`).

## W3 — Backtest engine (C25)

`backtest/engine.py::run(residual, positions, hedge_ratio, notional,
fill_lag)` → `BacktestResult(trades, daily_pnl, equity)`.

- P&L: `gross = side·(rv_exit − rv_entry)·notional`; daily
  mark-to-market over the holding window so `daily_pnl` is
  Sharpe-ready; spread+slippage lumped on the entry-fill bar,
  borrow accrued per held day. `sum(daily_pnl) == sum(net_pnl)`.
- Fills lag `fill_lag` (≥1) trading days; `fill_lag=0` raises.
- Position open at series end is closed at the last bar
  (`closed_at_end=True`), never silently dropped.

**C25 covered:** synthetic short trade matches hand calc to <1e-6
(gross 80,000; cost 763.49; net 79,236.51); leakage test perturbs a
late bar and asserts every trade that exited earlier is byte-
identical. 7/7 tests.

## W4 — Metrics

`backtest/metrics.py` — annualised Sharpe & Sortino (√252), hit
rate, turnover, signed max drawdown (≤0, on the equity curve), avg
holding days, total net P&L, trade count. Zero-variance / no-trade
inputs return 0.0, never NaN/inf. Sortino denominator is downside
deviation (RMS of below-target obs), verified distinct from Sharpe
on an asymmetric series. 11/11 tests.

## Foundation status

W1–W4: **38/38 tests green.** Engine + costs + state machine +
metrics ready. W5 (A/B + bootstrap) is next.
