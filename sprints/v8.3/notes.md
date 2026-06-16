# Sprint v8.3 -- Notes

This sprint has not started implementation yet. This file currently
contains only the decision log for a PRD revision made before any code was
written -- a correction to the originally-drafted factor set, made before
T1 ran, not after seeing any output.

---

## PRD revision: factor set correction (pre-implementation)

The original v8.3 PRD draft specified a four-factor regression for
Decomposition 4 that included the He-Kelly-Manela intermediary capital
risk factor (`ICAP_ret`), gated on a data-availability probe (`G1`) modeled
on v7.1's NAV probe. Before any code was written, this was revised.

### Change 1 -- the factor set

**Removed:** the He-Kelly-Manela intermediary capital factor from
Decomposition 4 entirely.

**Reason:** this book trades and is marked daily. The attribution
regresses daily book return on daily factor returns. The Manela series is
published monthly with a multi-month update lag. Including it would have
required one of two things, both refused:
1. Interpolating a synthetic daily series from the monthly print -- refused
   on the same principle as the v7.1 NAV decision: never fabricate a
   series to fill a frequency gap. v7.1 stopped a whole sprint rather than
   substitute a proxy for missing daily NAV data; the same standard
   applies here, at the level of a single factor rather than a whole
   data source.
2. Collapsing the entire attribution to monthly frequency to match the
   factor -- refused because daily granularity is the explicit point of
   v8.3 (per-day reconciliation, per-day P&L decomposition). Trading a
   daily attribution down to monthly to accommodate one factor would defeat
   the sprint's purpose to serve a single regression term.

**Replacement:** four daily, liquid, exposure-matched factors, all already
in the v8.1 universe and already daily: equity beta via `SPY`, rates/
duration beta via `IEF`, credit-spread beta via `HYG_ret - IEF_ret` (high
yield over duration-matched treasuries, isolating the credit component
from the duration component already captured by the rates term), and a
real-asset term via `GLD` (offered as optional in the revision request,
included here since it is already daily, already in the universe, and
costs nothing to add).

**Stated in the PRD explicitly:** Decomposition 4 is the one genuinely
empirical, non-tautological output of this sprint. Decompositions 1, 2, 3,
5, and 6 are accounting identities -- their "residual" or "selection" term
is *defined* as whatever total minus the other term leaves over, so they
reconcile by construction and prove nothing empirical. Decomposition 4's
betas are fit on a trailing window and applied to data the regression has
not seen, so its R-squared and residual are real, falsifiable-in-spirit
numbers. This is exactly why every factor entering it must be daily and
exposure-matched -- a frequency-mismatched factor would have corrupted the
one part of this sprint that actually measures something. House Rule 7 (no
security-selection layer exists) remains attached to the regression
residual wherever it appears: the residual is exposure-timing, never
alpha, never selection, regardless of which factors are in the regression.

### Change 2 -- G1 rewritten

The original G1 was a hard data-availability probe (modeled on v7.1's
G0a): attempt to retrieve the Manela factor at daily or best-available
frequency, document coverage, and only then decide the fallback. This was
unnecessary and is removed. The factor is omitted by design regardless of
whether a daily version exists somewhere -- there is no scenario in which
this sprint would use it, so there is nothing to probe for.

G1 is rewritten as a one-line documented omission, not a probe outcome:
the monthly intermediary-capital factor is theoretically the cleaner
credit factor (it is a genuine, well-regarded measure of financial
intermediary balance-sheet capacity), but it is frequency-mismatched to a
daily attribution, so it is intentionally omitted here and revisited only
at the desk, with proper non-daily-frequency tooling, never inside this
daily pipeline. This is named the same way House Rule 7 names the absent
security-selection layer: a permanent, stated gap, not a future task and
not a probe pending a different answer.

**Dependent tasks closed out, not renumbered:** Task T1 originally covered
both G1 (the probe) and G2 (dividend history). G1's probe portion is
removed from T1; G2 remains. Task T4 (factor regression) no longer has a
conditional branch on G1's outcome -- the four-factor set is now fixed,
not contingent on anything T1 finds. No task numbering changed, since
nothing else in the task list was actually gated on G1's probe outcome
specifically.

### Change 3 -- standing rule (House Rule 8)

Added to the v8 House Rules in the PRD, not just to this sprint's own
scope, because the risk this corrects is a recurrence risk across the rest
of the v8 programme, not a one-sprint mistake:

**No monthly or lower-frequency factor, including the He-Kelly-Manela
intermediary capital factor, is to be used in any daily attribution, daily
regression, daily residual decomposition, or daily signal in v8.3 or any
later v8 sprint.** If a later sprint's prompt, an inherited PRD section, or
a residual-handling step references the Manela factor or any monthly
factor in a daily context, that is to be treated as a stale carryover --
omit it and record the omission, exactly as G1 does here, rather than
interpolating it to daily frequency. Daily work in this programme uses
daily, exposure-matched factors only. Lower-frequency factors are a
desk-data concern, explicitly out of scope for this programme's daily
pipeline.

This mirrors how House Rule 7 (no security-selection layer) and the v7.1
NAV decision (no synthetic data to fill a frequency/availability gap)
already operate in this programme: a permanent constraint stated once,
inherited by every later sprint, rather than re-litigated each time a
similar temptation appears.

---

## Implementation (all tasks T1-T10)

A design choice made during implementation, recorded as such: the PRD
allowed either "rolling or full-sample OLS" for Decomposition 4 in one
later restatement of the task. **Rolling was used, full-sample was not**
-- a full-sample fit would use every date's data to explain every other
date, including dates before it in the sample, which fails the sprint's
own no-future-leakage requirement by construction. Rolling, refit daily,
fit on `t-252..t-1` to explain `t`, is the only choice that can honestly
pass `test_factor_regression_no_future_leakage`.

### T1 -- dividend history (G2)

`signals/dividends.py::fetch_dividends/ingest/load_dividend_matrix`, a new
yfinance access path (the existing `signals.load.fetch` deliberately
excludes dividend rows via `actions=False`). Retrieved for all 8 tickers:

| ticker | distributions | first | last |
|--------|---------------|-------|------|
| SPY | 134 | 1993-03-19 | 2026-03-20 |
| EFA | 47 | 2001-10-02 | 2026-06-15 |
| EEM | 47 | 2003-12-22 | 2026-06-15 |
| TLT | 285 | 2002-09-03 | 2026-06-01 |
| IEF | 287 | 2002-09-03 | 2026-06-01 |
| HYG | 229 | 2007-05-01 | 2026-06-01 |
| LQD | 286 | 2002-09-03 | 2026-06-01 |
| GLD | 0 | -- | -- |

**G2: PASS for 7/8 tickers.** GLD's zero count is the correct expected
value, not a gap or a failure -- physical gold pays no income. No G1 probe
was run (House Rule 8 / Change 2 above).

### T2-T7 -- the seven decompositions, all reconciling

`risk/attribution.py`. `build_v82_book(close)` reconstructs the exact v8.2
closing book (`L=120`, `long_short=True`, `k_dead_zone=0.5`, `band_pct=0.20`,
`rebal_freq=1`) -- frozen, not retuned (House Rule 4). Full sample,
2007-04-11 to 2026-06-15:

| decomposition | reconciles to | max residual | tolerance |
|---------------|----------------|---------------|-----------|
| R1 (per-instrument) | gross P&L | 1.46e-11 | 1e-6 x notional |
| R1 (per-asset-class) | gross P&L | 2.91e-11 | 1e-6 x notional |
| R2 (long/short) | gross P&L | 1.46e-11 | 1e-6 x notional |
| R3 (directional/selection) | gross P&L | 7.28e-12 | 1e-6 x notional |
| R4 (factor-explained/residual) | gross P&L | 7.28e-12 | 1e-6 x notional |
| R5 (carry/price, per instrument) | per-instrument P&L | 9.09e-13 | 1e-6 x notional |
| R6 (gross/net/cost) | net P&L | 0.0 (exact) | 1e-6 x notional |
| R7 (MCTR by sleeve) | **portfolio sigma**, not P&L | 1.39e-17 | 1e-6 x sigma |

All eight reconciliations pass, several to literal machine-precision zero
(R6, by construction -- it re-exposes `run_multi_asset`'s own components
rather than recomputing them).

### Findings worth flagging (forensic, not performance claims -- House Rule 1)

1. **Commodity (GLD alone) is the largest-contributing sleeve**: $434,238
   of $880,154 total gross P&L (49%), more than rates ($181,515) and
   credit ($178,381) combined, far more than equity ($86,020). This is a
   real, measured fact about this specific book over this specific
   sample -- not a reason to overweight gold going forward (single-regime
   caveat, House Rule 6).
2. **Carry dominates gross P&L: 69.6% ($612,274 of $880,154)**, driven
   almost entirely by HYG ($266,429) and LQD ($123,602) distributions.
   Price change contributes only $267,880. This book's money came mostly
   from collecting credit distributions while holding a trend signal,
   not from price appreciation -- a materially different story than "the
   trend signal worked."
3. **The factor regression's beta-explained component is net NEGATIVE
   (-$1,440,668) while the residual (exposure-timing) is positive
   (+$2,295,741)**, summing to the actual $880,154 gross P&L. The
   four-factor model (mean R^2 ~42% day to day) would have lost money in
   aggregate if the book had simply held its average factor exposure
   passively. The honest reading of this result is deliberately cautious:
   the four daily factors (SPY, IEF, HYG-IEF, GLD) are daily-return
   factors and do not span carry -- they cannot distinguish between a
   dollar earned from a coupon distribution and a dollar earned from a
   price move on the same day. Because carry accounts for 70% of gross
   P&L (finding 2 -- dominated by HYG and LQD coupons), the positive
   exposure-timing residual is substantially confounded with that coupon
   income, which the regression model sees as "unexplained" relative to
   the daily price-return factors. The realized gains in this book came
   from a combination of carry accrual and exposure-timing that a linear
   daily-return factor model cannot cleanly separate. The residual is not
   evidence of standalone trend-timing skill, and it is not a return
   source that would survive after stripping out carry. As always, the
   residual is exposure-timing, never security selection, never alpha
   (House Rule 7) -- there is no security-selection mechanism to credit
   it to, and the confound with carry makes it even less interpretable
   as a stand-alone timing signal without further decomposition.
4. **Turnover cost is small in absolute terms**: $20,989 over the full
   19-year sample (0.04 bps/day average), consistent with the v8.2 T2+T2b
   book's ~5.5x annualized turnover at ~2bp round-trip cost
   (5.5 x 2bp ~= 11bp/year x 19 years x $1M =~ $21k, matching almost
   exactly). Borrow cost ($47,127) exceeds turnover cost, reflecting the
   sustained short positions a long/short book carries.
5. **Ex-ante and realized sleeve MCTR are close on average** (e.g. equity
   0.005137 ex-ante vs 0.005139 realized) but this average masks real
   divergence around vol regime shifts, visible in
   `sprints/v8.3/plots/mctr_by_sleeve.png` -- the average understates how
   much the two can disagree on any given day.

### T8 -- reconciliation test suite

`tests/test_attribution.py`, 20 tests: R1-R7 reconciliation (including the
asset-class no-double-count check), the factor regression's no-future-
leakage property (both a real-data perturbation test and a fully
controlled synthetic test that perturbs only the evaluation day's own
factor value), GLD's zero carry, distribution lumpiness (not smoothed),
book-construction and per-instrument-P&L no-look-ahead, and the tidy
frame's shape/completeness (with an explicit, bounded allowance for the
same trailing data-gap behavior already documented in v8.1/v8.2 -- a few
HYG/IEF closes are missing in this data snapshot's last few days, which
correctly produces NaN P&L on exactly those (date, ticker) cells while the
held weight is carried forward, not a bug).

### T9 -- sanity baseline, hand-verified

Two dates checked independently (hand-summed per-instrument P&L vs the
pipeline's `gross_pnl_series`, not reusing the decomposition code path
being validated): 2008-10-15 (a date with one of the largest absolute
gross P&L moves in the sample) and a mid-sample HYG distribution date.
Both match to floating-point precision (diff <= 1.14e-13).

### T10 -- notebook + tidy output

`notebooks/08_3_attribution.ipynb` (built by `scripts/build_notebook_v8_3.py`),
executes clean, ends with `[notebook clean]`. Persists
`data/processed/attribution.parquet` (38,608 rows, one per date x ticker,
dashboard-ready: pnl, carry, price_change, gross/net P&L, cost,
directional/selection, beta_explained, residual, r_squared, all broadcast
or per-instrument as appropriate) and
`data/processed/attribution_mctr_by_sleeve.parquet` (date x sleeve x mode).

### S3 guardrail (verbatim)

"This is a forensic accounting layer on the v8.2 book. It explains
realized P&L; it does not predict future P&L, and it does not contain or
imply a security-selection capability -- every instrument here is an ETF
basket. Reconciliation to machine precision confirms the bookkeeping is
correct; it is not evidence the book is good."

### Gate-status table

| ID | Status | Note |
|----|--------|------|
| G1 | N/A by design | omitted, not probed (House Rule 8) |
| G2 | PASS | 7/8 tickers with real distributions; GLD's zero is correct |
| E1' | PASS | no look-ahead in book construction, per-instrument P&L, or the factor regression (real-data and synthetic tests) |
| R1-R7 | PASS | see reconciliation table above |
| S3 | stated | guardrail above |

Sprint v8.3 status: **closed**. T1-T10 all done.
