# Sprint v8.4 -- Notes

---

## Implementation record

### File name deviation from PRD (documented, not silent)

The PRD specified `execution/paper.py`. The implementation ARGUMENTS
specified `execution/alpaca_paper.py`. The implementation follows the
ARGUMENTS (more specific), consistent with the project's practice of
following the implementation-turn instruction when it deviates from the
PRD's planning text. The more explicit name avoids future ambiguity with
any generic `paper.py` Python standard library interactions.

### Two-leg crossing deviation from PRD (documented, not silent)

The PRD specified a single signed-delta order for zero-crossing, relying on
Alpaca paper's auto-cross behavior. The implementation ARGUMENTS explicitly
requested "the correct two-leg order sequence." The implementation follows
the ARGUMENTS: each crossing generates two separate OrderSpec entries with
distinct PositionIntent values:

- Long-to-short: Leg 1 = `sell_to_close` (close the long), Leg 2 =
  `sell_to_open` (open the short)
- Short-to-long: Leg 1 = `buy_to_close` (close the short), Leg 2 =
  `buy_to_open` (open the long)

Each leg is independently guarded (cap check, order-count check) and
independently logged in the reconciliation report. This is the more
explicit design; a bug in either leg is visible in the reconciliation
rather than absorbed by Alpaca's auto-cross logic.

The PRD's `MAX_ORDERS_PER_RUN = 20` limit already accounts for this: 2
legs x 8 tickers = 16 max, well within the 20-order ceiling.

---

## T1 -- alpaca-py installation and connection (P7)

`alpaca-py` installed via `pip install alpaca-py`, added to
`pyproject.toml` under `[project] dependencies`. Confirmed:

```
from alpaca.trading.client import TradingClient  # imports OK
from alpaca.trading.enums import PositionSide, OrderSide, PositionIntent  # all present
from alpaca.trading.requests import MarketOrderRequest  # notional parameter confirmed
```

`execution/alpaca_paper.py::connect(dry_run)` reads `ALPACA_PAPER_API_KEY`
and `ALPACA_PAPER_SECRET_KEY` from environment variables only. Returns None
in dry-run mode (no TradingClient constructed, no credentials needed).
Raises `EnvironmentError` with a clear message if called with
`dry_run=False` and credentials are missing.

**P7 check:** `grep -n "PKIA\|api_key\s*=\s*['\"]" execution/alpaca_paper.py`
-> zero matches. No credential appears in any committed file.

---

## T2 -- current-positions reader

`get_current_positions(client, dry_run) -> dict[str, float]`:
- dry_run=True returns `{}` without calling Alpaca (P8 invariant)
- live mode: `client.get_all_positions()` -> iterate positions in UNIVERSE
- Sign convention: `PositionSide.LONG` -> positive notional; `PositionSide.SHORT` -> negative notional
- Positions for tickers outside UNIVERSE are ignored

---

## T3-T4 -- order translation and guard layer

`compute_delta_orders` + `apply_guards`. Pre-registered constants
(House Rule 3):

| constant | value | rationale |
|----------|-------|-----------|
| `CAP_PER_POSITION_NOTIONAL` | $8,000 | 8% of $100K paper NAV; conservative cap |
| `MAX_ORDERS_PER_RUN` | 20 | 2.5x universe size; hard ceiling on API calls |
| `DELTA_MIN_NOTIONAL` | $10 | skips trivially small adjustments |
| `DRY_RUN_DEFAULT` | True | safe default; live run requires explicit override |

**Guard priority order (matters because a cap-rejected order does not
count against the MAX_ORDERS_PER_RUN budget):**
1. CAP check (uses `abs(target_notional)`, not the leg's own notional, so
   both legs of a crossing share the same guard decision)
2. MAX_ORDERS_PER_RUN count (incremented only for orders that survive cap check)
3. DRY_RUN override (last, so that a dry run of an oversized target still
   shows REJECTED_CAP rather than DRY_RUN, making the guard auditable)

---

## T5-T8 -- fill reader, cost marking, reconciliation, attribution feed

Not yet implemented in this pass (no live paper session). The
`build_fill_records`, `mark_costs`, and `reconcile` functions are
implemented and tested with mocked data; `feed_attribution` (P6) deferred
to the first live paper session.

---

## T9 -- integration test (dry-run, all P-gates)

20 tests in `tests/test_paper_execution.py`, all passing, zero real
Alpaca calls. Adversarial scenarios covered:
- Oversized target -> REJECTED_CAP, zero PENDING survivors
- Long-to-short crossing -> correct two-leg sequence (leg 1:
  sell_to_close, leg 2: sell_to_open)
- Short-to-long crossing -> symmetric two-leg sequence
- Delta < DELTA_MIN_NOTIONAL -> skipped, no OrderSpec generated
- 23 orders -> first 20 PENDING, last 3 REJECTED_MAX_ORDERS
- dry_run=True -> all 2 non-trivial orders become DRY_RUN, zero PENDING
- Cost marking: SPY buy $1000 -> cost = 2bp * 1000 = $0.20 exactly (P5)
- Borrow applied to short but not long (P5)
- Reconciliation: 2 fills (one normal, one cap-rejected) -> both appear in
  output, neither silently dropped (P1)
- Flagging threshold: $100 discrepancy on a $3000 intended fill -> flagged
  (threshold = max($10, 0.5% * $3000) = $15 < $100)
- P7: module source grepped for API key patterns -> zero matches
- P8: full dry-run pipeline -> zero TradingClient instantiations, zero
  PENDING orders

### Gate-status summary

| ID | Status | Note |
|----|--------|------|
| P1 | PASS | reconciliation captures all orders, zero silent drops |
| P2 | PASS | cap-rejected order never reaches PENDING; adversarial test confirms |
| P3 | PASS | 20th order is PENDING; 21st is REJECTED_MAX_ORDERS |
| P4 | not directly tested here | signal timestamp check deferred to live run |
| P5 | PASS | exact CostParams constant match, borrow sign correct |
| P6 | deferred | attribution feed not yet tested (no live fills yet) |
| P7 | PASS | zero credential patterns in module source |
| P8 | PASS | zero Alpaca API calls in dry-run mode |

---

## Second fail-safe guard: traded-notional brake (MAX_TRADED_NOTIONAL_PER_RUN)

### Why the position-size cap alone is insufficient

The position-size cap (`CAP_PER_POSITION_NOTIONAL = $8,000`) checks
`abs(target_notional)`: the size of the position you are trying to reach.
For a zero-crossing this is the size of the *destination* position only.

But a zero-crossing *transacts* more than the destination: to go from long
$7,000 to short $6,000, the run trades $7,000 (close leg) + $6,000 (open
leg) = $13,000, even though the destination position ($6,000) is under
the $8,000 cap. The cap passes; the transaction volume is $13,000. The cap
is measuring the right thing for position-sizing purposes and the wrong
thing for transaction-volume purposes. These are two distinct properties
that need two distinct guards.

### The new guard: MAX_TRADED_NOTIONAL_PER_RUN

**Value:** `MAX_TRADED_NOTIONAL_PER_RUN = 16_000.0` (2 x `CAP_PER_POSITION_NOTIONAL`).

**Rationale:** the largest single crossing that would pass the position-size
cap is one where the destination is just under $8,000 and the existing
position is also around $8,000, totaling roughly $16,000 traded. Setting
the brake at exactly $16,000 means that crossing just passes; any larger
combination is blocked. This is a round number chosen before any live run,
consistent with House Rule 3 (not tuned for performance).

**Guard logic:** the brake checks the *summed* notional of all legs in a
ticker group for the run. For a crossing: `abs(close_leg) + abs(open_leg)`.
For a single-leg order: `abs(leg.notional)`. If adding the group's total
to the run's accumulated total would exceed `MAX_TRADED_NOTIONAL_PER_RUN`,
the guard fires. The crossing is still evaluated as a unit (all-or-nothing):
neither leg fires on breach, so you cannot end up half-crossed under the
brake any more than under the cap.

**Distinct reason code:** `REJECTED_TRADED_NOTIONAL` -- separate from
`REJECTED_CAP` so the two guards are distinguishable in the reconciliation
log. A `REJECTED_CAP` entry means the destination position was too large.
A `REJECTED_TRADED_NOTIONAL` entry means the transaction volume in a single
run was too large. These are different operational problems with different
remedies.

### Plain-English distinction (for notes inheritance and live-session review)

- **Position-size cap** (`CAP_PER_POSITION_NOTIONAL`): limits how large a
  position you are allowed to *hold*. Checked against `abs(target_notional)`.
  This is a position-sizing limit.

- **Traded-notional brake** (`MAX_TRADED_NOTIONAL_PER_RUN`): limits how much
  you are allowed to *transact* in a single run. Checked against the summed
  notional of all legs submitted. This is a transaction-throughput limit.

- **They diverge only in crossings.** A plain open or reduce has
  `|traded| == |delta|` which is at most `|target|`, so if the destination
  passes the cap the traded amount is at most the cap value and the brake is
  not the binding constraint. A crossing has `|traded| = |close| + |open|`,
  which can be roughly 2x the destination, and the brake catches this extra
  volume that the cap ignores.

### Guard ordering update

Guards now run in this priority order (DRY_RUN last, so all content
rejection reasons stay auditable in dry-run):

1. `REJECTED_CAP`: destination position too large
2. `REJECTED_TRADED_NOTIONAL`: run's transaction volume would be too large
3. `REJECTED_MAX_ORDERS`: run's order count would be too large
4. `DRY_RUN`: order would otherwise be submitted (all content guards passed)

The refactored `apply_guards` processes legs in ticker groups rather than
individually. This fixed a pre-existing secondary bug: the original per-leg
MAX_ORDERS check could allow leg 1 of a crossing when `submitted_count`
was at the limit and block only leg 2, leaving the book half-crossed. The
group-based implementation blocks all legs of a crossing atomically under
all three content guards.

### Tests added (27 total, up from 20)

- `test_traded_notional_brake_catches_crossing_that_cap_misses`: the core
  case. Target $6,000 (under $8,000 cap), current $7,000 long, total traded
  $13,000 (over $12,000 custom brake) -> both legs REJECTED_TRADED_NOTIONAL,
  not REJECTED_CAP.
- `test_traded_notional_brake_crossing_all_or_nothing`: confirms that leg 1
  (close) is also blocked, not just leg 2 (open), when the group total
  breaches the brake.
- `test_normal_open_unaffected_by_traded_notional_brake`: a plain open
  within the brake passes through unchanged.
- `test_crossing_within_brake_passes`: a crossing whose total traded notional
  is within the brake passes both guards.
- `test_both_rejection_reasons_visible_in_dry_run`: REJECTED_CAP and
  REJECTED_TRADED_NOTIONAL both appear in dry-run output; a normal order
  that passes both becomes DRY_RUN. Guards stay auditable.
- `test_traded_notional_accumulates_across_tickers`: the brake is a
  per-run total; a second order that would push the total over the limit
  is blocked even if it would pass individually.
- `test_traded_notional_brake_uses_default_constant`: asserts
  `MAX_TRADED_NOTIONAL_PER_RUN == 2 * CAP_PER_POSITION_NOTIONAL`.

### Live-session smoke test plan (pending, do not run now)

When the zero-crossing is exercised in the first supervised live paper
session, include one crossing constructed near the traded-notional brake
boundary:

- Set current_notionals to a position that, combined with a target just
  under the cap, produces a group_traded total above $16,000.
  Example: current SPY +$9,000, target SPY -$7,500. Close leg: $9,000,
  open leg: $7,500, total: $16,500 > $16,000 brake. Target $7,500 < $8,000
  cap. This is the case the position-size cap alone would miss.
- Confirm the guard blocks BOTH legs against real Alpaca API responses
  (not just in the dry-run test). The dry-run test proves the guard fires
  in code; the live session proves it fires in the path that actually
  reaches the order router, where the partial-success risk lives.
- Log the order_id (or absence thereof) in the reconciliation JSON and
  verify both legs show REJECTED_TRADED_NOTIONAL, not REJECTED_ALPACA.

Sprint v8.4 status: **second fail-safe guard implemented and tested (27
tests pass); live session still pending for T5/T8.**

---

## T5 live paper session (2026-06-17 — market hours)

**Result: PASS.** Connection and fill round-trip confirmed.

### Orders submitted

| order_id | ticker | side | notional | fill_price | filled_qty | filled_$ | status |
|----------|--------|------|----------|------------|------------|----------|--------|
| 70a77327 | SPY | buy_to_open | $100.00 | $750.784 | 0.133181 | $100.00 | FILLED |
| 2d649792 | SPY | sell_to_close | $99.98 | $750.734 | 0.133176 | $99.98 | FILLED |

Both fills within the 30s timeout. Reconciliation: 0 flagged discrepancies.
Simulated cost on close leg: $0.020 (2bp half-spread + slippage on $100).
Residual after close: $0.003 (fractional share rounding — expected).
Log: `execution/logs/reconciliation_2026-06-17.json`.

Bug found and fixed: `build_fill_records` used `order.filled_notional` which
doesn't exist in alpaca-py. Fixed to `filled_qty * filled_avg_price`.

### Cap calibration issue (carry to v8.6)

The full-book pipeline run (all 8 tickers, $100k NAV) rejected all orders
at REJECTED_CAP. Root cause: `CAP_PER_POSITION_NOTIONAL = $8,000` but at
$100k NAV even the smallest target weight (IEF 18%) = $18,123.

For ALL positions to pass the cap at current weights:
  NAV < CAP / max_weight = 8000 / 0.36 ≈ $22,000

**v8.6 decision needed:** either lower PAPER_NAV_DEFAULT to ~$20k (all
weights pass cap), or raise the cap proportionally, or make the cap a
fraction of NAV rather than a fixed dollar amount. Document the choice in
v8.6 PRD before the first real book execution.

Sprint v8.4 status: **T5 live session DONE (2026-06-17). T8 (attribution
feed append) deferred to v8.6.**

---

## Supervised smoke gate — items 2-4 (2026-06-17 market hours)

### Item 2: Short locate — PASS (with critical finding)

1-share SPY `sell_to_open` filled (order addb8e0a, 1 share @ $751.51).
Negative position confirmed via `get_current_positions`: SPY = -$751.56.

**Critical finding: notional shorts are blocked by Alpaca paper.**
`"fractional orders cannot be sold short"` — Alpaca rejects any notional
sell_to_open with a 422 error. Shorts MUST be submitted as `qty` (whole
shares), not `notional`.

Design fix required in v8.6: `submit_orders` needs a short path that
converts `notional → floor(notional / last_price)` shares and submits
with `qty` instead of `notional`. Long orders (buy_to_open, buy_to_close)
can remain notional. Longs-reducing-short (buy_to_close on a fractional
short) should also use `qty` from the position record, not notional.

### Item 3: Zero-crossing — PASS

Long open: 1 share SPY @ $751.49 (order 4b5e9a7f).
Leg 1 sell_to_close: 1 share @ $751.41 (order 94c50a50) — filled.
Leg 2 sell_to_open:  1 share @ $751.38 (order 55a05158) — filled.
Final position: SPY = -$751.40 (short). Closed via close_position.

Both legs filled in sequence (close confirmed before open submitted).
All-or-nothing structure held: no partial crossing observed.

### Item 4: Guard boundary — PASS

Scenario: EEM $7,000 open (group_traded $7k, passes cap + brake);
SPY crossing close $7,900 + open $7,000 = group_traded $14,900;
accumulated total $21,900 > $16,000 brake; SPY target -$7,000 < cap.

apply_guards(dry_run=False) fired REJECTED_TRADED_NOTIONAL on BOTH SPY
legs. EEM reached submit_orders (order d89789f0, filled $6,999.99).
SPY never reached the Alpaca API. Guard fired in the live code path, not
just in dry-run mode.

### Smoke gate verdict: items 1-4 PASS; item 5 (T8 attribution feed) deferred to v8.6

Outstanding v8.6 design changes surfaced by this gate:
1. `submit_orders` needs a short path using `qty` (not `notional`)
2. `PAPER_NAV_DEFAULT` or `CAP_PER_POSITION_NOTIONAL` must be recalibrated
   before full-book execution (cap rejects all positions at $100k NAV)
3. `close_position` API should be preferred over notional close orders for
   dust/fractional residuals

---

## NAV-relative position-size cap refactor (2026-06-17)

### Context

The live T5 session (today) rejected all 8 book tickers at REJECTED_CAP.
Root cause: the old absolute guard was CAP_PER_POSITION_NOTIONAL = $8,000.
At PAPER_NAV_DEFAULT = $100,000, even the smallest target weight (IEF 18.12%)
produces a notional of $18,120, which exceeds the $8,000 cap by 2.3x.
A position-size limit that cannot hold any position in the intended book is
not a guard; it is a hard block. The cap must scale with the book.

### Decision: MAX_POSITION_PCT_OF_NAV = 0.40

The cap is now abs(target_notional) > MAX_POSITION_PCT_OF_NAV * nav.

Rationale for 0.40 (not the suggested 0.25):
- 0.25 would reject TLT (-36.25%), EFA (34.96%), and EEM (26.14%) from the
  live-run weights -- 3 of 8 names still blocked.
- 0.25 would have been an improvement over $8k but not a solution.
- 0.40 (40% of NAV) admits the observed maximum weight (TLT 36.25%) with
  ~10% headroom. At $100k NAV the cap is $40,000; the largest position is
  TLT at $36,250.
- A per-name cap of 40% is conservative for a diversified 8-name vol-targeted
  book. The gross long+short exposure is typically 1.5x to 2x NAV spread
  across all 8 names; 40% per name constrains any single name to no more than
  roughly 40/(150 to 200) = 20-27% of gross exposure.

MAX_TRADED_NOTIONAL_PER_RUN remains $16,000 (absolute). It is deliberately
NOT scaled with NAV; it is a fat-finger throughput limit on a single
execution run, not a portfolio constraint.

### What changed

execution/alpaca_paper.py:
- CAP_PER_POSITION_NOTIONAL removed; replaced by MAX_POSITION_PCT_OF_NAV = 0.40
- apply_guards signature: _cap: float -> _cap_pct: float, _nav: float
  (both default to module constants; production callers should pass live
  account equity as _nav)
- Cap check: abs(target_notional) > _cap becomes > _cap_pct * _nav
- Added deliberate-split comment at the constants block and in the docstring
- No change to MAX_TRADED_NOTIONAL_PER_RUN or the brake logic

tests/test_paper_execution.py:
- Removed import of CAP_PER_POSITION_NOTIONAL
- Three basic cap tests migrated to use explicit _cap_pct=0.10, _nav=100k
  overrides (deterministic, independent of module defaults)
- test_both_rejection_reasons_visible_in_dry_run: oversized target changed
  from $9k to $45k and explicit _cap_pct=0.40, _nav=100k added
- test_traded_notional_brake_uses_default_constant renamed and rewritten as
  test_traded_notional_brake_is_absolute (asserts $16k, no cap reference)
- Four new tests added:
    test_full_book_at_100k_nav_passes_cap (regression gate for T5 finding)
    test_cap_rejects_position_exceeding_pct_of_nav
    test_cap_scales_with_nav ($15k passes at $100k NAV, fails at $30k NAV)
    test_brake_fires_independently_of_nav (same brake fires at any NAV)

Test result: 31/31 pass. No live Alpaca calls.
