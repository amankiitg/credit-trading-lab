# Sprint v8.4 -- Paper Execution and Guards (Programme v8, Sprint 4)

## Context: The Transition to Paper Trading

v8.1 built the signal. v8.2 brought turnover under control. v8.3 explained
where the realized P&L came from. v8.4 connects the signal to an actual
order router -- Alpaca's paper-trading endpoint -- so the live signal can
run daily and produce real orders, fills, and a fill-vs-intention
reconciliation, all against a paper account with zero financial risk.

This is the first sprint in this programme that interacts with an external
service at runtime. The character of the engineering changes accordingly:
correctness gates now include credential security, network error handling,
guard enforcement under adversarial conditions (e.g. a bug that generates
an enormous target weight), and fill-vs-intention accounting that must
match to within a known tolerance. None of this constitutes a claim that
the signal predicts anything.

## v8 House Rules (carried, with one addition)

1. **No edge claim.** Paper trading is not evidence the signal will make
   money with real capital. It is a plumbing test.
2. **No look-ahead.** The target weights sent to Alpaca each day must be
   computed using data through the previous market close only. The
   signal's own E1' invariant (tested in v8.1-v8.3) carries over; the
   new P4 gate below re-verifies it explicitly for the execution path.
3. **Parameters pre-registered, not tuned.** Every guard threshold and
   every cost constant is a named, fixed value chosen before any paper
   run happens (see §Signal Definition). No guard threshold is adjusted
   because the first paper run "felt too conservative."
4. **Book frozen from v8.2.** `L=120`, `k_dead_zone=0.5`, `band_pct=0.20`,
   `rebal_freq=1`. Not touched here.
5. **Mechanical and reproducible.** Every execution run must produce the
   same orders given the same market close data and Alpaca position state.
6. **Single-regime caveat.** Historical paper performance, once accumulated,
   is still measured on a forward extension of the same 2007-2026 universe
   and regime assumptions. It is not a general-purpose backtest or a claim
   about future returns.
7. **Security-selection layer is still the named permanent gap.**
8. **No monthly/lower-frequency factor.** (House Rule 8, carried.)
9. **No credential in any committed file, test output, or log line.** All
   API keys live in environment variables only. Any accidental key leak
   in a commit is a hard stop: rotate the key before continuing.

---

## Economic Hypothesis

None (House Rule 1). The purpose of this sprint is operational: confirm
that the signal-to-order pipeline is correct, that the guards prevent
obviously bad orders, that fills are captured and attributed, and that
the fill-vs-intention reconciliation is reliable. Paper P&L, once it
accumulates, will be reported as-is without any interpretation as evidence
for or against the signal's edge.

---

## Falsification Criteria

New `P`-series (Paper execution gates), continuing the v8 gate convention.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|----------------|------------------|
| P1 | Fill reconciliation: every submitted order either has a matching fill record within tolerance, or the discrepancy is logged and the fill is flagged, never silently dropped | 0 unlogged discrepancies per run | bug; fix -- silent mismatches are the most dangerous failure mode here |
| P2 | Guard: no order exceeding the per-position notional cap is ever submitted to Alpaca | 0 cap-violating orders submitted in any run, including adversarial test runs where the target weight deliberately exceeds the cap | bug; fix -- guards must enforce, not suggest |
| P3 | Guard: total orders submitted per run does not exceed `MAX_ORDERS_PER_RUN` | 0 runs where `len(submitted_orders) > MAX_ORDERS_PER_RUN_DEFAULT` (20) | bug; fix |
| P4 | No look-ahead in order computation: the target weights used to generate orders at time T are computed from data through close(T-1) only | same perturbation test as E1' (v8.1/v8.3), applied to the execution path | bug; fix |
| P5 | Cost marking consistent with v6.5: the simulated cost applied to each fill uses `execution.costs.CostParams` defaults exactly (`half_spread_bp=1.5`, `slippage_bp=0.5`, `borrow_annual=0.004`) | exact constant match verified by test | wrong/loosened cost assumption |
| P6 | Attribution feed: after each paper run, the tidy frame at `data/processed/attribution.parquet` is extended with the paper-fill rows in a v8.3-compatible schema | new rows appear with correct date, ticker, pnl, carry, etc. | bug in the feed path |
| P7 | Credential security: no API key, secret key, or account ID appears in any committed source file, log file, or test output | zero occurrences on grep | rotate key immediately, fix before re-committing |
| P8 | Dry-run mode: running with `DRY_RUN=True` produces a complete reconciliation report (listing all intended orders and their guards status) but submits zero orders to Alpaca | 0 Alpaca API calls made in dry-run mode | broken dry-run safeguard |

---

## Signal Definition (the order-translation pipeline)

**Account convention.** The Alpaca paper account starts with a nominal
equity of `PAPER_NAV_DEFAULT = 100_000` USD. All target weights from the
v8.2 signal are fractions of this NAV. Pre-registered and fixed; not
updated from the account's mark-to-market equity in the first version
(a deliberate simplification: keeps the signal-to-order translation
stationary, avoids feedback loops from paper P&L back into position size).

**Pre-registered guard constants (House Rule 3):**
```
CAP_PER_POSITION_NOTIONAL = 8_000    # USD hard cap per position (8% of default NAV)
MAX_ORDERS_PER_RUN = 20              # hard ceiling on orders submitted per execution run
DRY_RUN_DEFAULT = True               # safe default: never submit until explicitly overridden
```
`CAP_PER_POSITION_NOTIONAL` is a round, conservative number (8% of a
$100K paper account) chosen to limit any single position to a reasonable
size regardless of what the signal or a future bug might produce. It is
not optimised to any backtest metric.

**Step 1 -- compute target.** Run the full v8.2 signal pipeline using data
through yesterday's close (E1' invariant applies):
```
target_weight_i = apply_rebalance_control(
    to_position_matrix(compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)),
    rebal_freq=1, band_pct=0.20
)[today_date - 1_business_day]
```
Result: a signed weight in (-w_max, +w_max) = (-0.50, +0.50) for each
of the 8 tickers (or NaN if still in warmup -- treat as 0).

**Step 2 -- compute target notional.**
```
target_notional_i = target_weight_i * PAPER_NAV_DEFAULT
```

**Step 3 -- read current Alpaca positions.** Via `TradingClient.get_all_positions()`.
Convert to a signed-notional dict `{ticker: current_notional_i}` (positive
for long, negative for short, 0 if not held). Tickers not in UNIVERSE are
ignored.

**Step 4 -- compute delta.**
```
delta_notional_i = target_notional_i - current_notional_i
```
If `|delta_notional_i| < DELTA_MIN_NOTIONAL = 10` (USD), skip the order for
that ticker (too small to be worth the bid-ask cost). Pre-registered.

**Step 5 -- guard check (before any order submission).**
For each proposed order:
- `|target_notional_i| > CAP_PER_POSITION_NOTIONAL`: reject and log.
  Do not submit. Set `status = 'REJECTED_CAP'` in the order log.
- Cumulative submitted order count would exceed `MAX_ORDERS_PER_RUN`:
  reject all remaining orders (not just the one that would tip the
  count over). Set `status = 'REJECTED_MAX_ORDERS'`. This is a hard
  ceiling, not a warning.
- `DRY_RUN = True`: reject all orders with `status = 'DRY_RUN'` without
  calling any Alpaca API endpoint.

**Step 6 -- crossing through zero.** When `current_notional_i` and
`target_notional_i` have opposite signs (long-to-short or short-to-long
flip), Alpaca paper supports a single signed fractional-notional order
that will close the existing position and open the new one automatically.
Use this direct approach: submit one order for `delta_notional_i` (which
will be > position size and cross zero). Verify the resulting Alpaca
position has the expected sign in the post-fill reconciliation (P1).

**Step 7 -- order type.** Market orders only (Alpaca paper, daily signal).
Fractional notional orders via `qty` or `notional` parameter where
supported (ETFs on Alpaca paper support fractional shares). Pre-registered.

**Step 8 -- fill capture and cost marking.** After order submission, wait
for fill confirmations (poll or webhook -- paper fills are typically
immediate). For each fill:
```
fill_notional_i    = |filled_quantity_i| * fill_price_i
simulated_cost_i   = (half_spread_bp + slippage_bp) * 1e-4 * fill_notional_i
                     + (borrow_annual / 252) * |short_notional_i_held|
paper_net_pnl_i(t) = price_change_pnl_i - simulated_cost_i
```
using `execution.costs.CostParams` defaults exactly (P5). The fill price
from Alpaca paper will differ from the signal's close-based price by up
to one half-spread -- this is a known, expected, and documented difference
between paper execution and the historical simulation.

**Step 9 -- fill-vs-intention reconciliation.**
```
fill_discrepancy_i = filled_notional_i - intended_delta_notional_i
```
Flag any `|fill_discrepancy_i| > max(10, 0.005 * |intended_delta_notional_i|)`
(pre-registered tolerance: larger of $10 or 0.5% of intended). Log ALL
discrepancies, flagged or not. Write to
`execution/logs/reconciliation_{YYYY-MM-DD}.json`. Feed filled rows to
the v8.3 attribution pipeline (P6).

---

## Data

| source | contents | frequency | status |
|--------|----------|-----------|--------|
| `data/raw/{ticker}.parquet` + yfinance ingest | adj_close for signal computation | daily (fetched at/after 4pm ET) | existing (v8.1) |
| `data/raw/{ticker}_dividends.parquet` | dividend history for carry attribution | daily (ex-div sparse) | existing (v8.3) |
| Alpaca paper account | current positions, fill confirmations, account equity | live/realtime | **new, requires credentials** |
| Environment variables | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY` | n/a | **new, must not be in any committed file** |

**Alpaca paper endpoint:**
```
BASE_URL = "https://paper-api.alpaca.markets"
```
The modern alpaca-py library: `pip install alpaca-py`.
```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
```

**Known constraints and biases:**
- Paper fills use Alpaca's simulated fill engine (typically fills at the
  NBBO bid/ask at time of submission, not exactly at close) -- a
  systematic difference from the historical backtest's "fill at close"
  assumption, documented as the "paper-execution slippage basis" in the
  reconciliation report.
- Alpaca paper does not charge borrow fees or commissions; these are
  simulated via Step 8's cost-marking formula to keep the paper-trading
  P&L consistent with the historical cost model.
- Alpaca paper's short-sale eligibility list changes over time; all 8
  universe tickers (SPY, EFA, EEM, TLT, IEF, HYG, LQD, GLD) are liquid
  US-listed ETFs and are expected to be shortable on the paper account,
  but this should be verified on first connection.
- Trading halts, circuit breakers, and order rejections by Alpaca
  (not by the guards layer) are captured in the reconciliation as
  `status = 'REJECTED_ALPACA'` and logged, not treated as programme errors.

---

## Success Metrics

No Sharpe, IC, or predictive metric (House Rule 1). Metrics are the
`P1-P8` gates and these informational reports (not gated):
- Number of orders submitted vs. intended per run (expected: 0-8 on a
  normal rebalancing day, 0 if all positions are already at target).
- Fill-vs-intention reconciliation report, persisted per run.
- Simulated paper cost per run in bps (expected: consistent with the
  historical 0.04 bps/day average from v8.3, within the paper-execution
  slippage basis).

---

## Research Architecture

```
close(t-1) from yfinance
      |
[T1] Alpaca connection: TradingClient, credential check (P7), account access
      |
[T2] Current positions from Alpaca -> signed-notional dict
      |
[T3] execution/paper.py::compute_orders(target_weights, current_positions)
        -- delta computation, guard checks (P2, P3), zero-crossing logic
        -- DRY_RUN short-circuit (P8)
      |
[T4] Order submission to paper-api.alpaca.markets (skipped in DRY_RUN)
      |
[T5] Fill reader: poll Alpaca for fill confirmations, build fill records
      |
[T6] Cost marking: apply v6.5 CostParams to each fill (P5)
      |
[T7] Reconciliation report: fill-vs-intention (P1), write to
     execution/logs/reconciliation_{date}.json
      |
[T8] Attribution feed: extend data/processed/attribution.parquet (P6)
      |
[T9] Full integration test (dry-run): P1-P8 suite, adversarial cap test
```

**New module:** `execution/paper.py` -- the execution, guard, and
reconciliation logic lives here. Keeps the single-file-per-concern
discipline already established in `execution/costs.py` and
`execution/position.py`.

**Reused, not rebuilt:** `execution.costs.CostParams` (v6.5 constants,
unchanged -- P5 gate confirms this), `risk.attribution.build_tidy_attribution`
(v8.3 tidy frame schema, extended with paper fill rows -- P6 gate).

---

## Risks and Biases

- **Credential leak (P7).** The highest-severity risk in this sprint.
  Any API key in a committed file, log line, or test output must trigger
  an immediate key rotation and a commit revert before any further work.
  The guard is not a warning; it is a hard stop.
- **Guard bypass.** A bug where the guard check passes but the cap is
  still violated (e.g., incorrect sign handling causing a positive
  notional to be compared to the cap without absolute value). The
  adversarial test in T9 exists specifically to exercise this.
- **Silent fill mismatch (P1).** A partial fill or an Alpaca rejection
  that is not captured in the reconciliation is more dangerous than a
  logged discrepancy, because the position state will drift from the
  intended state over time. P1's "0 unlogged discrepancies" threshold is
  deliberately strict.
- **Paper/live gap.** Paper fills are cheaper (no real borrow, no real
  market impact) and sometimes faster (no real queue) than live fills.
  A paper run that "works" does not guarantee the same behaviour in live
  execution. This is named, not corrected.
- **Non-stationarity of the paper-execution slippage basis.** Alpaca's
  paper fill engine may change its simulation methodology; a change in
  the systematic difference between paper fills and the close-based
  simulation would not be detected until the reconciliation showed a
  systematic drift. Monitor the reconciliation report for trends.

---

## Out of Scope

- Real-money execution of any kind
- Intraday rebalancing (daily close-to-close only)
- Margin, PDT rules, or real borrow-availability checks (paper account
  does not enforce these in the same way as a live account)
- Corporate actions (splits, mergers) during the holding period (the
  signal will naturally recompute a new target next day; no mid-hold
  handling is built)
- Any performance claim about the signal based on paper fills
- Changing the signal, the book parameters, or the v8.3 attribution
  engine (House Rule 4, carried)
- A web UI, scheduled cron wiring, or always-on daemon process -- the
  execution pipeline is a manually-triggered script in this sprint; a
  scheduler would be a separate, later sprint

---

## Dependencies

- `execution/costs.py` -- v6.5 constants, unchanged (P5)
- `risk/attribution.py` -- v8.3 tidy frame, extended (P6)
- `signals/trend_signal.py`, `signals/etf_universe.py` -- v8.2, unchanged
- `data/raw/{ticker}.parquet`, `data/raw/{ticker}_dividends.parquet` -- v8.3
- `alpaca-py` >= 0.8 -- **not yet installed**; must be added to
  `pyproject.toml` and the virtual environment before T1
- `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_SECRET_KEY` -- environment variables,
  never committed
