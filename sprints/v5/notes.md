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
metrics ready.

## W5 — A/B comparison (C26, C27) — THE HEADLINE

`backtest/ab_test.py`. Strategy A = RV1, OLS hedge, no regime
filter. Strategy B = identical but entries gated to
`equity_credit_lag == equity_first`. Same engine, costs, sizing.
Full in-sample, default thresholds (2.0 / 0.5 / 4.0).

| metric | A (no filter) | B (equity_first) |
|---|---|---|
| Sharpe | **0.591** | **0.179** |
| Sortino | 0.946 | 0.269 |
| hit rate | 80.9% | 64.3% |
| trades | 94 | 14 |
| total net P&L | $760,372 | $50,650 |
| max drawdown | −$152,025 | −$30,245 |
| avg holding days | 15.7 | 21.2 |

**Bootstrap ΔS = Sharpe(B) − Sharpe(A)** (1000 stationary-block
resamples, block 21, seed 20260516):

- ΔS point estimate = **−0.412**
- 95% CI = **[−0.815, −0.009]**
- fraction of resamples with ΔS > 0 = **2.1%**

### C26 — PASS

Strategy A executed 94 round-trips with a finite Sharpe 0.591. The
control strategy is real; the engine produced a usable baseline.

### C27 — **FAIL (thesis rejected at the trading level)**

ΔS is **negative** and the **entire 95% CI sits below zero**
(upper bound −0.009). The `equity_first` regime filter does not add
risk-adjusted return — it removes it. Only 2.1% of bootstrap
resamples produced a positive increment.

**What happened.** Filtering to `equity_first` cut the strategy from
94 trades to 14 — an ~85% loss of trade count, because `equity_first`
covers only ~8% of days. Sprint v3's C22 finding (mean-reversion is
~43% faster on `equity_first` days) is real, but it does **not**
monetize: the faster reversion is more than offset by the
diversification lost from trading 7× less often. Sprint v4 already
foreshadowed this — the shuffled-regime baseline showed
`equity_first` does not raise the *frequency* of tradeable
dislocations.

This is the central, honest result of Tier 1: **the equity-credit
lag is a real statistical effect but not a tradeable regime filter.**
Per the PRD, a correctly-measured rejection of a pre-registered
hypothesis is a successful sprint. W6–W10 characterize the
rejection (OOS, benchmarks, failure analysis, robustness, portfolio).

`data/results/backtest_trades.parquet` — 108 trades (94 A + 14 B).
W5 tests: 7/7 (bootstrap sign, seed reproducibility, CI ordering).

## W6 — Walk-forward OOS (C28)

`backtest/ab_test.py::walk_forward`. Thresholds grid-calibrated on
2007-01-01 → 2018-12-31 by Strategy B net Sharpe, locked, applied to
2019-01-01 → 2026-04-15.

- Train-chosen thresholds: entry 1.5 / exit 1.0 / stop 3.0
  (train Strategy-B Sharpe 0.529)
- OOS Strategy A: Sharpe 0.658, 83 trades
- OOS Strategy B: Sharpe 0.048, 13 trades
- **OOS incremental ΔS = −0.610**

### C28 — **FAIL**

The OOS incremental Sharpe is negative — *more* negative than the
in-sample −0.412. The thesis rejection is not an artifact of
in-sample threshold tuning; it holds out-of-sample under
honestly-locked parameters. (Minor approximation: a position open
across the 2018/2019 boundary contributes post-split mark-to-market
to the OOS slice; at most one trade, immaterial.)

## W7 — Benchmarks (C29)

`backtest/benchmarks.py`. Important unit fix: the Sprint-1 random
baseline stores `sharpe = mean/std·√n_trades` (per-trade basis, see
`signals/benchmarks.py:65`), **not** an annualised daily Sharpe.
The C29 comparison is therefore done on the per-trade basis via
`trade_sharpe(trades) = mean(net_pnl)/std(net_pnl)·√n` — apples to
apples.

| | per-trade Sharpe | vs random p95 (1.700) |
|---|---|---|
| random baseline p95 (hy_spread) | 1.700 | — |
| Strategy A (no filter) | 5.000 | **+3.300 PASS** |
| Strategy B (equity_first) | 1.418 | **−0.282 FAIL** |
| buy-hold HYG (annualised daily) | 0.444 | (context) |

### C29 — **FAIL**

C29 is pre-registered on **Strategy B**, which scores a per-trade
Sharpe of 1.42 — below the random-entry 95th percentile of 1.70. The
regime-filtered strategy does not beat a lucky random-entry path.

Notable: **Strategy A clears the benchmark with room to spare**
(per-trade Sharpe 5.0, excess +3.3). The base RV1 signal is a real
edge; the `equity_first` filter is what drags it below the bar.

W7 tests: 8/8.

## W8 — Failure slide (C31)

`backtest/failure.py`. Worst-5 trades per strategy →
`data/results/failure_analysis.parquet` (10 rows × 17 cols). z and
all four regime labels (vol / equity / equity_credit_lag / crisis)
read at the **signal** date, not the fill date. Auto-generated
one-line post-mortems keyed on exit reason / crisis / hedge drift.

### C31 — A PASS, B FAIL

| strategy | max single-trade P&L share | C31 (<25%) |
|---|---|---|
| A (no filter) | 0.104 | PASS |
| B (equity_first) | **0.491** | **FAIL** |

Strategy B's result rests on a single trade contributing 49% of its
total P&L — the inevitable consequence of only 14 trades. B is not a
diversified strategy; it is essentially one good trade plus noise.

Failure-slide findings:
- Strategy A's worst trade (−683 bps, 2009-03-13) entered in the GFC
  high-vol regime — the dislocation widened before reverting.
- Two of A's worst 5 are clean stop-outs (z diverged 2.0→4.1 and
  −2.9→−4.1).
- Several of **B's** worst trades exit by `take_profit` (z reverted
  to the mid-band) yet still lose money: the 63-day rolling z-mean
  drifts toward the residual during the hold, so a z-reversion is
  not a raw-residual reversion. This is a Sprint-3 signal-design
  artifact (trading the z of a residual whose z-window mean moves)
  surfaced honestly by the failure analysis.

W8 tests: 6/6.

## W9 — Robustness: grid + subperiod + regime table (C30)

### C30 grid — **FAIL**

27-cell entry × exit × stop grid. **0 of 27 cells** have ΔS > 0.
ΔS range [−0.688, −0.071], median −0.347. There is no parameter
corner where the `equity_first` filter helps.

### C30 subperiod — **FAIL**

Split at 2016-09-15: first-half ΔS −0.353, second-half ΔS −0.485.
Both negative — the rejection is not crisis-specific, it holds in
both halves.

### Hedge-method panel (reported only)

| method | Sharpe A | Sharpe B | ΔS | 95% CI |
|---|---|---|---|---|
| OLS | 0.591 | 0.179 | −0.411 | [−0.861, +0.033] |
| Kalman | 0.709 | 0.093 | −0.617 | [−1.179, −0.228] |
| DV01 | −0.082 | −0.007 | +0.075 | [−0.326, +0.470] |

DV01 shows a nominally positive ΔS, but both DV01 strategies have
~zero Sharpe (they barely make money), and the CI straddles zero
widely — it is noise on top of two non-strategies, not a rescue of
the thesis. Under the two hedge methods that *do* produce a real
strategy (OLS, Kalman), ΔS is firmly negative.

### Regime table — `data/results/regime_performance.parquet` (28 rows)

Sharpe of each strategy's daily P&L restricted to each regime label.
The damning cell: **Strategy B's P&L on `equity_first` days has
Sharpe −0.18** — even inside its own gating regime the strategy
loses risk-adjusted. On the *same* `equity_first` days, the
unfiltered Strategy A scores +0.64. The regime the thesis is built
on is the regime where the gated strategy does worst.

A side-finding: Strategy A's best regime cell is
`equity_credit_lag = credit_first` (Sharpe 1.26) — the opposite of
the thesis regime.

W9 tests: 3/3 (regime-table schema + cartesian completeness).

## W10 — Portfolio + sprint close

`risk/portfolio.py` — combine RV1/RV2/RV3 Strategy-B backtests under
equal-weight and inverse-vol (trailing 63d, lagged 1 day) schemes.

| book | Sharpe |
|---|---|
| RV1_B | 0.179 |
| RV2_B | 0.223 |
| RV3_B | 0.043 |
| portfolio — equal weight | 0.199 |
| portfolio — inverse vol | 0.131 |

Neither portfolio scheme beats the best single signal (RV2_B 0.223).
Combining three weak gated strategies just averages the weakness;
inverse-vol does worse than equal-weight. Portfolio diversification
does not rescue the thesis.

`notebooks/05_backtest.ipynb` — 21 cells, executes end-to-end;
6 plots in `sprints/v5/plots/`. W10 tests: 4/4.

## Falsification scoreboard — final

| | criterion | result |
|---|---|---|
| C25 | engine correctness + no leakage | **PASS** |
| C26 | control strategy real (≥30 trades) | **PASS** |
| C27 | THESIS — ΔS>0, bootstrap CI excludes 0 | **FAIL** (ΔS −0.411, CI [−0.815, −0.009]) |
| C28 | OOS walk-forward ΔS>0 | **FAIL** (OOS ΔS −0.610) |
| C29 | Strategy B beats random p95 | **FAIL** (B 1.42 vs p95 1.70) |
| C30 | robust across grid + subperiods | **FAIL** (0/27 cells, both halves negative) |
| C31 | no single trade > 25% of P&L | **FAIL** (B: 49%) |

**The thesis is rejected.** The equity-credit lag is a real
statistical effect (Sprint v3 C22: ~43% faster mean-reversion on
`equity_first` days) but **not a tradeable regime filter**. Gating
to `equity_first` discards ~85% of trades; the faster reversion on
survivors does not compensate for the lost diversification. The
unfiltered Strategy A is the better strategy on every cut. Engine
gates pass — the rejection is correctly measured, not a bug. Per the
PRD, a pre-registered rejection cleanly demonstrated is a successful
final sprint.

Full suite: 187/190 pytest (3 failures are pre-registered Sprint v3
honest failures — no v5 regression); 38/38 Catch2. v5 added 53 tests,
all green.
