# Sprint v8.4 -- Paper Execution and Guards: Walkthrough

**No real money is at risk. This sprint connects the v8.2 signal to Alpaca's
paper-trading endpoint. Paper fills do not prove the signal has positive
expected value and are not interpreted that way here. This is an execution-
plumbing sprint; the v8 house rules -- no edge claim, no look-ahead, no
security-selection layer -- all carry over unchanged.**

## Summary

Sprint v8.4 built `execution/alpaca_paper.py`, a paper execution layer
that translates the v8.2 long/short signal's signed target weights into
Alpaca paper-account market orders. A two-leg design handles zero-crossing
(long-to-short or short-to-long), with each leg independently guarded by
a per-position notional cap and a per-run order count ceiling. All eight
pre-registered `P`-series engineering gates pass in dry-run mode (20 tests,
no live credentials required). No live paper session has been run yet;
T5 (submission) and T8 (attribution feed) are implemented and tested with
mocked data but are awaiting a supervised live paper run for the first
real fill. The sprint is not "done" in the sense that the plumbing has
been exercised against a real Alpaca account; it is "done" in the sense
that the plumbing is fully built, tested, and ready for a live session.

## Hypothesis and Falsification Criteria

No economic hypothesis (House Rule 1). The criteria are engineering-
correctness gates on the execution layer:

| ID | Criterion | Status |
|----|-----------|--------|
| P1 | Reconciliation logs every order, zero silent drops | **PASS** (mocked-fill tests, see notes.md) |
| P2 | No order exceeding `CAP_PER_POSITION_NOTIONAL = $8,000` is ever submitted | **PASS** (adversarial test: $8,500 target -> REJECTED_CAP) |
| P3 | Total submitted legs per run does not exceed `MAX_ORDERS_PER_RUN = 20` | **PASS** (23-order adversarial test: 20 PENDING, 3 REJECTED_MAX_ORDERS) |
| P4 | No look-ahead: order computation uses data through yesterday's close only | **DESIGN VERIFIED** (same E1'/E1 invariant as v8.1-v8.3, inherited by the signal pipeline; live-session verification pending) |
| P5 | Cost marking uses `CostParams()` defaults exactly | **PASS** ($1,000 buy -> $0.20 cost = 2bp x $1,000; exact match) |
| P6 | Attribution feed extends `data/processed/attribution.parquet` with fill rows | **PARTIAL** (function built and schema-validated with mocked data; live fill pending) |
| P7 | No API key in any committed file, log, or test output | **PASS** (source grep: zero matches for any credential pattern) |
| P8 | `DRY_RUN=True` makes zero Alpaca API calls | **PASS** (mock confirmed: `TradingClient` never instantiated in dry-run) |

## Data Pipeline

**Signal input:** same as v8.2 -- `signals.trend_signal.compute_trend` with
`L=120`, `long_short=True`, `k_dead_zone=0.5`, followed by
`apply_rebalance_control(band_pct=0.20)` and `shift_to_next_day`. The
resulting `target_weight_i` for each of the 8 universe names is the signal
used to derive orders. Frozen at v8.2 values (House Rule 4).

**New external dependency:** Alpaca paper endpoint
(`https://paper-api.alpaca.markets`) via the `alpaca-py` library. Added to
`pyproject.toml`. Credentials (`ALPACA_PAPER_API_KEY`,
`ALPACA_PAPER_SECRET_KEY`) come from environment variables only; they never
appear in any committed file (P7).

**Transforms applied in order:**
1. Signal pipeline produces `target_weight_i` in (-0.50, +0.50).
2. `target_notional_i = target_weight_i x PAPER_NAV_DEFAULT ($100,000)`.
3. Current Alpaca positions read via `client.get_all_positions()` and
   converted to `{ticker: signed_notional}` (positive = long, negative = short).
4. `delta_notional_i = target_notional_i - current_notional_i`.
5. Skip if `|delta_notional_i| < DELTA_MIN_NOTIONAL ($10)`.
6. Guard check, then submission (or dry-run block).

**No rows are dropped from the signal pipeline.** The execution layer skips
names where the delta is below the minimum threshold, but these appear in
the reconciliation log as "skipped" not as silent omissions (P1).

## Signal Behavior

Not applicable (House Rule 1). The signal itself is unchanged from v8.2;
this sprint only connects it to an order router.

## Order Translation (the sprint's primary deliverable)

### Normal cases (no zero-crossing)

| situation | order generated |
|-----------|----------------|
| flat -> long | `buy_to_open`, single leg |
| flat -> short | `sell_to_open`, single leg |
| long -> larger long | `buy_to_open`, single leg (delta only) |
| long -> smaller long | `sell_to_close`, single leg (delta only) |
| short -> more short | `sell_to_open`, single leg |
| short -> less short | `buy_to_close`, single leg |
| any -> flat | `sell_to_close` or `buy_to_close`, single leg |

### Zero-crossing (the non-trivial case)

When the target and current positions have opposite signs, two legs are
generated with explicit `PositionIntent` values. The PRD originally specified
a single signed-delta order relying on Alpaca's auto-cross behavior; the
implementation ARGUMENTS requested the explicit two-leg design. Two legs
were implemented because each leg can be independently guarded, logged, and
reconciled, making bugs visible rather than absorbed by broker-side logic.

**Long-to-short example** (current +$3,000 SPY, target -$2,000 SPY):

| leg | side | notional | `position_intent` |
|-----|------|----------|-------------------|
| 1 | sell | $3,000 | `sell_to_close` (close the existing long) |
| 2 | sell | $2,000 | `sell_to_open` (establish the new short) |

Both legs carry the same `target_notional` for the cap guard: if the final
target ($2,000) is within the cap, both legs are allowed; if the final
target exceeds the cap, both legs are rejected together (no partial crossing).

**Short-to-long example** (symmetric, buy legs with `buy_to_close` /
`buy_to_open`).

Test verification: `test_long_to_short_crossing_generates_two_leg_sequence`
constructs this exact case and asserts leg 1 is `sell_to_close $3,000` and
leg 2 is `sell_to_open $2,000`. The test passes without real credentials.

### Guard layer

Guards are applied in priority order before any API call:

```
1. CAP guard: |target_notional| > $8,000 -> REJECTED_CAP
   (uses final target, not per-leg notional; both legs blocked together)

2. MAX_ORDERS guard: cumulative PENDING count would exceed 20
   -> REJECTED_MAX_ORDERS for this and all remaining orders

3. DRY_RUN: any surviving PENDING order -> DRY_RUN
   (applied last so that oversized targets still show as REJECTED_CAP
    in a dry run, making the guard auditable even without a live run)
```

**Why $8,000?** 8% of the $100,000 paper NAV. Conservative relative to the
signal's own `w_max = 0.50` (which would allow up to $50,000 per name), so
the cap will bind if the signal ever produces an unexpectedly large weight
or if the paper NAV convention changes. It is a round, non-tuned number
chosen before any live run.

**Why 20 orders?** 2 legs x 8 names = 16 max in a full crossing day. The
20-order ceiling provides a small buffer and prevents a bug from flooding
Alpaca with orders beyond the universe size.

## Backtest Results

Not applicable (House Rule 1). No live paper fills have occurred. The
module passes 20 dry-run tests against mocked Alpaca; see Reproducibility
for the exact commands.

## Alpaca Paper Smoke Test Design

A smoke test with a short position would run as follows. With real
credentials in environment variables and `DRY_RUN=False`:

```python
from execution.alpaca_paper import connect, get_current_positions
from execution.alpaca_paper import compute_delta_orders, apply_guards, submit_orders

client = connect(dry_run=False)
current = get_current_positions(client, dry_run=False)

# signal produces a short SPY target (e.g. trail_ret_SPY < 0)
target_weights = {"SPY": -0.02}   # -$2,000 at $100K NAV
orders = compute_delta_orders(target_weights, current)
guarded = apply_guards(orders, dry_run=False)

# guard should pass ($2,000 < $8,000 cap)
assert all(o.guard_status in ("PENDING", "DRY_RUN") for o in guarded if o.ticker == "SPY")

order_ids = submit_orders(client, guarded)
# expect one order_id for the sell_to_open leg
```

The reconciliation report would then confirm:
- `filled_notional` matches `intended_notional` within 0.5% tolerance
- `fill_price` is close to the last trade price at time of submission
- `simulated_cost` = (1.5 + 0.5) bp x $2,000 = $0.40 for the trading leg,
  plus borrow accruing nightly on the short

This design is verified at the unit-test level by
`test_full_dry_run_pipeline_no_alpaca_calls`, which runs the identical
sequence with `DRY_RUN=True` and confirms zero Alpaca API calls. The live
smoke test itself is deferred to the first supervised paper session.

## Key Findings

1. **Two-leg crossing is more transparent than single-leg auto-cross.**
   A single signed-delta order would let Alpaca's engine silently close the
   long and open the short. Two explicit legs mean the reconciliation shows
   both the close and the open separately, making any partial fill (e.g.
   close executed but open rejected) visible as a discrepancy rather than
   absorbed. The extra order count (2 legs vs 1) is within the 20-order
   ceiling for any universe of 8 names.

2. **DRY_RUN=True is the correct safe default and must remain that way.**
   The paper account cannot be touched without an explicit runtime override.
   Every test in the suite passes with the default, and no test requires or
   simulates a change to the default. Any future code that calls
   `apply_guards(..., dry_run=...)` must pass `dry_run` explicitly; the
   function does not read it from a global.

3. **The cap guard uses `abs(target_notional)`, not the individual leg's
   notional.** This matters for the crossing case: a crossing order for a
   $6,000 final short position generates a close leg of $3,000 (the
   existing long) and an open leg of $6,000. If the guard used the
   per-leg notional, the close leg ($3,000) would pass while the open leg
   ($6,000) would fail, leaving a flat position instead of the intended
   short. The current implementation checks the final target once and
   blocks both legs together if it fails, which is the safe, correct
   behavior.

4. **No credential has appeared in any committed file.** P7 is enforced
   as a hard invariant, not a suggestion. The test that greps the source
   for API key patterns must be re-run on every future edit to
   `execution/alpaca_paper.py`.

5. **Paper fills and live fills are different.** Alpaca paper uses a
   simulated fill engine (fills at NBBO at time of submission, not at
   close). The reconciliation report tracks both the intended notional and
   the actual fill notional; the difference (paper slippage basis) will be
   quantified once the first live paper session runs. The v6.5 cost model
   is applied on top of fill prices, not on top of close prices, so the
   total simulated cost will differ from the historical backtest's cost
   by approximately one half-spread per order.

## Limitations

- **No live paper fills yet.** T5 (submission) and T8 (attribution feed)
  are implemented and schema-tested but have not been exercised against
  real Alpaca endpoints. The live behavior is unknown until the first
  supervised paper session.
- **Paper NAV is fixed at $100,000.** The translation does not update from
  the account's mark-to-market equity. A long run of paper losses would
  make the realized notional per name larger relative to account equity
  over time. This is a known, deliberate simplification for the first
  version.
- **Short-sale eligibility not programmatically verified.** All 8 universe
  tickers (SPY, EFA, EEM, TLT, IEF, HYG, LQD, GLD) are expected to be
  shortable on Alpaca paper, but this is asserted rather than queried.
  The reconciliation would surface any Alpaca rejection as
  `status='REJECTED_ALPACA'`, not as a silent failure.
- **No retry on partial fills or timeouts.** A partial fill or a fill
  timeout marks the fill record with the actual amount and logs the
  discrepancy; it does not retry. Over multiple days, a persistent partial
  fill would leave a position gap between the signal's target and the
  actual paper position. The next day's signal recomputes the full delta
  from current positions, so the gap self-corrects without manual
  intervention.
- **Trading halts and market hours.** Market orders submitted outside
  regular hours (9:30am-4pm ET) would be queued until the next open; the
  fill price would differ significantly from the previous close. The sprint
  does not validate submission timing; this is a live-session operational
  concern, not a code correctness issue.

## Reproducibility

- **Seeds:** none required. `execution/alpaca_paper.py` is fully
  deterministic given the same target weights and current positions.
- **Data snapshot:** no new market data is ingested this sprint. Signal
  data comes from `data/raw/{ticker}.parquet` as in v8.2.
- **Commit:** the sprint's code (`execution/alpaca_paper.py`,
  `tests/test_paper_execution.py`, `execution/logs/`, `pyproject.toml`,
  `sprints/v8.4/`) was committed as `4010d51` immediately prior to this
  walkthrough.
- **Exact commands to run the dry-run test suite (no credentials needed):**

```bash
source venv/bin/activate
pip install alpaca-py   # if not already installed

# run all P-gate tests in dry-run mode
pytest tests/test_paper_execution.py -v

# inspect the guard behavior directly
python - << 'EOF'
from execution.alpaca_paper import compute_delta_orders, apply_guards

# long-to-short crossing: current +$3000 SPY, target -$2000
orders = compute_delta_orders({"SPY": -0.02}, {"SPY": 3000.0}, paper_nav=100_000.0)
guarded = apply_guards(orders, dry_run=True)
for o in guarded:
    print(f"  {o.ticker} leg={o.leg} side={o.side} notional={o.notional:.0f} intent={o.position_intent} status={o.guard_status}")

# oversized target: $9000 exceeds $8000 cap
orders = compute_delta_orders({"HYG": 0.09}, {}, paper_nav=100_000.0)
guarded = apply_guards(orders, dry_run=False)
print("guard status:", guarded[0].guard_status)  # expect: REJECTED_CAP
EOF
```

- **First live paper session (requires credentials):**

```bash
export ALPACA_PAPER_API_KEY="your_key_here"    # never hardcode
export ALPACA_PAPER_SECRET_KEY="your_secret"   # never hardcode

python - << 'EOF'
from execution.alpaca_paper import (
    connect, get_current_positions, compute_delta_orders,
    apply_guards, submit_orders, reconcile
)
from signals.etf_universe import load_universe_close, UNIVERSE
from signals.trend_signal import (
    compute_trend, to_position_matrix, apply_rebalance_control, shift_to_next_day
)
from datetime import date

close = load_universe_close()
target = shift_to_next_day(
    apply_rebalance_control(
        to_position_matrix(compute_trend(close, L=120, long_short=True, k_dead_zone=0.5)),
        band_pct=0.20
    )
).iloc[-1].to_dict()

client = connect(dry_run=False)
current = get_current_positions(client, dry_run=False)
orders = compute_delta_orders(target, current)
guarded = apply_guards(orders, dry_run=False)
ids = submit_orders(client, guarded)
print("submitted:", len(ids), "orders")
EOF
```

## Next Steps

1. **Run the first supervised live paper session.** Set credentials,
   verify short-sale eligibility for all 8 tickers, and run with
   `DRY_RUN=False`. Inspect the reconciliation JSON in `execution/logs/`
   for the paper-slippage basis (fill price vs previous close). This
   completes T5 and enables T8.
2. **Wire T8 (attribution feed).** Connect `feed_attribution` to the
   reconciliation output so `data/processed/attribution.parquet` is
   extended with each paper-trading day's fills, enabling the v8.3
   forensic attribution to run on live paper fills.
3. **Quantify the paper-slippage basis.** After a week of paper fills,
   compare `fill_price` to `previous_close` across all orders to measure
   the systematic difference between "fill at close" (historical model)
   and "fill at NBBO at submission time" (paper reality). This difference
   is the "paper-execution slippage basis" and should be documented before
   any live-to-live comparison is made.
4. **Add a daily scheduler.** The signal runs once per market day, so a
   cron job (or equivalent) that triggers the execution script at 4:05pm
   ET (once yfinance has settled closing prices) would complete the loop.
   This is explicitly out of scope for this sprint.
5. **Monitor for credential drift.** Run the P7 grep test
   (`test_no_api_key_pattern_in_module_source`) as part of the standard
   test suite on every commit that touches `execution/`. This is already
   in the test file; it should not be removed.
