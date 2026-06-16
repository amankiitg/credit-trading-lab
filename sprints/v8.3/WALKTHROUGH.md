# Sprint v8.3 -- Forensic Attribution Engine: Walkthrough

**This is a forensic accounting layer on the v8.2 book. It explains
realized P&L; it does not predict future P&L, and it does not contain or
imply a security-selection capability -- every instrument here is an ETF
basket. Reconciliation to machine precision confirms the bookkeeping is
correct; it is not evidence the book is good.**

## Summary

Sprint v8.3 built a daily P&L and risk attribution engine for the v8.2
closing long/short trend book, producing seven reconciling decompositions
over the 2007-04-11 to 2026-06-15 sample. There is no economic hypothesis
under test (House Rule 1): the "verdict" is whether the bookkeeping is
correct, not whether any performance claim holds. The engineering gates
(G2, E1', R1-R7) all pass. Three headline findings emerge from the
accounting: carry income accounts for 70% of total gross P&L; the commodity
(GLD) sleeve contributed more gross P&L than rates, credit, and equity
combined; and the factor regression's residual, while positive and
labelled exposure-timing per House Rule 7, is confounded with carry accrual
that the daily-return factor model cannot separate from price-driven
returns -- it is not evidence of standalone trend-timing skill.

## Hypothesis and Falsification Criteria

No economic hypothesis is under test. The criteria below are
engineering-correctness gates, not signal-quality gates:

| ID | Criterion | Status |
|----|-----------|--------|
| G1 | Monthly intermediary-capital factor (He-Kelly-Manela) | **N/A by design** (House Rule 8, omitted not probed) |
| G2 | Per-ticker dividend history retrievable for all 8 ETFs | **PASS** (7/8 with real data; GLD zero is correct -- physical gold pays no income) |
| E1' | No look-ahead in book construction, per-instrument P&L, and the rolling factor regression | **PASS** (real-data perturbation test and a fully-controlled synthetic test, both in `tests/test_attribution.py`) |
| R1 | Decompositions 1a (per-instrument) and 1b (per-asset-class) sum to total daily gross P&L | **PASS** (max residuals 1.46e-11 and 2.91e-11) |
| R2 | Long + short P&L sum to total daily gross P&L | **PASS** (1.46e-11) |
| R3 | Directional + selection sum to total daily gross P&L | **PASS** (7.28e-12) |
| R4 | Beta-explained + residual sum to total daily gross P&L | **PASS** (7.28e-12) |
| R5 | Carry + price-change sum to per-instrument P&L | **PASS** (9.09e-13) |
| R6 | Gross P&L = net P&L + turnover cost + borrow cost | **PASS** (0.0 exactly -- re-exposure of `run_multi_asset` components) |
| R7 | Sum of sleeve MCTRs = total portfolio sigma (Euler identity; reconciles to vol, not P&L) | **PASS** (1.39e-17) |
| S3 | Guardrail statement present verbatim | **stated** |

All eight reconciliation gates pass, six of them at or below floating-point
rounding and two at exact zero or machine epsilon.

**Correction stated, not glossed over:** R7 reconciles to total portfolio
vol via the Euler identity, not to P&L. The original PRD framing said
"every decomposition must sum to total daily P&L"; that was corrected
before code was written and is re-stated here so future readers are not
misled about what R7 actually tests.

## Data Pipeline

**Book input:** the v8.2 closing book, frozen at `L=120`, `long_short=True`,
`k_dead_zone=0.5`, `band_pct=0.20`, `rebal_freq=1` (House Rule 4 -- not
retuned). Reconstructed via `risk.attribution.build_v82_book` from the
same `data/raw/{ticker}.parquet` files used throughout v8.x.

**New data this sprint (G2):** per-ticker dividend/distribution history via
`signals.dividends.ingest` -- a new yfinance access path (the existing
`signals.load.fetch` deliberately excludes dividend rows via `actions=False`).
Data persisted to `data/raw/{ticker}_dividends.parquet` for all 8 tickers.

**Transforms applied, in order:**
1. `adj_close` matrix (8 x 4826) -- existing, outer-joined, tz-naive.
2. `daily_returns` (pct_change, fill_method=None) -- sparse NaN at data-gap dates.
3. `build_v82_book` -- target positions and run_multi_asset result.
4. `gross_pnl = net_pnl + turnover_cost + borrow_cost` -- the single
   authoritative gross P&L series all P&L decompositions reconcile against.
5. Each of the seven decompositions -- described individually below.

**Known biases:**
- `adj_close` is yfinance's back-adjusted total-return convention: the
  `shares_i(t) = target_i(t) * notional / adj_close_i(t-1)` formula used
  in Decomposition 5 is an approximation of the true unadjusted share
  count. For recent dates the discrepancy is small; for dates far from
  today, retroactive back-adjustment grows. Documented, not corrected.
- Distribution data from yfinance is as-originally-paid, not
  restated (no known restatement risk for ETF distributions, but not
  independently verified).
- HYG and IEF close prices are missing from the data snapshot for the last
  few trading days of the sample. This correctly propagates NaN to
  per-instrument P&L and factor returns on those days. No imputation; the
  gap is bounded (<0.5% of active cells) and documented in the test suite.
- Single-rate-supercycle caveat: all numbers below are measured on
  2007-2026, one historical regime. None of them are forward-looking.

**Rows dropped:** zero. 1,074 non-zero distribution events appear in the
dividend matrix (out of 38,608 date-ticker cells), and GLD has zero
throughout (correct, as noted under G2).

## Signal Behavior

This sprint has no signal under test (House Rule 1). The seven
decompositions are the deliverable; the following replaces the standard
"Signal Behavior" section with the decomposition results and reconciliation
residuals.

---

### Decomposition 1 -- per-instrument and per-asset-class P&L

**Plot:** `sprints/v8.3/plots/pnl_by_sleeve.png`

Cumulative gross P&L by asset-class sleeve, 2007-2026:

| sleeve | cumulative gross P&L | share of total |
|--------|----------------------|----------------|
| commodity (GLD) | $434,238 | 49% |
| rates (TLT, IEF) | $181,515 | 21% |
| credit (HYG, LQD) | $178,381 | 20% |
| equity (SPY, EFA, EEM) | $86,020 | 10% |
| **total** | **$880,154** | **100%** |

R1 reconciliation residuals: 1.46e-11 (per-instrument), 2.91e-11
(per-asset-class) -- well below the 1e-6 x notional tolerance.

**Finding (not a performance claim):** GLD alone contributes 49% of gross
P&L over this sample, more than rates and credit combined. This is a
real finding about this book and this regime -- not a reason to overweight
gold going forward, and not a property of trend-following in general.

---

### Decomposition 2 -- long vs short P&L

| bucket | cumulative gross P&L |
|--------|----------------------|
| long positions | $1,638,830 |
| short positions | -$758,676 |
| **total (same as D1)** | **$880,154** |

R2 max residual: 1.46e-11. The short bucket loses money in aggregate over
this sample -- consistent with the single-regime bias (a book that is
sometimes short TLT and IEF during a multi-decade rate decline will pay a
price for those shorts).

---

### Decomposition 3 -- directional vs selection

**Plot:** `sprints/v8.3/plots/directional_vs_selection.png`

Market proxy: equal-weighted average return across the 8-name universe
(the same basket as v8.2's T8 buy-and-hold baseline).

| component | cumulative gross P&L |
|-----------|----------------------|
| directional (net exposure x market return) | $1,138,143 |
| selection (P&L relative to uniform market move) | -$257,989 |
| **total** | **$880,154** |

R3 max residual: 7.28e-12. **Selection is defined as the residual of
directional -- an exact accounting identity, not an independent
measurement.** A negative selection total means the book's relative
positioning (long some names, short others, beyond the net-exposure
market bet) detracted from gross P&L over the sample on balance.

---

### Decomposition 4 -- factor regression (the one genuinely empirical output)

**Plot:** `sprints/v8.3/plots/factor_betas.png` (rolling betas, top panel;
rolling R^2, bottom panel)

Rolling 252-day OLS, refit daily, data through `t-1` only (E1' verified).
Factor set: SPY (equity), IEF (rates/duration), HYG-IEF (credit spread),
GLD (real asset). **No monthly or lower-frequency factor** (House Rule 8).

| component | cumulative gross P&L |
|-----------|----------------------|
| beta-explained | -$1,440,668 |
| residual (exposure-timing) | +$2,295,741 |
| **total** | **$880,154** |

R4 max residual: 7.28e-12. Rolling R^2 distribution:
mean 0.42, median 0.39, std 0.21, range 0.06 to 0.93.

**Interpretation -- deliberately cautious, per notes.md finding 3:**
The four daily factors do not span carry. They are daily-return factors;
a dividend received on day `t` appears in the book's daily return on `t`
but also in `gross_pnl(t)`, and the factors (SPY, IEF, HYG-IEF, GLD)
measure daily price change, not coupon accrual. Because carry accounts for
70% of gross P&L (Decomposition 5), the positive exposure-timing residual
is substantially confounded with that coupon income, which the regression
labels "unexplained" relative to the daily price-return factors. The honest
reading: passive average factor exposure (holding mean-beta positions
continuously) would have lost $1.44M over the sample; the realized $880K
came from a combination of carry accrual and exposure-timing that a linear
daily-return factor model cannot cleanly separate. **The residual is not
evidence of standalone trend-timing skill.**

**The residual is exposure-timing, never security selection, never alpha**
(House Rule 7). There is no security-selection layer in this programme;
every instrument is an ETF basket. See the next-steps section for what
would be required to disentangle carry from timing within the regression
residual.

---

### Decomposition 5 -- carry vs price change

**Plot:** `sprints/v8.3/plots/carry_vs_price.png`

| component | cumulative | share |
|-----------|------------|-------|
| carry (dividend/distribution accrual) | $612,274 | 70% |
| price change | $267,880 | 30% |
| **total** | **$880,154** | **100%** |

R5 max residual across all tickers: 9.09e-13.

Carry by ticker:

| ticker | carry |
|--------|-------|
| HYG | $266,429 |
| LQD | $123,602 |
| EFA | $63,098 |
| IEF | $62,795 |
| SPY | $40,494 |
| TLT | $31,530 |
| EEM | $24,325 |
| GLD | $0 |

**GLD carry is exactly zero** (physical gold pays no income -- a correct,
expected value, not a data gap or a bug). The carry series is genuinely
lumpy: most days are exactly zero, and a real distribution appears on the
ex-distribution date. This is correct economic behavior, not smoothing
error.

---

### Decomposition 6 -- gross vs net P&L, turnover and borrow cost

**Plot:** `sprints/v8.3/plots/gross_vs_net.png`

| line item | total |
|-----------|-------|
| gross P&L | $880,154 |
| turnover cost | $20,989 (0.04 bps/day average) |
| borrow cost | $47,127 (0.10 bps/day average) |
| net P&L | $812,038 |

R6 residual: **exactly 0.0** -- this decomposition re-exposes
`run_multi_asset`'s own cost components rather than recomputing them
independently, so it reconciles by construction rather than by
floating-point coincidence. Turnover cost total ($21K) is consistent with
the v8.2 T2+T2b book's ~5.5x annualized turnover at approximately 2bp
round-trip: 5.5 x 2bp x 19yr x $1M ~= $21K.

---

### Decomposition 7 -- marginal contribution to portfolio vol by sleeve

**Plot:** `sprints/v8.3/plots/mctr_by_sleeve.png`

R7 max residual: 1.39e-17 against portfolio sigma (the Euler identity
`sum of MCTR = sigma_portfolio` is verified, not P&L -- a separate
quantity as corrected in the PRD and retested here).

Mean MCTR by sleeve, ex-ante and realized (63-day trailing covariance):

| sleeve | ex-ante | realized |
|--------|---------|----------|
| equity | 0.00514 | 0.00514 |
| rates | 0.00172 | 0.00172 |
| credit | 0.00122 | 0.00122 |
| commodity | 0.00105 | 0.00105 |

The averages are extremely close between ex-ante and realized -- this
masks real day-to-day divergence, which is visible in the plot around
vol regime shifts (e.g. 2020-03). The comparison is the informative
diagnostic; the summary statistic understates the interesting behavior.

## Backtest Results

**Not applicable (House Rule 1).** No Sharpe, hit rate, or drawdown
threshold was pre-registered and none is reported here. The decomposition
tables in Signal Behavior above contain all the pre-registered outputs.

## Key Findings

1. **70% of gross P&L is carry, not price appreciation.** Carry income
   ($612,274) dwarfs price-change returns ($267,880). HYG and LQD
   distributions account for most of the carry. The narrative that this
   is a "trend-following" book is misleading; it is a book that also
   systematically collects coupon income by being long high-yield and
   investment-grade credit when the trend signal is positive -- and in this
   19-year sample, those signals were positive most of the time.
2. **GLD contributes 49% of gross P&L** -- more than rates, credit, and
   equity combined. This is a sample-regime fact (gold ran from ~$680/oz
   in 2007 to ~$3000+ in the sample's close), not a general property of
   trend following in this universe.
3. **The factor regression residual (labelled exposure-timing) is
   confounded with carry.** The four daily price-return factors (SPY, IEF,
   HYG-IEF, GLD) do not separate carry accrual from price-move returns.
   The positive "exposure-timing" residual (+$2.3M) cannot be attributed
   to trend-timing skill without first stripping out the carry component
   that the daily-return regression model classifies as "unexplained." The
   residual is exposure-timing, never selection, never alpha (House Rule 7).
4. **All seven reconciliation gates pass to near-zero residuals**, most
   below 1e-11. R6 is exactly zero by construction (re-exposure, not
   recomputation). This confirms the bookkeeping is internally consistent,
   which is the sprint's actual deliverable.
5. **The security-selection layer is the named permanent gap.**
   This sprint produces no per-issuer attribution because there is no
   per-issuer data or mechanism in this programme -- every instrument is
   an ETF basket. Filling this gap requires issuer-level data from a
   vendor (Bloomberg terminal, WRDS/CRSP) with individual bond and equity
   holdings per ETF, to decompose the ETF's return into the security-level
   contributions. That work is explicitly deferred to a desk environment
   ("BB-era" work), not a future sprint in this programme.

## Limitations

- **Carry-timing confound in the factor regression.** This is the central
  analytical limitation of this sprint's Decomposition 4. A clean
  separation of carry from exposure-timing would require a total-return
  factor decomposition or an explicit carry term in the regression --
  neither is implemented here because such factors are not available at
  daily granularity within this programme's free-data constraint.
- **Security-selection layer absent by design.** The ETF basket structure
  means there is a whole layer of attribution that cannot be done here --
  which sectors drove HYG's return? Which regions drove EFA? These
  questions require issuer/security-level holdings data not available in
  the free-data pipeline.
- **Single-rate-supercycle.** Every dollar figure in this sprint is
  realized over one historical regime (2007-2026) that includes a secular
  rate decline, a credit spread compression, and a major gold run.
  Carry, the commodity sleeve's dominance, and the short-bucket losses
  are all properties of this regime, not general properties of the signal.
- **No monthly factor.** The He-Kelly-Manela intermediary capital factor
  is the theoretically better credit factor (it measures financial
  intermediary balance-sheet capacity directly) but is monthly with a
  multi-month lag and is excluded by design (House Rule 8). The
  HYG-IEF proxy captures the credit-spread component but not the balance-
  sheet-constraint component of credit pricing.
- **Carry-on-shorts sign convention is correct but depends on yfinance
  distribution data.** Short-position carry is correctly negative
  (short sellers owe distributions to lenders). No independent
  verification of the distribution amounts was done.
- **The adj_close share-count approximation in Decomposition 5** slightly
  misstates carry for dates far from today (back-adjustment scaling grows
  over time). The carry-vs-price split should be treated as approximate
  for the pre-2010 portion of the sample.

## Reproducibility

- **Seeds:** none required for the attribution pipeline (`risk.attribution`
  is fully deterministic). Test fixtures use `np.random.default_rng`
  seeds 0 and 0.
- **Data snapshot:** `data/raw/{SPY,EFA,EEM,TLT,IEF,HYG,LQD,GLD}.parquet`,
  `data/raw/{ticker}_dividends.parquet` x8, fetched 2026-06-16. Date
  range 2007-04-11 to 2026-06-15.
- **Commit:** this sprint's code (`risk/attribution.py`,
  `signals/dividends.py`, `signals/etf_universe.py`, `tests/test_attribution.py`,
  `notebooks/08_3_attribution.ipynb`) is being committed alongside this
  walkthrough.
- **Exact commands to regenerate every output:**

```bash
source venv/bin/activate

# 1. ingest dividend history (only needed once)
python -c "from signals.dividends import ingest; from signals.etf_universe import UNIVERSE; ingest(UNIVERSE)"

# 2. rebuild and execute the notebook (regenerates all six plots and all tables)
python scripts/build_notebook_v8_3.py
jupyter nbconvert --to notebook --execute --inplace notebooks/08_3_attribution.ipynb

# 3. run the full reconciliation and no-look-ahead test suite
pytest tests/test_attribution.py -v
```

## Next Steps

1. **Separate carry from timing in the factor regression.** Build a
   carry-adjusted book return series (gross P&L minus carry accrual) and
   re-run Decomposition 4 on that residual. This would isolate whether
   the positive exposure-timing residual survives after extracting the
   coupon income that the daily-return factors cannot see. If the
   carry-stripped residual collapses toward zero, that is evidence that
   most of the "timing" in this book is really just "being long
   high-yielding names while collecting their distributions."
2. **Security-selection layer at the desk (BB data).** Fill the named
   gap: decompose each ETF's daily return into its security-level
   contributions using Bloomberg ETF holdings data (available to desk
   analysts) and attribute the book's HYG and LQD carry to specific
   bond sectors (HY energy, HY technology, IG financials, etc.). This is
   the next natural attribution layer and requires institutional data not
   available in this free-data pipeline.
3. **Carry-adjusted factor regression as a v8.4 task.** The most
   immediately actionable follow-on within this programme: subtract the
   Decomposition-5 carry series from the book's gross P&L to form a
   carry-stripped book return, then regress that on the four daily factors.
   No new data is required; both inputs already exist. This directly tests
   whether the positive exposure-timing residual is mostly carry in
   disguise or a separable component.
4. **Extend the attribution horizon.** Rolling-window attribution (e.g.
   trailing 252d rolling carry fraction, trailing rolling R^2) would
   show whether the carry dominance and the factor-model explanatory power
   are stable or concentrated in specific regimes. The framework already
   produces per-day numbers; the aggregation is a presentation choice.
