# Sprint v8.1 — Universe and Trend Signal: Walkthrough

**This sprint built an operational instrument, not a prediction.** The trend
rule (sign of a 120-day trailing return, vol-targeted, long-only/flat) is a
well-documented, generic effect used here purely to exercise the platform's
universe-loading, vol-targeting, and position-construction machinery. No
hypothesis about future returns is under test, and none is claimed.

## Summary

Sprint v8.1 built a small, transparent, mechanical time-series trend signal
across an 8-name liquid ETF universe (SPY, EFA, EEM, TLT, IEF, HYG, LQD, GLD),
with positions sized to a fixed per-name volatility target and capped gross
leverage. There is no economic hypothesis under test (PRD House Rule 1): the
deliverable is a daily target-position vector and the engineering correctness
of the pipeline that produces it, not a performance claim. Headline result:
all five engineering-correctness gates (E1-E5) pass on the real 2007-2026
universe, two of which (E2, E5) required correcting the PRD's own gate
definition after running against real data. **Verdict: the instrument is
correctly built.** This is not a confirmed-or-rejected hypothesis verdict —
there was no hypothesis to confirm or reject.

## Hypothesis & Falsification Criteria

There is no economic hypothesis under test (sprints/v8.1/PRD.md, House Rule 1
and Economic Hypothesis section state this explicitly). The criteria below are
engineering-correctness gates, not signal-quality gates.

| ID | Criterion | PRD threshold | Observed | Status |
|----|-----------|---------------|----------|--------|
| E1 | No look-ahead: position at t+1 unaffected by perturbing data after close t | exact match on unperturbed prefix | bit-for-bit match, synthetic and real data | **PASS** |
| E2 | No scaling/unit bug in the vol-targeting formula | formula identity holds | held exactly (`atol=1e-9`) on all 37,480 defined rows | **PASS (corrected)** |
| E3 | Gross leverage cap respected every day | `sum(abs(weight)) <= 2.0` | max observed 2.000, 0 violations | **PASS** |
| E4 | Reproducibility: identical inputs, identical output | bit-for-bit identical | confirmed, synthetic and real data | **PASS** |
| E5 | Point-in-time universe membership | no value before `max(L, W)` days of history | first valid signal at exactly day 120 for every ticker | **PASS (corrected)** |
| S1 | Guardrail statement | present verbatim | present (see Key Findings) | **stated** |

**Two corrections to the PRD, found by running the code, not assumed at design
time:**
- **E2** as originally written compared each asset's own realized volatility
  `sigma_i(t)` to the `v=10%` target band `[5%, 20%]`. This tests the wrong
  thing — `sigma_i(t)` is an *input* to the sizing formula, not its output, and
  comparing it to `v` is an empirical claim about each asset's natural
  volatility, not a check for a coding bug. EEM's realized vol averages 23.6%,
  outside that band — real emerging-market-equity behavior, not a defect. E2
  was rewritten to test the actual no-bug invariant on the formula's output:
  `raw_weight_i(t) * sigma_i(t) == signal_i(t) * min(v, w_max * sigma_i(t))`,
  which holds by construction for a correct implementation.
- **E5** as originally written required `L + W = 183` trading days of warmup.
  The mathematically exact minimum is `max(L, W) = 120`, because the trend
  lookback and the vol-estimation window share the same starting point and
  their warmups overlap rather than stack. Verified directly: every ticker's
  first valid signal appears at exactly day 120.

## Data Pipeline

**Source:** yfinance, daily OHLCV + adjusted close, via the existing
`signals.load.fetch` / `write_raw` boundary (no second ingest path).
**Universe (8 names, chosen for liquidity and history depth only — PRD House
Rule 4, not for trend performance):**

| ticker | asset class |
|--------|-------------|
| SPY | US large-cap equity |
| EFA | developed ex-US equity |
| EEM | emerging-market equity |
| TLT | 20+y US Treasury |
| IEF | 7-10y US Treasury |
| HYG | US high-yield credit |
| LQD | US investment-grade credit |
| GLD | gold |

**Date range:** 2007-04-11 to 2026-06-15, 4826 rows. All four newly-fetched
tickers (TLT, EFA, EEM, GLD) came back with this same start date — the PRD's
expectation of staggered inception (GLD 2004, EEM 2003, EFA 2001, vs SPY's
true 1993 inception) did not materialize, because `signals.load.DEFAULT_START
= "2007-04-11"` is the repo-wide convention already used for every other
ticker (`SPY.parquet` itself only goes back to 2007-04-11, not SPY's actual
inception). **There is no staggered inception in this dataset.**
`load_universe_close` still outer-joins rather than inner-joins as correct
practice, but the meaningful point-in-time test in this sprint turned out to
be the signal's own L/W warmup (E5), not differing data start dates.

**Transforms, in order:**
1. `adj_close` -> `log_ret = ln(adj_close[t] / adj_close[t-1])`
2. `trail_ret[t] = adj_close[t] / adj_close[t-L] - 1`, L=120
3. `signal[t] = 1 if trail_ret[t] > 0 else 0`, defined only once both the
   120-day and 63-day windows are satisfied
4. `sigma[t] = std(log_ret, window=63) * sqrt(252)`
5. `raw_weight[t] = signal[t] * min(v / sigma[t], w_max)`, v=0.10, w_max=0.50
6. `gross[t] = sum(abs(raw_weight))`; `scale[t] = min(1, g_max / gross[t])`,
   g_max=2.0
7. `weight[t] = raw_weight[t] * scale[t]`

**Known biases:** `adj_close` is yfinance's back-adjusted total-return series
(existing repo convention, not new here) — a measurement convention, not a
source of look-ahead. No survivorship bias (all 8 tickers are large,
currently-listed funds).

**Rows dropped:** none outright. 42 cells in the outer-joined close matrix are
`NaN` for SPY/IEF/HYG/LQD on dates where EFA/EEM/TLT/GLD have data (isolated
missing daily bars in the underlying per-ticker files, not an alignment bug).
These propagate as `NaN` through `trail_ret`/`sigma`/`signal` on exactly those
dates rather than being forward-filled — the conservative, correct choice.

## Signal Behavior

No IC, rank-IC, t-stat, or decay profile — explicitly out of scope (House Rule
1; this is not a predictive signal under test). What is reported instead:

**Coverage / activity per ticker** (full 2007-2026 sample, days where both
L and W warmups are satisfied):

| ticker | days defined | long fraction | first valid date |
|--------|-------------|----------------|-------------------|
| SPY | 4664 | 75.3% | 2007-10-01 |
| HYG | 4664 | 75.4% | 2007-10-01 |
| LQD | 4664 | 70.1% | 2007-10-01 |
| IEF | 4664 | 65.3% | 2007-10-01 |
| EFA | 4706 | 65.0% | 2007-10-01 |
| GLD | 4706 | 67.6% | 2007-10-01 |
| EEM | 4706 | 60.3% | 2007-10-01 |
| TLT | 4706 | 58.0% | 2007-10-01 |

No ticker is degenerate (stuck at 0% or 100% long). All in a plausible 58-75%
range for a sign-of-trailing-return rule over an 18-year, multi-regime sample.

**Average realized annualized vol per ticker** (informational only, see E2
correction above — this is *not* a target the sizing formula is being
graded against):

| ticker | avg realized vol |
|--------|-------------------|
| EEM | 23.6% |
| EFA | 18.8% |
| SPY | 17.0% |
| GLD | 16.8% |
| TLT | 14.5% |
| HYG | 8.4% |
| LQD | 7.2% |
| IEF | 6.6% |

`v=10%` sits roughly mid-spread. Bond/credit names will rarely hit the
`w_max=0.50` cap; equity/EM/gold names will be capped more often. This is
expected for one uniform `v` across a heterogeneous universe, not a defect.

## Backtest Results

**No backtest was run.** No Sharpe, hit rate, turnover, max drawdown, or
capacity estimate is computed or claimed anywhere in this sprint (House Rule
1). The numbers below are position-construction diagnostics, not performance:

- Gross exposure: mean 1.736, max 2.000 (the leverage cap binds on the
  highest-conviction days).
- Net exposure equals gross exposure on every single day — expected for a
  long-only/flat book with no shorts, and itself a useful sanity check that no
  negative weight leaked into the construction.
- `sprints/v8.1/plots/positions_and_exposure.png`: (a) a date x ticker weight
  heatmap and (b) gross/net exposure over time with the leverage-cap line.
  Both panels are titled explicitly as construction diagrams, not performance
  results.

A synthetic stress test (`test_gross_leverage_cap_respected_under_stress`) — a
smooth uptrend with near-zero realized vol across all 8 names, engineered so
the pre-cap gross would be far above 2.0 — confirms the cap actually binds
(observed gross reaches 2.000) rather than the real-data test having simply
never exercised it.

## Key Findings

1. **The instrument is correctly built**: no look-ahead, reproducible,
   bounded leverage, point-in-time universe membership, all verified directly
   against real data, not just asserted.
2. **Two of the PRD's own pre-registered gates (E2, E5) were mis-specified**
   and had to be corrected after running against real data. Both corrections
   are documented with the exact reasoning, not silently patched — consistent
   with this lab's practice (cf. sprint v6.6's C32/C33 revision) of revising a
   wrong gate in the open rather than forcing a pass or quietly lowering the
   bar.
3. **Staggered inception, a deliberate design feature in the PRD, did not
   materialize** because of an existing repo-wide data convention
   (`DEFAULT_START="2007-04-11"`) that predates this sprint. The point-in-time
   test that ended up mattering was the signal's own warmup window, not
   differing ticker start dates.
4. **A single uniform vol target (`v=10%`) produces materially different
   capping behavior across asset classes** — credit/rate ETFs (6-8% realized
   vol) are rarely capped, equity/EM/gold ETFs (15-24%) are capped more often.
   This is a property of choosing one risk budget for a heterogeneous universe,
   not a bug, and is worth keeping in mind if this instrument is ever extended.
5. **Long-only/flat construction has a free internal consistency check**: net
   exposure equals gross exposure on every day in this dataset. Any future
   change that introduces shorting should expect (and explicitly test for) net
   diverging from gross.

## Limitations

- **No cost model.** No transaction costs, slippage, or borrow are applied
  anywhere — there is no PnL claim, so none was needed, but this means the
  position vector is not directly tradeable as-is.
- **No predictive validation of any kind.** This is by design (House Rule 1),
  not an oversight, but it means this walkthrough cannot and does not say
  whether the trend rule "works" on this universe.
- **Single mechanical rule, single parameter set.** `L=120`, `W=63`, `v=0.10`,
  `w_max=0.50`, `g_max=2.0` are illustrative, pre-registered, and untuned — no
  parameter sensitivity analysis was performed because none is meaningful
  without a performance claim to be sensitive about.
- **Common start date across all 8 tickers** (2007-04-11) means the universe
  was never actually tested under true staggered inception, despite the PRD's
  framing — see Key Finding 3.
- **Multiple-testing context carried forward**: this is the programme's
  second instrument after the v1-v6.6 HY/IG research line and the v7.1 NAV
  wedge probe. It is not a multiple-comparison concern for v8.1 itself (no
  statistical claim is made), but would become one the moment any future
  sprint adds a performance claim on top of this construction.

## Reproducibility

- **Seeds:** none required for the production pipeline (`compute_trend` is a
  deterministic function of its inputs). The pytest stress test
  (`test_gross_leverage_cap_respected_under_stress`) and the synthetic
  fixtures in `tests/test_trend_signal.py` use `np.random.default_rng` with
  fixed seeds (0, 1, 2) where randomness is used at all.
- **Data snapshot:** `data/raw/{SPY,EFA,EEM,TLT,IEF,HYG,LQD,GLD}.parquet`,
  fetched via `signals.etf_universe.ingest`, snapshot date 2026-06-16
  (yfinance), date range 2007-04-11 to 2026-06-15.
- **Commit:** this sprint's code (`signals/etf_universe.py`,
  `signals/trend_signal.py`, `tests/test_trend_signal.py`,
  `data/processed/v8_1_target_positions.parquet`) was committed as
  `b1b78a8` immediately prior to this walkthrough.
- **Exact commands to regenerate every output:**

```bash
source venv/bin/activate

# 1. ingest the 4 new tickers (skip if data/raw/{TLT,EFA,EEM,GLD}.parquet already exist)
python -c "from signals.etf_universe import ingest; ingest(['TLT','EFA','EEM','GLD'])"

# 2. build the target-position vector
python -c "
from signals.etf_universe import load_universe_close
from signals.trend_signal import compute_trend, to_position_matrix
close = load_universe_close()
tidy = compute_trend(close)
to_position_matrix(tidy).to_parquet('data/processed/v8_1_target_positions.parquet')
"

# 3. run the full engineering-correctness test suite (E1-E5 + bounds)
pytest tests/test_trend_signal.py -v

# 4. regenerate the illustrative plot
python -c "
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from signals.etf_universe import load_universe_close, UNIVERSE
from signals.trend_signal import compute_trend, to_position_matrix
close = load_universe_close()
pos = to_position_matrix(compute_trend(close))
# see sprints/v8.1/notes.md T7 for the exact plotting code
"
```

## Next Steps

This sprint produced an instrument, not a result, so "next steps" means
extending the instrument, not following up on an inconclusive finding:

1. **Add a cost-aware turnover report** (no PnL claim implied) — how often
   does `signal_i(t)` flip per name per year, purely as an operational
   planning number for whoever might run this.
2. **If a performance claim is ever wanted**, that is a new, separately
   pre-registered sprint (a v8.2 or v9) with its own IC test and Sharpe
   threshold — explicitly not a retroactive addition to v8.1, which was
   scoped to have none (House Rule 1).
3. **Revisit the uniform `v=10%` choice** if this instrument is extended to a
   wider or more heterogeneous universe — Key Finding 4 shows it already
   produces uneven capping behavior across just 8 names.
4. **Decide whether true staggered inception is worth testing** by fetching
   each ticker from its actual listing date rather than the repo-wide
   2007-04-11 floor, if the point-in-time logic in `load_universe_close` /
   `compute_trend` needs a harder real-world test than this sample provided.
