# Sprint v8.3 -- Forensic Attribution Engine (Programme v8, Sprint 3, centerpiece)

## Context: What This Sprint Is and Is Not

v8.1 built the instrument (signal + position construction). v8.2 made it
symmetric, built the first cost-aware P&L accumulator, and brought turnover
under control (T2+T2b: `band_pct=0.20`, `k_dead_zone=0.5`, ~5.5x annualized
turnover, down from ~32x). **v8.3 does not change the book.** It builds a
forensic accounting layer on top of the exact book v8.2 closed with, and
asks: when this book makes or loses a dollar, where did that dollar
actually come from? Per-name? Long bucket or short? Net market exposure or
relative positioning? A known macro factor or something unexplained?
Coupon/dividend accrual or price change? Gross trading cost? Which sleeve's
risk dominates the realized volatility?

**This is not a research sprint and there is no economic hypothesis under
test** (House Rule 1, carried unchanged from v8.1/v8.2). The "falsification
criteria" below are bookkeeping-correctness gates: does every decomposition
reconcile to the quantity it claims to decompose, within a tight tolerance,
using only data available at the time. Where a "reconciliation" is an
accounting identity by construction (most of them are -- the residual is
*defined* as whatever is left over), that is stated plainly, not dressed up
as an empirical test. The one decomposition whose substance is genuinely
empirical, not tautological, is the factor regression's R² and beta
stability -- and even there, the "reconciliation" (residual = total minus
explained) is still definitional.

## v8 House Rules (carried, restated for this sprint's character)

1. **No edge claim.** Nothing in this sprint's output is a performance
   claim or a search for one. Attribution explains what already happened
   in the v8.2 book; it does not suggest changing it.
2. **No look-ahead -- explicit for this sprint:** the attribution for day
   `t` uses only positions known at the start of `t` (the `target` weight
   vector already shifted by `signals.trend_signal.shift_to_next_day`) and
   realized returns/factor values over `t` itself. Any rolling estimate
   used to *explain* day `t` (e.g. factor betas) must be fit using data
   only through `t-1` -- explicit, not assumed, and tested the same way
   E1 was tested in v8.1/v8.2 (perturb data after a cutoff, confirm the
   prefix is unchanged).
3. **Risk budgeting, not signal optimization** -- not directly applicable
   to an accounting layer, but its spirit carries over as: no decomposition
   parameter (regression window, vol window, sleeve grouping) is chosen or
   adjusted to make any number look better. Every parameter here is
   pre-registered below, before any output is computed.
4. **Universe and book fixed from v8.2.** This sprint does not add,
   remove, or reweight tickers, and does not change `L`, `v`, `w_max`,
   `g_max`, `band_pct`, or `k_dead_zone`. The book being attributed is
   exactly v8.2's closing book.
5. **Mechanical and reproducible.**
6. **Single-regime caveat carries over** wherever a dollar figure appears:
   2007–2026 is one historical regime. Attribution explains *this* book's
   *this* P&L in *this* sample; it is not a claim about what will attribute
   well going forward.
7. **The security-selection layer is permanently and deliberately
   absent -- named as a gap, not a future task.** Every instrument in this
   book is an ETF: a basket. There is no mechanism anywhere in this
   programme that picks individual bonds within HYG/LQD or individual
   equities within SPY/EFA/EEM. The factor regression's residual (§
   Decomposition 4) must be labelled **exposure-timing**, never security
   selection -- there is no security-selection layer to attribute to.
8. **No monthly or lower-frequency factor in any daily context -- a
   standing constraint, not just a decision for this sprint.** This book
   trades and is marked daily; any factor entering a daily regression,
   daily attribution, or daily residual decomposition must itself be
   daily and exposure-matched. The He-Kelly-Manela intermediary capital
   factor (monthly, multi-month publication lag) is the concrete example
   that motivated this rule (see notes.md decision log) and is named here
   so it does not recur: it is never to be interpolated to daily
   frequency to fill the gap, and never to be used at its native monthly
   frequency inside a daily computation. If a later v8.x sprint's prompt,
   inherited text, or residual-handling step references it (or any other
   monthly/lower-frequency factor) in a daily context, treat that as a
   stale carryover and omit it, recording the omission the same way G1
   does below -- not by fabricating daily granularity that does not exist.
   Lower-frequency factors are a desk-data concern, out of scope for this
   programme's daily work.

---

## Economic Hypothesis

None (House Rule 1). The "hypothesis," to the extent the word applies at
all, is purely accounting: that the v8.2 book's daily P&L can be fully and
exactly re-expressed as the sum of each decomposition's components, with
no unexplained leakage beyond floating-point rounding. This is a claim
about the correctness of the bookkeeping, not about markets.

---

## Falsification Criteria

Continuing the `E`/`B`/`S` gate convention from v8.1/v8.2, plus a new `R`
(reconciliation) series specific to this sprint's seven decompositions, and
a `G` entry continuing v7.1's data-gate convention. `G1` is no longer a
data-availability probe (see Change 2 in notes.md): the intermediary
capital factor is omitted from Decomposition 4 by design, regardless of
what frequency might be retrievable, because it is monthly and this
attribution is daily. `G1`'s row below records that decision, not a probe
outcome.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|----------------|------------------|
| G1 | Monthly intermediary-capital factor (He-Kelly-Manela) is frequency-mismatched to this daily attribution | **intentionally omitted by design** -- no daily series is sought, none is fabricated | N/A -- this is a standing decision, not a test; see House Rule 8 and notes.md. Revisit only at the desk, with proper non-daily-frequency tooling, never inside this daily pipeline |
| G2 | Per-ticker dividend/distribution history is retrievable for all 8 ETFs via the existing yfinance boundary | retrieved for all 8, non-negative, sparse (most days zero) as expected for distribution data | document and treat carry as zero for any ticker where this fails, named explicitly, not silently |
| E1′ | No look-ahead in the attribution layer itself: factor betas explaining day `t` use only data through `t-1`; all per-day decompositions use only the position held entering `t` and realized data over `t` | perturbation test (E1-style) passes for every decomposition | bug; fix before any number is reported |
| R1 | Decomposition 1 (per-instrument / per-asset-class) sums to total daily gross P&L | residual ≤ 1e-6 × notional, every day | bug in grouping or summation; fix |
| R2 | Decomposition 2 (long vs short) sums to total daily gross P&L | residual ≤ 1e-6 × notional, every day | bug; fix |
| R3 | Decomposition 3 (directional vs selection) sums to total daily gross P&L | residual ≤ 1e-6 × notional, every day (exact by construction: selection ≡ total − directional) | bug in the directional term itself, since the residual term cannot fail to reconcile by definition |
| R4 | Decomposition 4 (factor-explained vs residual) sums to total daily gross P&L | residual ≤ 1e-6 × notional, every day (exact by construction: residual ≡ total − explained) | bug in the beta-explained term; the identity itself cannot fail |
| R5 | Decomposition 5 (carry vs price change) sums to total daily gross P&L per instrument | residual ≤ 1e-6 × notional, every day | bug in the carry/price split |
| R6 | Decomposition 6 (gross → net via turnover cost) reconciles to net daily P&L exactly | residual ≤ 1e-6 × notional, every day (exact by construction -- this *is* `backtest.multi_asset.run_multi_asset`'s own formula, re-exposed, not recomputed independently) | bug; fix |
| R7 | Decomposition 7 (marginal contribution to vol, by sleeve) sums to total portfolio vol, **not P&L** -- a separate identity (Euler decomposition: `Σ_sleeve MCTR_sleeve = σ_portfolio`), ex-ante and realized separately | residual ≤ 1e-6 × σ_portfolio, both ex-ante and realized | bug in the covariance/weight computation |
| S3 | Guardrail statement, not a test | must appear verbatim in `notes.md`: **"This is a forensic accounting layer on the v8.2 book. It explains realized P&L; it does not predict future P&L, and it does not contain or imply a security-selection capability -- every instrument here is an ETF basket. Reconciliation to machine precision confirms the bookkeeping is correct; it is not evidence the book is good."** | N/A |

**Correction to the user's framing, stated explicitly rather than silently
adjusted:** "every decomposition must sum to the total daily P&L" is exact
for Decompositions 1–6 (all are P&L decompositions). Decomposition 7 is a
**volatility** decomposition (marginal contribution to portfolio vol) -- it
reconciles to total portfolio vol via the standard Euler identity, not to
P&L. Both are pre-registered as "reconciling decompositions"; the reconciled
quantity differs by construction and is stated per-row in the table above,
not glossed over.

---

## Signal Definition (the seven decompositions, precisely)

**Common setup.** Let `target_i(t)` be the v8.2 closing book's held weight
for instrument `i` on day `t` (output of `apply_rebalance_control` with
`band_pct=0.20` composed with `compute_trend(..., k_dead_zone=0.5)`, then
`shift_to_next_day`). Let `ret_i(t) = adj_close_i(t)/adj_close_i(t-1) - 1`
(unchanged from `backtest.multi_asset`). Gross daily P&L is
`gross_pnl(t) = Σ_i target_i(t) · ret_i(t) · notional` (identical to
`run_multi_asset`'s own formula -- this sprint re-exposes it per-component,
it does not recompute it differently).

**(1) Per-instrument and per-asset-class P&L:**
```
pnl_i(t) = target_i(t) · ret_i(t) · notional
```
already sums to `gross_pnl(t)` by construction. Asset-class grouping
(carried from v8.1's universe table, not redefined here):
equity = {SPY, EFA, EEM}, rates = {TLT, IEF}, credit = {HYG, LQD},
commodity = {GLD}.

**(2) Long vs short P&L:**
```
pnl_long(t)  = Σ_{i: target_i(t) > 0} pnl_i(t)
pnl_short(t) = Σ_{i: target_i(t) < 0} pnl_i(t)
```

**(3) Directional vs selection split.** Market proxy, pre-registered:
the equal-weighted average return across the 8-name universe -- the same
basket already built as v8.2's T8 buy-and-hold baseline, reused here
rather than inventing a second "the market" definition for this programme.
```
market_ret(t)    = (1/8) · Σ_i ret_i(t)
directional(t)   = net_exposure(t) · market_ret(t) · notional
selection(t)     = gross_pnl(t) − directional(t)        # exact by construction
```
`net_exposure(t)` is the existing v8.1/v8.2 series (`Σ_i target_i(t)`).
`selection(t)` captures P&L from being long some names and short/underweight
others beyond what a uniform market move would produce -- e.g. long EEM and
short IEF is a *relative* bet even if `net_exposure(t)` happens to be near
zero.

**(4) Factor regression -- the one genuinely empirical, non-tautological
output of this sprint.** Decompositions 1, 2, 3, 5, and 6 are accounting
identities: each "residual" or "selection" term is *defined* as whatever
total minus the other term leaves over, so they reconcile by construction
and prove nothing about markets. Decomposition 4 is different: the betas
are estimated on a trailing window and then applied to data the
regression has not seen, so `R²` and the resulting `residual(t)` are real,
falsifiable-in-spirit numbers (a low `R²` or a residual the same size as
`gross_pnl(t)` itself would be a genuine, informative finding, not a bug).
This is exactly why every factor in it must be **daily and
exposure-matched to the book** (House Rule 8) -- a frequency-mismatched
factor would corrupt the one part of this sprint that is actually
measuring something.

Factor set, all already in the v8.1 universe and already daily: equity beta
via `SPY`, rates/duration beta via `IEF`, credit-spread beta via
`HYG_ret(t) - IEF_ret(t)` (high yield over duration-matched treasuries,
isolating the credit component from the duration component already
captured by the rates term), and a real-asset term via `GLD`. The
He-Kelly-Manela intermediary capital factor is **not included** -- it is
monthly with a multi-month publication lag, and including it would mean
either interpolating a synthetic daily series (refused, same principle as
the v7.1 NAV decision: never fabricate a series to fill a frequency gap)
or collapsing this entire attribution to monthly, which would destroy the
daily granularity that is the point of v8.3 (House Rule 8, G1).

Rolling OLS, refit daily, window `W_FACTOR=252` trading days, **using only
data through `t-1`** (pre-registered, not a grid):
```
book_ret(t) = alpha + beta_eq*SPY_ret(t) + beta_rates*IEF_ret(t)
              + beta_credit*(HYG_ret(t) - IEF_ret(t)) + beta_gold*GLD_ret(t) + eps(t)
```
fit on `{t-252, ..., t-1}`, applied to realize day `t`'s decomposition:
```
beta_explained(t) = [beta_eq*SPY_ret(t) + beta_rates*IEF_ret(t)
                      + beta_credit*(HYG_ret(t)-IEF_ret(t)) + beta_gold*GLD_ret(t)] * notional
residual(t)       = gross_pnl(t) - beta_explained(t)     # exact by construction
```
Reported: rolling betas over time, rolling `R²`, `beta_explained(t)`,
`residual(t)`. **The residual is labelled exposure-timing, never security
selection, never alpha** (House Rule 7) wherever it appears, in every
output -- it captures P&L from *when* the book held its factor exposures
(and from whatever this four-factor set does not span), not from picking
better instruments within a factor bucket, because there is no such
mechanism in this programme.

**(5) Carry vs price change.** Let `div_i(t)` be instrument `i`'s
per-share distribution on day `t` (zero on non-distribution days) and
`shares_i(t) = target_i(t) · notional / adj_close_i(t-1)` the implied
share count. Then:
```
carry_i(t)        = shares_i(t) · div_i(t)
price_change_i(t) = pnl_i(t) − carry_i(t)            # exact by construction
```
Distributions are lumpy (most days zero, a real payment on ex-distribution
dates) -- this is documented as the expected shape of the series, not
treated as a defect.

**(6) Gross vs net, turnover cost in bps.** Already computed inside
`backtest.multi_asset.run_multi_asset` -- this decomposition re-exposes it
per-day rather than recomputing it independently (R6's reconciliation is
therefore exact by re-use, not by coincidence):
```
turnover_cost(t) = (half_spread_bp + slippage_bp)·1e-4·Σ_i|Δtarget_i(t)|·notional
borrow_cost(t)   = borrow_annual/252 · notional · Σ_i max(-target_i(t), 0)
net_pnl(t)       = gross_pnl(t) − turnover_cost(t) − borrow_cost(t)
```

**(7) Marginal contribution to portfolio vol, by sleeve, ex-ante vs
realized.** Sleeves = the same four asset-class buckets as Decomposition 1.
Let `w_s(t)` be the held weight vector restricted to sleeve `s`'s
instruments, embedded in the full 8-vector (zeros elsewhere), and `Σ̂(t)`
a covariance matrix of daily instrument returns. Euler decomposition
(exact identity, not an estimate):
```
sigma_p(t)    = sqrt(w(t)' Σ̂(t) w(t))
MCTR_s(t)     = (w(t)' Σ̂(t) w_s(t)) / sigma_p(t)
Σ_s MCTR_s(t) = sigma_p(t)                            # exact by construction
```
**Ex-ante**: `Σ̂(t)` is the trailing 63-day covariance of instrument
returns over `{t-63, ..., t-1}` (point-in-time, matches the existing
`W=63` vol-targeting window for consistency) -- a forecast available before
day `t`. **Realized**: `Σ̂(t)` is the trailing 63-day covariance over
`{t-62, ..., t}` (includes day `t`) -- measuring what actually happened,
computed with the benefit of hindsight. **This hindsight use is
deliberate and stated explicitly**: Decomposition 7 is a forensic,
after-the-fact risk report, not a trading input -- it does not violate
House Rule 2 / E1′, which governs the P&L attribution and the factor
betas (Decompositions 1–6 and the regression in 4), not this retrospective
risk-decomposition diagnostic.

---

## Data

| source | contents | status |
|--------|----------|--------|
| `data/raw/{SPY,EFA,EEM,TLT,IEF,HYG,LQD,GLD}.parquet` | adj_close, used throughout, including all four Decomposition-4 factors (SPY, IEF, HYG, GLD) | existing |
| Per-ticker dividend/distribution history, all 8 ETFs | `div_i(t)` for Decomposition 5 | **new -- G2, via the existing yfinance boundary, probed in T1** |

The He-Kelly-Manela intermediary capital factor is **not a data dependency
of this sprint** -- it is omitted by design (House Rule 8, G1), not
fetched, not probed, not awaiting a feasibility check. No row for it
appears above on purpose.

**Known biases:**
- `signals.load.fetch` currently calls `actions=False`, deliberately
  excluding dividend/split rows -- G2's ingestion is a genuinely new data
  path (still via yfinance, still the same vendor), not a reuse of an
  existing fetch.
- Distribution data from yfinance reflects what was actually paid
  historically; no restatement risk is expected for this series (dividend
  history is not normally revised), but this is asserted, not separately
  verified, and should be noted as such.

---

## Success Metrics

No Sharpe, IC, or any predictive metric (House Rule 1). Metrics are the
`R1–R7`/`E1′`/`G1–G2`/`S3` gates above, plus these informational reports
(not gated, explicitly labelled diagnostic):
- Rolling β stability and R² over time for Decomposition 4 (does the
  factor model explain a little or a lot of this book's variance, and does
  that change over the sample) -- reported, not thresholded.
- Sleeve MCTR ranking, ex-ante vs realized (does the ex-ante risk forecast
  match what actually dominated realized vol) -- reported, not thresholded.
- Carry as a fraction of total P&L, in aggregate and per sleeve -- reported.

---

## Research Architecture

```
v8.2 closing book (band_pct=0.20, k_dead_zone=0.5)
      |
[T1] Data probe: dividend history (G2) -- intermediary capital factor omitted by design, not probed (G1)
      |  HARD GATE on G2 outcome for Decomposition 5 specifically
[T2] backtest/attribution.py: per-instrument / per-asset-class P&L (1) + long/short split (2)
      |
[T3] Directional vs selection (3) -- reuses v8.2's equal-weight basket as the market proxy
      |
[T4] Factor regression (4) -- rolling 252d OLS, point-in-time betas
      |
[T5] Carry vs price change (5) -- requires G2
      |
[T6] Gross/net/turnover-cost re-exposure (6) -- thin wrapper over backtest.multi_asset
      |
[T7] Marginal contribution to vol by sleeve, ex-ante vs realized (7)
      |
[T8] Reconciliation test suite: R1-R7, E1' -- the sprint's actual deliverable-correctness gate
      |
[T9] Sanity baseline: hand-verify 2-3 known dates across all seven decompositions
      |
[T10] Notebook + sprint close
```

**Reused, not rebuilt:** `signals.trend_signal` (the v8.2 book),
`backtest.multi_asset` (gross P&L and cost formulas -- Decomposition 6 is a
thin re-exposure, not an independent recomputation, by design, so R6 is
exact by construction rather than by luck), `execution.costs` (v6.5
constants, unchanged).

**New this sprint:** `backtest/attribution.py` (Decompositions 1-7),
`signals/dividends.py` or an extension of `signals/load.py` (G2 ingestion).
No probe script for the intermediary capital factor exists or is needed --
it is omitted by design, not by data unavailability (House Rule 8, G1).

---

## Risks and Biases

- **Frequency mismatch is the reason the intermediary capital factor is
  excluded from Decomposition 4, not a risk this sprint carries** -- by
  fixing the factor set to daily, exposure-matched series only (House
  Rule 8), the risk of silently fabricating or smuggling in a
  lower-frequency series is designed out rather than monitored for.
- **Security-selection-shaped misreading of the residual** is the single
  biggest interpretive risk of this entire sprint. House Rule 7 exists
  specifically to forbid describing Decomposition 4's residual as "alpha"
  or "selection skill" anywhere in any output -- there is no
  security-selection mechanism in this programme, full stop.
- **Distribution lumpiness** (Decomposition 5) means carry is exactly zero
  on the vast majority of days and a real, sometimes sizeable, jump on
  ex-distribution dates -- a naive reader could mistake this for noise or a
  bug; it is documented explicitly as expected shape.
- **Ex-ante vs realized vol contribution (7) will disagree**, often
  substantially, especially around vol regime shifts -- this is the
  expected and informative behavior of the comparison, not a sign that one
  of the two calculations is wrong.
- **Single-regime caveat, restated**: every dollar and every vol number in
  this sprint's output is attributable *within* the 2007–2026 sample this
  book was built and tuned against. None of it is a forward-looking claim.

---

## Out of Scope

- Changing the book in any way (`L`, `v`, `w_max`, `g_max`, `band_pct`,
  `k_dead_zone` are all frozen at their v8.2 closing values)
- Any security-selection mechanism or analysis (House Rule 7 -- there is
  nothing to build here; ETFs are baskets)
- Extending the factor set beyond equity/rates/credit/gold (no momentum,
  value, or other style factors this sprint, and no monthly or
  lower-frequency factor of any kind -- House Rule 8)
- Any monthly-frequency analysis, interpolation, or proxy construction for
  the intermediary capital factor or any other lower-frequency series
- Any performance claim, IC test, or Sharpe threshold (House Rule 1)
- Real-time or production attribution reporting -- this is a historical,
  one-shot forensic run over the existing sample

---

## Dependencies

- `signals/trend_signal.py`, `signals/etf_universe.py` -- v8.1/v8.2, unchanged
- `backtest/multi_asset.py`, `execution/costs.py` -- v8.2, reused as-is for
  Decomposition 6's gross/net/cost formulas
- New: per-ticker dividend/distribution history via yfinance (T1/G2)
- No dependency on the He-Kelly-Manela intermediary capital factor, or any
  external data source for it -- omitted by design (House Rule 8, G1)
