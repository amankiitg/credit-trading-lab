# Sprint v8.2 — Add Shorts and Set Parameters: Walkthrough

**This sprint sets operational parameters. It does not claim predictive
validity.** Every number below is measured over 2007-2026, a single
historical regime dominated by one secular rate-decline supercycle (~5% to
~0%, partially reversed from 2022) that mechanically flatters trend-following
exposure to rate-sensitive names (TLT, IEF). No parameter in this sprint was
chosen to maximize a backtest metric.

## Summary

v8.2 made the v8.1 trend signal symmetric (`signal_i(t) = sign(trail_ret_i(t))`,
long or short, not long-only/flat), built the first cost-aware daily P&L
accumulator for this book, and used it to set two genuinely new operational
parameters: a no-trade band (turnover control) and a buy-and-hold baseline
to calibrate against. There is no hypothesis under test and therefore no
confirmed/rejected/inconclusive verdict in the usual sense — the engineering
gates (`E1–E7`, `B1–B2`) all pass. The magnitude-based no-trade band (T2)
**did not meet** its own success criterion (turnover to single-digit/
low-double-digit annualized); a follow-on mechanism, **T2b** (signal-level
hysteresis, a dead zone on the sign decision), was added within this same
sprint to address the diagnosed cause directly and **did** meet the
criterion (32.32x → 5.48x, an 83.0% cut), at an honestly-reported net-return
cost. This walkthrough extends the original T2-only finding rather than
overwriting it — see the **Addendum: T2b** section below for the full
three-stage result.

## Hypothesis & Falsification Criteria

No economic hypothesis is under test (PRD House Rule 1, carried from v8.1).
The criteria below are engineering-correctness gates plus one genuine
operational success criterion (turnover):

| ID | Criterion | Status |
|----|-----------|--------|
| E1 | No look-ahead (signal, position, rebalance-control hold logic) | **PASS** |
| E2 | Vol-target formula identity, signed: `raw_weight·sigma == signal·min(v, w_max·sigma)` | **PASS** (explicitly verified with `signal=-1` rows present) |
| E3 | Gross leverage cap (`Σ\|weight\| <= 2.0`), with negative weights | **PASS** |
| E4 | Reproducibility | **PASS** |
| E5 | Point-in-time universe membership (`max(L,W)=120` day minimum, v8.1 correction carried) | **PASS** |
| E6 | Net/gross exposure relationship (`\|net\| <= gross` always; net diverges from gross once shorts are on) | **PASS** |
| B1 | Daily P&L reconciles exactly against an independent reference formula | **PASS** |
| B2 | Cost model matches the v6.5 constants exactly (`half_spread_bp=1.5, slippage_bp=0.5, borrow_annual=0.004`) | **PASS** |
| S2 | Guardrail statement (verbatim, below) | **stated** |
| E7 | No look-ahead, vol-target identity, and gross cap re-verified with hysteresis (`k_dead_zone>0`) active | **PASS** (see Addendum: T2b) |
| T2 success criterion (band alone) | No-trade band cuts turnover to single-digit/low-double-digit annualized | **NOT MET** (32.32x → 31.24x, a 3.3% cut) — diagnosed, not hidden |
| T2b success criterion (band + hysteresis) | Same target, addressing the diagnosed sign-flip cause directly | **MET** (32.32x → 5.48x, an 83.0% cut) — see Addendum: T2b |

## Data Pipeline

Unchanged from v8.1: 8-name ETF universe (SPY, EFA, EEM, TLT, IEF, HYG, LQD,
GLD), `signals.etf_universe.load_universe_close`, 2007-04-11 to 2026-06-15,
4826 rows, no new ingestion this sprint.

**Transforms, in order (new steps in bold):**
1. `adj_close` → `log_ret`, `trail_ret[t] = adj_close[t]/adj_close[t-L] - 1`, L=120 (unchanged)
2. **`signal[t] = sign(trail_ret[t])` ∈ {-1, 0, +1}** (was `1 if >0 else 0` in v8.1)
3. `sigma[t]` (63d realized vol), `raw_weight = signal · min(v/sigma, w_max)`, gross-leverage cap (unchanged formula, now sign-aware)
4. **`net_exposure[t] = Σ weight_i(t)` (signed), `gross_exposure[t] = Σ |weight_i(t)|`** — new first-class output series
5. **`apply_rebalance_control`**: state-based, proportional no-trade band (`band_pct=0.20`) — only trade a name when the gap between held and desired exceeds 20% of that day's desired weight; re-caps gross exposure after the hold logic, since the joint cap no longer holds automatically once names can go stale independently
6. **`run_multi_asset`** (new `backtest/multi_asset.py`): daily P&L, reusing the exact v6.5 cost constants via a turnover-based daily cost, since `backtest/engine.py`'s single-pair/discrete-position API does not fit this 8-name continuously-weighted book (a pre-registered architecture decision, not a mid-sprint surprise)

**Known biases:** unchanged from v8.1 (`adj_close` is yfinance's
back-adjusted total-return convention; no survivorship bias; all 8 tickers
share the repo-wide 2007-04-11 start, so there is no true staggered
inception to test point-in-time logic against — same finding as v8.1).

**Rows dropped:** none. 42 cells in the outer-joined close matrix carry a
temporary one-day gap (isolated missing prints, not new this sprint); the
rebalance control carries the previously-held weight forward through these
rather than dropping the name to zero for that one day — a deliberate
choice, confirmed by a dedicated test, and the reason the band, even at
`band_pct=0` / `rebal_freq=1`, is not a bit-for-bit no-op against the bare
v8.1 output on real data (only on gap-free synthetic data).

## Signal Behavior

No IC, rank-IC, t-stat, or decay profile — out of scope by design (House
Rule 1; this is not a predictive signal under test). What changed and what
is reported instead:

**Long/short vs long-only activity**, full 2007-2026 sample: every ticker
has both `signal=+1` and `signal=-1` rows under the v8.2 default (verified
directly, not assumed) — the v8.1 book never had a single short.

**Net vs. gross exposure** (`sprints/v8.2/plots/long_only_vs_long_short.png`,
bottom panel; `sprints/v8.2/plots/no_trade_band_exposure.png`): in the v8.1
long-only book, net exposure equals gross exposure on every single day (no
shorts exist to create a difference) — confirmed exactly by test. In the
v8.2 long/short book, net visibly and persistently diverges from gross,
swinging with how much the 8 names agree on trend direction. This is exactly
the behavior `E6` exists to verify, not assume: `|net(t)| <= gross(t)` holds
on every date, and gross stays within the `g_max=2.0` cap throughout,
including through the rebalance-control re-cap step.

**Turnover decomposition** (the sprint's most informative "signal behavior"
finding, despite not being a predictive-signal statistic): of all turnover
in the long/short book, **96.0% comes from outright sign flips** (1,183
reversal events over the sample, averaging a full `|Δweight|≈0.50` swing
each, since `w_max=0.50`) and only **3.9% from same-sign magnitude wobble**
(36,287 continuing-same-sign days, averaging `|Δweight|≈0.0007`). This is
why a magnitude-based no-trade band — correctly implemented, proportional,
state-based — could not meaningfully cut turnover: it targets exactly the
4% slice, not the 96% slice.

## Backtest Results

No Sharpe, hit rate, or capacity estimate — explicitly out of scope (House
Rule 1). The numbers below are operational-comparison and parameter-setting
diagnostics, not a performance claim, every one of them measured on the
single 2007-2026 rate-supercycle sample:

**Long-only (v8.1 rule) vs long/short (v8.2 rule)**, `L=120`, full sample:

| rule | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|------|--------------------|------------|--------------|-------------------|
| long-only (v8.1) | 13.26% | 15.35% | -24.48% | 34.78x |
| long/short (v8.2), pre-band | 7.95% | 17.67% | -34.46% | 32.37x |

Long-only outperforms long/short here. The most likely reason is exactly
the single-rate-supercycle risk this PRD calls out by name: long-only rode
a multi-decade TLT/IEF rate decline with no offsetting short legs;
long/short periodically shorts those same names during phases of that one
secular move, which costs money in this specific sample without that being
evidence it would cost money in a sample containing more than one rate
regime. **This is not evidence long-only is the better rule going
forward.**

**Light lookback robustness check** (not Sharpe-maximizing), long/short
rule:

| L | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|---|--------------------|------------|--------------|-------------------|
| 90 | 6.28% | 17.55% | -33.68% | 36.03x |
| 120 | 7.95% | 17.67% | -34.46% | 32.37x |
| 150 | 4.18% | 17.23% | -48.53% | 31.01x |

Net return sign is consistent (positive) across all three — the result does
not collapse as `L` moves. **`L=120` stays the default even though it is
not the best number in this table** (`L=90` is close; `L=150` is worse on
every metric) — no parameter here was chosen to maximize anything.

**No-trade band: before vs. after** (`band_pct=0` vs `band_pct=0.20`,
`rebal_freq=1` both ways), long/short rule, `L=120`:

| | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|---|--------------------|------------|--------------|-------------------|
| before (band off) | 7.95% | 17.63% | -34.46% | 32.32x |
| after (`band_pct=0.20`) | 7.79% | 17.64% | -34.34% | 31.24x |

Turnover fell 3.3% — **the pre-registered success criterion (single-digit/
low-double-digit annualized) was not met.** See Signal Behavior above for
why: 96% of turnover is sign-flip-driven, and a magnitude band cannot
suppress a flip without ignoring a real, decisive direction change in the
signal — which would defeat the point of the signal. An earlier pass through
this sprint tried a discrete daily-vs-weekly recompute schedule instead
(`rebal_freq=5`) and found a real 52-54% turnover cut; that mechanism is
superseded here in favor of the more principled, state-based proportional
band, even though the band achieves a smaller cut — a discrete schedule
delays flip-driven and wobble-driven trades indiscriminately, which is a
blunter instrument, not a more honest one.

**Buy-and-hold equal-weight baseline** (1/8 per name, no vol targeting,
structurally zero turnover):

| rule | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|------|--------------------|------------|--------------|-------------------|
| long-only (v8.1) | 13.26% | 15.35% | -24.48% | 34.78x |
| long/short (chosen band) | 7.79% | 17.64% | -34.34% | 31.24x |
| buy-and-hold equal-weight (1/8) | 7.04% | 10.08% | -32.05% | 0.00x |

Buy-and-hold's net return (7.04%) sits close to the long/short trend book's
(7.79%), at materially lower volatility (10.08% vs 17.64%) and a similar
drawdown. **The trend book is not obviously earning its extra risk and
turnover over a passive basket in this single-regime sample.** No claim of
superiority either way — this is the calibrating comparison T8 exists to
provide.

**Subperiod / parameter-sensitivity note:** no formal subperiod split or
parameter-sensitivity table beyond the lookback robustness check above is
in scope (House Rule 1 — there is no performance claim to stress-test
across regimes). The single-rate-supercycle caveat already covers the
dominant subperiod concern: any split of 2007-2026 still lives entirely
inside the same secular rate regime.

## Key Findings

1. **The symmetric signal and its vol-target/leverage-cap machinery are
   correctly built**, including for the negative case specifically (not
   just assumed by analogy to the long-only path) — `E2`, `E3`, `E6` are
   all verified directly against real short positions, not just real long
   ones.
2. **96% of this signal's turnover comes from sign flips, not vol-driven
   weight drift.** This was not assumed; it was measured. Any future
   turnover-reduction mechanism for this signal needs to address flip
   frequency directly (e.g., a confirmation delay or hysteresis on the
   sign itself), not position-size wobble.
3. **A correctly-designed, correctly-implemented cost-control mechanism can
   still miss its stated goal** — and the right response is to diagnose and
   report that, not to quietly redefine success. The no-trade band did
   exactly what it was built to do; it just was not built to address the
   actual dominant cost driver.
4. **Long-only outperforms long/short on every reported metric in this
   sample**, and the likely explanation (one-directional TLT/IEF rate
   exposure during a one-directional rate regime) is a property of the
   sample, not evidence about which rule is better going forward.
5. **A passive buy-and-hold basket is competitive with the actively-managed
   trend book on a return basis, at much lower volatility**, in this
   sample — a useful, humbling calibration point for any future sprint
   that might be tempted to treat the trend book's numbers as obviously
   good.

## Limitations

- **No predictive validation of any kind**, by design (House Rule 1) — this
  walkthrough cannot and does not say whether the symmetric trend rule
  "works," only that it is built correctly and how it behaves
  operationally.
- **Single rate supercycle.** Every return/vol/drawdown number above is
  measured on one historical regime; none of them are forward-looking
  claims, and no out-of-sample holdout exists because none was needed for
  an operational sanity check.
- **The no-trade band's failure to hit its turnover target is a property
  of this specific signal's flip frequency**, not a general statement
  about no-trade bands — a slower-turning signal might see a very
  different result from the same mechanism.
- **No transaction-cost-impact or capacity analysis** beyond the v6.5 bp
  constants — market impact, financing beyond the flat borrow rate, and
  capacity at scale are all unmodeled.
- **Multiple-testing context carried forward**: this is the third
  instrument/programme after the v1-v6.6 HY/IG line and the v7.1 NAV wedge
  probe. No statistical claim is made here, so no Sharpe-inflation risk
  applies yet, but the count should inform any future sprint that does
  make one.

## Reproducibility

- **Seeds:** none required for the production pipeline (`compute_trend`,
  `apply_rebalance_control`, `run_multi_asset` are deterministic functions
  of their inputs). Test-only synthetic fixtures use fixed
  `np.random.default_rng` seeds (0, 1, 2).
- **Data snapshot:** same as v8.1 — `data/raw/{SPY,EFA,EEM,TLT,IEF,HYG,LQD,GLD}.parquet`,
  2007-04-11 to 2026-06-15, no new ingestion this sprint.
- **Commit:** this sprint's code (`signals/trend_signal.py` extensions,
  `backtest/multi_asset.py`, `tests/test_trend_signal.py`,
  `tests/test_multi_asset_backtest.py`, `notebooks/08_2_add_shorts.ipynb`)
  was committed as `ab9815b` immediately prior to this walkthrough.
- **Exact commands to regenerate every output:**

```bash
source venv/bin/activate

# 1. rebuild and execute the notebook (regenerates both plots and every table)
python scripts/build_notebook_v8_2.py
jupyter nbconvert --to notebook --execute --inplace notebooks/08_2_add_shorts.ipynb

# 2. run the full engineering-correctness suite (E1-E6, B1-B2, band-specific tests)
pytest tests/test_trend_signal.py tests/test_multi_asset_backtest.py -v
```

## Next Steps

1. ~~**If single-digit turnover is operationally required**, the next
   mechanism to try is one that targets sign-flip frequency directly...~~
   **Done within this sprint — see Addendum: T2b below.** Resolved: 5.48x
   annualized, inside the target range, on the first parameter choice.
2. **v8.3 cost-attribution work should build on the T2+T2b book**, not the
   pre-T2 daily-naive profile and not the T2-only profile either — the
   turnover that exists now (`band_pct=0.20`, `k_dead_zone=0.5`, ~5.5x
   annualized) is the one any attribution panel should decompose, per the
   sequencing agreed before this sprint started (and restated when T2b was
   added).
3. **If a performance claim is ever wanted** for either the long-only or
   long/short rule, that is a new, separately pre-registered sprint with
   its own IC test and Sharpe threshold — explicitly not a retroactive
   addition here (House Rule 1). The T2b addendum below makes this more
   pointed: net return fell further once turnover was brought under
   control (4.24% vs. T2-only's 7.79%), so any future performance claim
   needs to be evaluated on the T2+T2b book specifically, not on these
   earlier-stage numbers.
4. **The buy-and-hold baseline's competitiveness** (Key Finding 5) is worth
   carrying into any future performance-claim sprint as the bar to clear,
   not the trend book's own raw numbers — more so now that the T2+T2b book
   underperforms buy-and-hold on return (see Addendum: T2b).
5. **If genuine-reversal responsiveness matters more than turnover** in some
   future use of this instrument, the ~9-trading-day average lag T2b
   introduces (Addendum: T2b) is the concrete number to weigh against the
   83% turnover cut — a tradeoff, not a free win.

## Addendum: T2b — Signal-Level Hysteresis (extends, does not overwrite, the T2 finding above)

T2's no-trade band could only ever address same-sign weight wobble — 4% of
this signal's turnover (Signal Behavior, above). T2b adds a dead zone
directly on the sign decision in `compute_trend`
(`signal_i(t) = sign(trail_ret_i(t))` becomes stateful: hold the previous
sign inside a dead zone, flip immediately and in full on a clear move past
the opposite threshold). It stacks on T2; it does not replace it.

**Width, pre-registered before running anything:**
`dead_zone_i(t) = k * sigma_i(t) * sqrt(L/252)`, `k=0.5` — half the implied
120-day trailing-return standard deviation, derived from the
already-computed vol-targeting input `sigma_i(t)`, not a new free-floating
window. Per the explicit instruction this addendum follows, exactly one
adjustment to `k` (on principle, not by search) was authorized if the first
run missed the target. **It did not miss** — `k=0.5` produced 5.48x
annualized turnover immediately, so no adjustment was made.

**Three-stage turnover result** (long/short rule, `L=120`, full sample):

| stage | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|-------|--------------------|------------|--------------|-------------------|
| 1: pre-T2 (daily-naive) | 7.95% | 17.63% | -34.46% | 32.32x |
| 2: T2 only (`band_pct=0.20`) | 7.79% | 17.64% | -34.34% | 31.24x |
| 3: T2 + T2b (band + hysteresis) | 4.24% | 17.41% | -32.84% | **5.48x** |

Plot: `sprints/v8.2/plots/turnover_three_stages.png`. Sign flips: 1,183 → 198
(an 83% reduction, matching the turnover cut almost exactly — confirming the
diagnosis that flips, not wobble, were the dominant cost).

**Net return cost, reported as-is:** 7.79% (stage 2) → 4.24% (stage 3). Per
House Rule 1, this is not evidence the buffer is bad (turnover, not return,
was the target) and not evidence it is good either.

**Responsiveness cost, quantified:** of 1,183 raw sign flips, 985 (83%) are
absorbed entirely as noise (they reverse before ever clearing the opposite
threshold). The remaining 198 are confirmed, but on average **8.9 trading
days (median 6) after** the raw signal first pointed that direction — the
genuine speed cost on real reversals, not just on noise. Plot:
`sprints/v8.2/plots/hysteresis_sign_whipsaw.png` (representative ticker:
IEF) shows the unbuffered sign whipsawing while the buffered sign holds
steady through the same period.

**Updated buy-and-hold comparison** (supersedes the T2-only row in
Backtest Results above):

| rule | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|------|--------------------|------------|--------------|-------------------|
| long-only (v8.1) | 13.26% | 15.35% | -24.48% | 34.78x |
| long/short, T2 + T2b (chosen book) | 4.24% | 17.41% | -32.84% | 5.48x |
| buy-and-hold equal-weight (1/8) | 7.04% | 10.08% | -32.05% | 0.00x |

Once turnover is brought under control, buy-and-hold's return (7.04%)
**exceeds** the trend book's (4.24%), at much lower volatility — a sharper
version of Key Finding 5, not a new finding.

**Additional key findings from this addendum:**
6. A correctly-diagnosed cost source, when addressed directly (hysteresis
   on the sign, not magnitude on the weight), can hit a turnover target a
   plausible-but-misdirected mechanism (the band) could not — confirming
   the value of decomposing *why* a number didn't move before reaching for
   a different lever.
7. Turnover reduction is not free: the same mechanism that filtered 83% of
   raw flips as noise also slowed genuine reversals by ~9 trading days on
   average, and the book's net return fell further as a result. Both sides
   of that tradeoff are now measured, not assumed.

**Tests, code, notebook:** `signals.trend_signal.compute_dead_zone`,
`_hysteresis_signal`, `compute_trend(..., k_dead_zone=...)`; 11 new tests in
`tests/test_trend_signal.py` (dead-zone formula and symmetry, oscillation
holds prior sign, clear move flips, first-valid-date seeding, gap-day
masking, no-look-ahead, vol-target identity, gross cap, T2-on-top-of-T2b);
`notebooks/08_2_add_shorts.ipynb` extended in place (T2b section inserted
between the T2 diagnosis and T8, not appended at the end).

## Guardrail (S2, verbatim)

"This sprint sets operational parameters; it does not claim predictive
validity. All performance figures here are measured over 2007-2026, a single
historical regime dominated by one secular rate-decline supercycle that
mechanically flatters trend-following exposure to rate-sensitive names. No
parameter in this sprint was chosen to maximize any backtest metric."
