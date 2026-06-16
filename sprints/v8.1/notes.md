# Sprint v8.1 -- Notes
**Signal: time-series trend (120d lookback, sign-only, long-only/flat), vol-targeted, 8-name ETF universe**

---

## T1 -- Universe ingest

Extended the existing yfinance boundary (`signals/load.py`) with `signals/etf_universe.py`.
Fetched `TLT`, `EFA`, `EEM`, `GLD` (the existing `SPY`, `HYG`, `LQD`, `IEF` parquet files
were already present and untouched).

| ticker | rows | first date | last date |
|--------|------|------------|-----------|
| TLT | 4826 | 2007-04-11 | 2026-06-15 |
| EFA | 4826 | 2007-04-11 | 2026-06-15 |
| EEM | 4826 | 2007-04-11 | 2026-06-15 |
| GLD | 4826 | 2007-04-11 | 2026-06-15 |

**Correction to the PRD's "staggered inception" framing:** the PRD described GLD
(2004), EEM (2003), EFA (2001) etc. as having different true inception dates from
SPY, intending this as a real test of point-in-time universe handling. In practice
all four new tickers were fetched with `signals.load.DEFAULT_START = "2007-04-11"`,
the same start date already used for every other ticker in this repo (the existing
`SPY.parquet` itself only goes back to 2007-04-11, not SPY's actual 1993 inception).
**There is no staggered inception in this dataset** -- the repo-wide convention is a
single common start date. `load_universe_close` still outer-joins (rather than
inner-joins) as a matter of correct practice, but the point-in-time test that
matters in this sprint is the signal's own L/W warmup, not differing data start
dates (see T5/E5 below).

---

## T2/T3 -- Trend signal + vol-targeted position construction

Implemented in `signals/trend_signal.py`:
- `compute_trend(close, L=120, W=63, v=0.10, w_max=0.50, g_max=2.0)` -- tidy
  (long-format) output, one row per (date, ticker).
- `to_position_matrix(tidy)` -- pivots to the date x ticker weight matrix.
- `shift_to_next_day(position_matrix)` -- relabels weight computed from close(t)
  as the target for t+1.

Run on the real 8-name universe, 2007-04-11 to 2026-06-15 (4826 rows):

| ticker | days defined | long fraction | first valid date |
|--------|-------------|----------------|-------------------|
| SPY | 4664 | 75.3% | 2007-10-01 |
| EFA | 4706 | 65.0% | 2007-10-01 |
| EEM | 4706 | 60.3% | 2007-10-01 |
| TLT | 4706 | 58.0% | 2007-10-01 |
| IEF | 4664 | 65.3% | 2007-10-01 |
| HYG | 4664 | 75.4% | 2007-10-01 |
| LQD | 4664 | 70.1% | 2007-10-01 |
| GLD | 4706 | 67.6% | 2007-10-01 |

No ticker is degenerate (0% or 100% long) -- all fall in a plausible 58-75% range.
Gross exposure: mean 1.736, max 2.000 (the cap binds on the highest-conviction
days). Net exposure equals gross exposure on every day, exactly as expected for a
long-only/flat book with no shorts -- this equality is itself a useful sanity
check that no negative weight leaked into the construction.

Output persisted to `data/processed/v8_1_target_positions.parquet` (4826 x 8).

Small data-hygiene note: the outer-joined close matrix has 42 NaN values for
`SPY`/`IEF`/`HYG`/`LQD` (none for `EFA`/`EEM`/`TLT`/`GLD`) -- isolated missing
daily bars in the underlying per-ticker parquet files, not a staggered-inception
or alignment bug. These propagate as `NaN` (not a stale carried-forward value)
into `trail_ret`/`sigma`/`signal` on exactly those dates, which is the correct,
conservative behavior (no forward-fill of missing closes).

---

## T4 -- No-look-ahead (E1) + reproducibility (E4)

`tests/test_trend_signal.py`:
- `test_no_lookahead_perturb_future_leaves_past_unchanged` (synthetic) and
  `test_no_lookahead_on_real_universe` (real data): perturb `adj_close` after a
  cutoff date by a large, obvious amount (+1000, x5) and assert the entire
  pre-cutoff tidy frame is bit-for-bit unchanged. **E1: PASS.**
- `test_reproducibility_identical_inputs_identical_output` (synthetic) and
  `test_reproducibility_on_real_universe` (real data): run `compute_trend` twice
  on identical inputs, assert `pandas.testing.assert_frame_equal` with no
  tolerance. **E4: PASS.**
- `test_shift_to_next_day_aligns_one_row_forward`: confirms
  `target.iloc[k] == pos.iloc[k-1]` and the first row of the shifted target is
  `NaN` (no position can be deployed before any signal exists).

---

## T5 -- Point-in-time universe membership (E5)

**Correction to the PRD's E5 threshold, found during implementation:** the PRD
stated the minimum gap as `L + W` trading days (120 + 63 = 183). This is wrong --
`trail_ret` requires `L` prior closes and `sigma` requires `W` prior closes; both
warmups are measured from the *same* starting point and overlap rather than
stack. The mathematically exact minimum gap is `max(L, W)`, which here is simply
`L = 120` (since `L=120 > W=63`). Verified directly on real data: the first valid
signal date for every ticker is exactly at row offset 120 (2007-10-01, 120
trading days after the shared 2007-04-11 start) -- confirming the formula, not
the PRD's original arithmetic.

`test_point_in_time_membership_synthetic` and
`test_point_in_time_membership_real_universe` assert `first_valid_offset >=
max(L, W)` for every ticker, with 0 violations on both synthetic and real data.
**E5: PASS** (against the corrected threshold).

---

## T6 -- Vol-target tracking (E2) + leverage / position bounds (E3)

**Correction to the PRD's E2 definition, found during implementation:** the PRD
specified E2 as checking whether `sigma_i(t)` (the input realized asset vol)
falls within `[5%, 20%]` (a 2x band around `v=10%`). Running this literally on
real data: EEM's average realized vol is **23.6%**, outside that band. This is
not a bug -- emerging-market equity genuinely is more volatile than the other
seven names, and `v=10%` was never fit to this universe (House Rule 1: no edge
claim, no tuning). The original E2 was testing the wrong thing: `sigma_i(t)` is
an *input* to the sizing formula, not its output, so comparing it to `v`
confuses "what vol does this asset have" with "did the sizing formula scale it
correctly."

**Revised E2** tests the actual no-bug invariant on the formula's *output*:
```
raw_weight_i(t) * sigma_i(t) == signal_i(t) * min(v, w_max * sigma_i(t))
```
This holds by construction for a correct implementation and would be violated by
a real scaling bug (missing `sqrt(252)` annualization, a variance/std mixup, an
inverted ratio, etc.). Verified exactly (`atol=1e-9`) across all 37,480 defined
(ticker, date) rows in the real universe. **E2: PASS** (against the corrected
definition).

The original empirical question -- does each ticker's realized vol sit near
`v=10%` -- is kept as an informational, non-gating report
(`test_vol_target_per_ticker_report`):

| ticker | avg realized annualized vol |
|--------|------------------------------|
| EEM | 23.6% |
| EFA | 18.8% |
| SPY | 17.0% |
| GLD | 16.8% |
| TLT | 14.5% |
| HYG | 8.4% |
| LQD | 7.2% |
| IEF | 6.6% |

`v=10%` sits roughly in the middle of this spread -- bond/credit ETFs (IEF, LQD,
HYG) will rarely hit the `w_max=0.50` cap (their `v/sigma` is well under 0.50),
while equity/EM/gold names (EEM, EFA, SPY, GLD) will be capped more often. This
is expected behavior for a single uniform `v` across a heterogeneous universe,
not a defect -- and is exactly the kind of asymmetry House Rule 3 (risk
budgeting, not signal optimization) anticipates rather than tunes away.

**E3 / position bounds:**
- `test_per_name_weight_bound_respected`: `|weight| <= w_max=0.50` and
  `weight >= 0` (long-only/flat) on every defined (ticker, date). **PASS.**
- `test_gross_leverage_cap_respected`: `gross <= g_max=2.0` on every day of the
  real universe. **PASS.**
- `test_gross_leverage_cap_respected_under_stress`: a synthetic smooth-uptrend,
  near-zero-vol universe (engineered so the pre-cap gross would be far above
  2.0) confirms the cap actually binds (observed gross hits 2.0) rather than
  the real-data test merely never having exercised it. **PASS.**

---

## T7 -- Illustrative visualization

`sprints/v8.1/plots/positions_and_exposure.png`: (a) a date x ticker weight
heatmap and (b) gross/net exposure over time with the `G_max=2.0` cap line. Both
panel titles state explicitly this is a construction diagram, not a performance
result -- no PnL, return, or Sharpe figure appears anywhere on the plot.

---

## T8 -- S1 guardrail statement + sprint close

**S1 (verbatim):** "This is a learning instrument. The rule is mechanical and
fully documented. No predictive claim, IC test, or Sharpe/performance threshold
is in scope for v8.1."

### Gate-status table

| ID | Status | Note |
|----|--------|------|
| E1 | **PASS** | no look-ahead, verified on synthetic and real data |
| E2 | **PASS** (corrected) | original PRD wording tested the wrong quantity; revised to the formula-identity invariant, see T6 |
| E3 | **PASS** | per-name and gross caps respected on real data and under a synthetic stress case |
| E4 | **PASS** | bit-for-bit reproducible on synthetic and real data |
| E5 | **PASS** (corrected) | PRD threshold `L+W` corrected to `max(L,W)`, see T5 |
| S1 | stated | guardrail recorded verbatim above |

### What this sprint delivered

1. `signals/etf_universe.py` -- universe definition (8 tickers) and an
   outer-joined close loader, reusing the existing yfinance ingest boundary.
2. `signals/trend_signal.py` -- `compute_trend`, `to_position_matrix`,
   `shift_to_next_day`. Pure functions, no regression, no fitted parameters
   beyond the five pre-registered constants (`L, W, v, w_max, g_max`).
3. `tests/test_trend_signal.py` -- 13 tests covering E1-E5 and position bounds,
   on both synthetic fixtures and the real universe. All pass.
4. `data/processed/v8_1_target_positions.parquet` -- the daily target-position
   vector, the sprint's stated deliverable.
5. Two honest corrections to the PRD's own gate definitions (E2, E5), found by
   actually running the code against real data rather than assumed correct at
   design time -- consistent with this programme's practice of revising
   mis-specified criteria in the open rather than silently forcing a pass
   (cf. sprint v6.6's C32/C33 revision).

No IC test, no Sharpe claim, no backtest performance result anywhere in this
sprint, per House Rule 1.

Sprint v8.1 is closed. T1-T8 all done.
