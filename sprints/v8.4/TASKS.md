# Sprint v8.4 - Tasks

Status: `[ ]` = not done, `[x]` = done.

**Dependency order:** T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9.
No hard data-gate stop (no G-series gate blocks execution of later tasks
the way G0a stopped v7.1) -- but P7 (credential security) is enforced at
every task: if a key appears in any file at any point, stop and rotate
before continuing.

---

- [x] **Task T1: alpaca-py installation and connection (P7, P8 groundwork)**
  - Add `alpaca-py` to `pyproject.toml` `[project.dependencies]` and
    `pip install alpaca-py` in the virtual environment.
  - Write `execution/paper.py::connect(dry_run: bool = True)` that reads
    `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` from environment
    only (never from a file, never hardcoded), constructs a
    `TradingClient(base_url="https://paper-api.alpaca.markets")`, and
    returns the client. Raise a clear `EnvironmentError` if either variable
    is missing.
  - Acceptance: `python -c "from execution.paper import connect; connect()"` succeeds
    with credentials in environment; fails with a clear message without them.
    Grep the module for any API key pattern -- zero matches (P7 prerequisite).
  - Files: `execution/paper.py`, `pyproject.toml`
  - Validation: fails if the module imports succeed but credentials are
    hardcoded or read from a file; fails if `connect()` does not raise on
    missing credentials.

- [x] **Task T2: current-positions reader**
  - `execution/paper.py::get_current_positions(client) -> dict[str, float]`
    calls `client.get_all_positions()` and returns a `{ticker: signed_notional}`
    dict (positive = long, negative = short, tickers not in UNIVERSE ignored).
    In dry-run mode, returns an empty dict (no Alpaca calls made -- P8).
  - Acceptance: unit test with a mocked `TradingClient` confirms correct
    sign convention (a short position of 10 shares at $50 = -$500);
    dry-run returns `{}` without any mock being called.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if short positions are returned with the wrong sign;
    fails if dry-run calls `get_all_positions`.

- [x] **Task T3: order translator with zero-crossing**
  - `execution/paper.py::compute_orders(target_weights, current_notionals,
    close_prices, paper_nav) -> list[OrderSpec]`
    where `OrderSpec` is a frozen dataclass: `(ticker, delta_notional,
    target_notional, guard_status)`. Implements Steps 1-6 of the PRD's
    Signal Definition: target notional, delta computation, `DELTA_MIN_NOTIONAL`
    skipping, and zero-crossing (single signed delta order).
    `guard_status` is set to `'PENDING'` at this stage (guards applied in T4).
  - Acceptance: test cases: (a) currently flat, target +$3000 SPY -> buy
    order for $3000; (b) currently long +$3000 SPY, target -$2000 -> single
    sell order for $5000 (cross through zero); (c) delta < $10 -> skipped.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if the zero-crossing case produces two orders instead
    of one; fails if the sign of the delta is inverted.

- [x] **Task T4: guard layer (P2, P3)**
  - `execution/paper.py::apply_guards(orders, dry_run) -> list[OrderSpec]`
    iterates the pending order list and applies:
    1. `|target_notional_i| > CAP_PER_POSITION_NOTIONAL (8000)`: set
       `guard_status = 'REJECTED_CAP'`, log the rejection with target and
       cap values.
    2. Cumulative submitted count would exceed `MAX_ORDERS_PER_RUN (20)`:
       set `guard_status = 'REJECTED_MAX_ORDERS'` for this and all
       remaining orders.
    3. `dry_run=True`: set `guard_status = 'DRY_RUN'` for all remaining
       (replaces any prior PENDING status).
    Returns the updated list with all statuses set (no order is left in
    PENDING after this function).
  - Acceptance: adversarial test -- pass an order with `target_notional=9000`
    (above cap), confirm it is REJECTED_CAP and NOT submitted; pass 21
    orders, confirm the 21st is REJECTED_MAX_ORDERS; confirm dry_run sets
    all to DRY_RUN. All three tests in `tests/test_paper_execution.py`.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if any REJECTED_CAP order could reach the Alpaca
    submission call (the cap guard must run BEFORE submission, not after).

- [~] **Task T5: order submission and fill reader (P8)**
  - `execution/paper.py::submit_orders(client, orders, dry_run)` iterates
    only orders with `guard_status == 'PENDING'` and submits each as a
    `MarketOrderRequest` with `notional` quantity, `time_in_force=DAY`.
    Returns a list of `FillRecord(ticker, intended_notional,
    filled_notional, fill_price, order_id, status)`.
    Dry-run: returns empty list, makes zero API calls (P8).
  - `execution/paper.py::poll_fills(client, order_ids, timeout_secs=30)`
    polls `client.get_order_by_id` until all orders reach a terminal state
    or timeout is reached; marks timed-out orders as `status='TIMEOUT'` in
    the fill record.
  - Acceptance: mocked Alpaca confirms zero API calls in dry-run; in
    live mode (integration test against real paper account) a $100 notional
    test order for SPY fills within the timeout.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if `submit_orders` calls any Alpaca endpoint when
    dry_run=True; fails if a non-PENDING order status is submitted.

- [x] **Task T6: cost marking (P5)**
  - `execution/paper.py::mark_costs(fills, borrow_notional_by_ticker)
    -> list[FillRecord]` enriches each fill record with:
    `simulated_cost = (half_spread_bp + slippage_bp) * 1e-4 * |filled_notional|
    + borrow_annual / 252 * borrow_notional` using `CostParams()` defaults
    exactly.
  - Acceptance: unit test confirms cost constants match
    `execution.costs.CostParams()` values exactly (P5);
    test a $1000 buy of SPY: cost = (1.5+0.5)*1e-4*1000 = $0.20 exactly;
    test a $1000 short: borrow = 0.004/252*1000 = $0.0159/day.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if any cost constant differs from `CostParams()`
    defaults; fails if borrow is applied to long positions.

- [x] **Task T7: fill-vs-intention reconciliation (P1)**
  - `execution/paper.py::reconcile(orders, fills) -> dict` produces a per-
    ticker dict of `{ticker: {intended, filled, discrepancy, flagged, status}}`
    and writes it to
    `execution/logs/reconciliation_{YYYY-MM-DD}.json`.
    Flags any `|discrepancy| > max(10, 0.005 * |intended|)`.
    Every order -- whether submitted, rejected by guard, or rejected by
    Alpaca -- appears in the reconciliation output (P1: no silent drops).
  - Acceptance: test with one normal fill, one cap-rejected order, one
    Alpaca-rejected order; confirm all three appear in the reconciliation
    output with the correct status; confirm the flagged-discrepancy
    threshold fires at the right boundary; confirm the log file is written.
  - Files: `execution/paper.py`, `execution/logs/.gitkeep`,
    `tests/test_paper_execution.py`
  - Validation: fails if any order is absent from the reconciliation output;
    fails if the log directory is not created; fails if the flagging
    threshold deviates from the pre-registered formula.

- [~] **Task T8: attribution feed (P6)**
  - `execution/paper.py::feed_attribution(fills, close_prices, dividends,
    date)` translates paper fill records into v8.3-compatible rows and
    appends them to `data/processed/attribution.parquet`, matching the
    tidy frame schema from `risk.attribution.build_tidy_attribution`
    (columns: date, ticker, asset_class, weight, pnl, carry, price_change,
    gross_pnl, net_pnl, turnover_cost, borrow_cost, ...).
  - Acceptance: after a dry-run (zero fills), the parquet file is
    unchanged (no empty rows appended); after a synthetic fill record, the
    parquet contains exactly one new row per ticker with correct values;
    schema matches the v8.3 column list exactly.
  - Files: `execution/paper.py`, `tests/test_paper_execution.py`
  - Validation: fails if the schema does not match the v8.3 tidy frame;
    fails if dry-run appends empty rows.

- [x] **Task T9: full integration test -- dry-run with adversarial scenarios (P1-P8)**
  - Single `tests/test_paper_execution.py::test_full_dry_run_pipeline` that
    runs the complete T1-T8 pipeline with `dry_run=True`, mocked Alpaca,
    and an adversarial target that includes: one position above the cap,
    one zero-crossing, one position below `DELTA_MIN_NOTIONAL`, and one
    that would tip the order count over `MAX_ORDERS_PER_RUN`. Asserts all
    P-series gates pass:
    - P1: reconciliation contains all 8 tickers, none silently dropped.
    - P2: the cap-violating order is REJECTED_CAP.
    - P3: no more than MAX_ORDERS_PER_RUN orders advance to submission.
    - P4: verify the signal timestamp is yesterday's close (not today's).
    - P5: cost constants match CostParams() exactly.
    - P6: attribution rows have correct schema (checked against column list).
    - P7: grep `execution/paper.py` for any API key pattern -- zero matches.
    - P8: zero Alpaca API calls made.
  - Acceptance: the test passes without any real Alpaca credentials.
  - Files: `tests/test_paper_execution.py`
  - Validation: fails if any P-gate fires; fails if the test requires real
    credentials to pass.
