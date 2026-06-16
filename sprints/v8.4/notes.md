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

Sprint v8.4 status: **implementation done (T1-T4 core + test suite);
T5-T8 partially (fill/reconciliation/cost implemented and tested with
mocked data; live feed deferred to first paper session); T9 passes dry-run.**
