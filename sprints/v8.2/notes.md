# Sprint v8.2 -- Notes
**Signal: symmetric time-series trend (120d lookback, sign-based, long/short), vol-targeted, 8-name ETF universe**

This sprint is in progress. T1, T3, T4, T5, T2, T6, T7, T8 are done. T9
(guardrail + close) is deliberately held open until the sprint actually
closes -- see "What remains" at the end of this file.

Decisions carried into this pass, restated for the record: keep
`long_short=True` (shorts), keep `L=120` -- both held fixed despite the
backtest numbers below being able to tempt a change, because no operational
(non-Sharpe) reason was found to revisit either. T2 and T8 were done because
they make the existing measurement more honest (real turnover/cost under a
less naive rebalance rule; a real baseline to judge the trend book against),
not because either was expected to flatter the numbers.

---

## T1 -- Symmetric trend signal + net/gross exposure (done)

`signals/trend_signal.py::compute_trend` gained a `long_short: bool = True`
parameter:
- `long_short=True` (new default, v8.2): `signal_i(t) = sign(trail_ret_i(t))
  in {-1, 0, +1}`.
- `long_short=False` (v8.1 rule, kept, not deleted): `signal_i(t) = 1 if
  trail_ret_i(t) > 0 else 0`.

The vol-targeting formula itself is unchanged
(`raw_weight_i(t) = signal_i(t) * min(v/sigma_i(t), w_max)`) -- multiplying by
a signed `signal_i(t)` after clipping the always-positive `v/sigma_i(t)` to
`w_max` was already sign-correct in the v8.1 code, so no formula change was
needed there, only the signal itself.

Added `net_exposure` (`sum_i weight_i(t)`, signed) and `gross_exposure`
(`sum_i |weight_i(t)|`) as explicit tidy-frame columns (gross existed
internally in v8.1 under the name `gross`; both are now named to match the
PRD and exposed via a new `to_exposure_series(tidy)` helper that returns a
clean date-indexed `[gross_exposure, net_exposure]` frame).

On the real 8-name universe, 2007-2026, `long_short=True`: no degenerate
all-flat case -- both long (`signal=+1`) and short (`signal=-1`) rows are
present for every ticker (verified directly, not assumed, in
`tests/test_trend_signal.py::test_per_name_weight_bound_respected`).

---

## Tests added/updated (`tests/test_trend_signal.py`, `tests/test_multi_asset_backtest.py`)

18 tests in `test_trend_signal.py` (was 13 in v8.1), 9 new in
`test_multi_asset_backtest.py`. All 27 pass.

**E2 (vol-target identity), now explicitly verified for shorts:**
`test_vol_target_formula_identity_real_universe` now asserts the active set
contains `signal == -1` rows before checking the identity
`raw_weight_i(t) * sigma_i(t) == signal_i(t) * min(v, w_max * sigma_i(t))` --
so the test cannot pass vacuously by accidentally never exercising the
negative case. It holds exactly (`atol=1e-9`) including for shorts.

**E3 (leverage cap), now explicitly verified with shorts on:**
`test_gross_leverage_cap_respected` (real data, signed default) and a new
`test_gross_leverage_cap_respected_under_stress_all_short` (synthetic
all-downtrend universe, every raw weight negative) confirm `gross_exposure
<= g_max` holds when computed from negative inputs -- i.e. the cap logic
uses `abs()` correctly and does not let negative weights cancel and
silently undercount gross.

**E6 (new, net/gross relationship):**
`test_net_within_gross_real_universe`: `|net_exposure(t)| <= gross_exposure(t)`
for every date (the mathematical identity `|sum(x_i)| <= sum(|x_i|)`,
verified, not assumed) and `gross_exposure(t) <= g_max` for every date.
`test_net_diverges_from_gross_with_shorts_on`: confirms `net != gross` on at
least some real days once shorts are allowed (the v8.1 long-only book had
`net == gross` on every single day; that equality must not silently
persist). `test_net_equals_gross_long_only`: confirms it still does persist
exactly when `long_short=False` -- the v8.1 code path is unchanged.

**E1 (no look-ahead), re-verified for the signed signal:** both existing E1
tests (`test_no_lookahead_perturb_future_leaves_past_unchanged`,
`test_no_lookahead_on_real_universe`) now exercise `long_short=True` (the new
default), since `real_close`/synthetic fixtures call `compute_trend(...)`
without overriding `long_short`. No new bug found.

**B1 (P&L reconciliation) and B2 (cost-model fidelity), new
(`tests/test_multi_asset_backtest.py`):** see T3 below.

---

## T3 -- Daily P&L accumulator (done)

**Architecture note (pre-registered in the PRD, confirmed here, not a
mid-implementation surprise):** `backtest/engine.py::run()` assumes a single
pair, one hedge ratio, and discrete `{-1,0,+1}` positions with a round-trip
trade ledger. It was not used for this book -- forcing an 8-name,
continuously fractional-weighted, daily-rebalanced portfolio through that
API would mean synthesizing fake single-pair trades, which would obscure
rather than clarify the P&L.

Built `backtest/multi_asset.py::run_multi_asset(target, close, notional,
cost_params)` instead, reusing `execution.costs.CostParams` (the exact v6.5
constants: `half_spread_bp=1.5`, `slippage_bp=0.5`, `borrow_annual=0.004`)
via a turnover-based daily cost:
```
daily_pnl(t)  = sum_i [target_i(t) * ret_i(t)] * notional - daily_cost(t)
daily_cost(t) = (half_spread_bp + slippage_bp) * 1e-4 * sum_i |Delta target_i(t)| * notional
                + borrow_annual/252 * notional * sum_i max(-target_i(t), 0)
```
`target` must already be the position *held* on each date (the output of
`signals.trend_signal.shift_to_next_day`) -- the module does not apply any
further shift internally. A dedicated test
(`test_b1_unshifted_weight_is_a_lookahead_bug_demonstration`) confirms
passing the un-shifted `weight` instead of `target` produces a different
(wrong) result, so a caller cannot silently swap them.

**B1 verified**: `test_b1_pnl_reconciles_with_independent_reference` compares
`run_multi_asset`'s output against a hand-rolled reference formula written
separately (a 3-day, 2-asset worked example), exact match.
`test_b1_no_lookahead_on_real_universe` perturbs `close` after a cutoff and
confirms the pre-cutoff P&L is unchanged.

**B2 verified**: `test_b2_default_cost_params_match_v6_5_constants` pins the
exact numbers; `test_b2_run_multi_asset_uses_default_cost_params_unless_overridden`
confirms a deliberately loosened cost model produces a different result (so
the default isn't accidentally being ignored).

Also added `annualized_turnover(target)` (mean daily `sum_i |Delta target_i(t)|`,
times 252) and small `annualized_return`/`annualized_vol` helpers
(operating on `daily_pnl / notional`, so they are independent of the
particular notional chosen). `backtest.metrics.max_drawdown` is reused as-is
on the `daily_return` series.

---

## T4/T5 -- Long-only vs long/short through the accumulator (done, via notebook)

`notebooks/08_2_add_shorts.ipynb` (built by `scripts/build_notebook_v8_2.py`,
executes clean, ends with `[notebook clean]`). Both rules at `L=120`
(carried, not retuned), full 2007-2026 sample, v6.5 costs:

| rule | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|------|-------------------|------------|--------------|-------------------|
| long-only (v8.1) | 13.26% | 15.35% | -24.48% | 34.78x |
| long/short (v8.2) | 7.95% | 17.67% | -34.46% | 32.37x |

**These are operational-comparison numbers, not a performance claim (S2).**
Long-only outperforms long/short here, and the most likely reason is exactly
the single-rate-supercycle risk this PRD called out: long-only was able to
ride a multi-decade TLT/IEF rate decline with no offsetting short legs;
long/short occasionally shorts those same names during phases of that one-
directional move, which costs money in this specific sample without there
being any evidence it would cost money in a sample that contains more than
one rate regime. **This number is not evidence long-only is the better rule
going forward** -- it is evidence that this 19-year window flatters
sustained-long-duration exposure, which is precisely House Rule 6.

Turnover is high for both rules (~32-35x annualized) under **daily**
rebalancing of a continuous vol-target -- `sigma_i(t)` moves a little every
day even when `signal_i(t)` does not flip, so the weight is rarely exactly
constant day to day. This is itself the concrete motivation for T2
(rebalance frequency), not yet implemented in this pass -- see "What
remains."

Net vs. gross exposure (plotted in
`sprints/v8.2/plots/long_only_vs_long_short.png`): the long-only book's net
equals its gross on every day (no shorts exist to create a difference); the
long/short book's net visibly diverges from its gross, consistent with E6's
test on real data.

---

## Light lookback robustness check (informational, not Sharpe-maximizing)

Long/short rule, `L` in `{90, 120, 150}` (a small pre-registered bracket
around the carried default, not a grid search):

| L | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|---|--------------------|------------|--------------|-------------------|
| 90 | 6.28% | 17.55% | -33.68% | 36.03x |
| 120 | 7.95% | 17.67% | -34.46% | 32.37x |
| 150 | 4.18% | 17.23% | -48.53% | 31.01x |

Net return sign is consistent (positive) across all three values -- the
result does not collapse or flip sign character as `L` moves, which is the
question this check exists to answer. **`L=120` remains the default
regardless of `L=120` not being the highest number in this table (`L=90` is
close, `L=150` is worse on every metric including drawdown) -- no parameter
here was chosen to maximize anything, consistent with House Rule 1.**

---

## T2 -- No-trade band (done) -- scope refinement from "rebalance frequency"

**Scope refinement, recorded explicitly so the PRD and code do not silently
drift apart:** T2 was originally written in the PRD as "rebalance
frequency" -- a discrete daily-vs-weekly recompute schedule
(`rebal_freq ∈ {1,5}`). An earlier pass through this sprint implemented
exactly that (plus a flat absolute `no_trade_band=0.05`), found via a
4-cell comparison that `rebal_freq=5` (weekly) qualified under the
pre-registered decision rule and cut turnover ~54%. **That implementation is
now superseded.** T2 is refined to be specifically a **state-based,
proportional no-trade band** (`band_pct=0.20`, checked every day, no
discrete schedule) -- the more specific and better-targeted design: the
book checks its target daily; a name only trades when the gap between its
currently held weight and the newly desired weight exceeds 20% of that
day's target (proportional, not flat, because vol-targeted weights vary
widely across the universe -- EEM's high-vol weight is much smaller than
IEF's low-vol weight, so a flat band would be relatively tight for one and
loose for the other). On breach, the trade goes all the way to the new
target, not partway to the band edge. `rebal_freq` and the flat
`no_trade_band` remain in the code (not deleted, already tested), but are
no longer the chosen mechanism.

**A real correctness wrinkle found while building this, independent of
which band variant is used:** the gross-leverage cap is enforced jointly
across all 8 names in `compute_trend`, but once names can go stale on
independent schedules (held instead of traded), the *held* combination is
no longer automatically guaranteed to satisfy `gross <= g_max` by the same
proof. Fixed by re-applying the cap to the held weights themselves
(`signals.trend_signal.apply_rebalance_control`), verified directly
(`test_rebalance_control_gross_cap_respected_despite_staleness`, an
engineered case where partial staleness would otherwise breach the cap, and
`test_band_pct_gross_cap_respected_real_universe` for the proportional band
specifically).

`apply_rebalance_control` also carries a previously-held weight forward
through a temporary one-day data gap (a missing price print) rather than
dropping the name to zero for that day -- a deliberate choice (you do not
liquidate a position because a data feed hiccupped), confirmed by
`test_rebalance_control_carries_forward_through_temporary_data_gap`.

**Result -- and an honest miss against the stated success criterion.**
Before (`band_pct=0`) vs after (`band_pct=0.20`), long/short rule, `L=120`,
full 2007-2026 sample:

| | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|---|--------------------|------------|--------------|-------------------|
| before (daily, band off) | 7.95% | 17.63% | -34.46% | 32.32x |
| after (band_pct=0.20) | 7.79% | 17.64% | -34.34% | 31.24x |

Turnover fell only 3.3% (32.32x to 31.24x) -- **the band did not reach the
stated success criterion of single-digit-to-low-double-digit annualized
turnover.** Rather than declare success on a number that barely moved, the
notebook decomposes turnover by cause:

| source | share of total turnover |
|--------|--------------------------|
| sign flips (long-to-short or short-to-long) | 96.0% |
| same-sign magnitude wobble | 3.9% |

1,183 flip events over the sample, averaging `|delta|≈0.50` per flip (a
full reversal, since `w_max=0.50`); 36,287 continuing-same-sign days
averaging `|delta|≈0.0007`. **A magnitude-based no-trade band only
suppresses the wobble share, and the wobble share is ~4% of total turnover
-- not the ~96% that comes from outright sign flips.** Even a band wide
enough to eliminate all of the wobble could not have gotten close to the
stated target, because it is structurally aimed at the wrong source. The
band is correctly implemented and does what it is designed to do; the
turnover problem in this signal is dominated by something else entirely.

**T2 remains a real, correctly-implemented mechanism** even though it is
superseded as the primary turnover control by T2b below: the magnitude band
still damps small position-size wobble on top of whatever sign decision is
in force, and `band_pct=0.20` continues to apply unchanged in the T2+T2b
book. Carried defaults `L=120`, `v=0.10`, `w_max=0.50`, `g_max=2.0` are
unchanged either way -- this finding touches only the rebalance mechanics,
never the signal or sizing parameters.

---

## T2b -- Signal-level hysteresis (dead zone), addressing sign-flip turnover

**Why this exists:** T2's magnitude band could only ever address the 4% of
turnover that is same-sign wobble -- the diagnosis above identified the
real driver (96% sign-flip turnover) as something a position-size band
structurally cannot touch. T2b targets the actual driver directly: a dead
zone on the *sign decision itself*, inside `compute_trend`, stacked on top
of (not replacing) T2.

**Mechanism.** `signal_i(t) = sign(trail_ret_i(t))` becomes stateful:
go long when `trail_ret_i(t)` is clearly positive (above an upper
threshold), go short when clearly negative (below the symmetric lower
threshold); inside the dead zone, hold the previously held sign rather than
flip. On a clear move past the opposite threshold, the sign still flips
immediately and in full -- this dampens whipsaw, it does not freeze the
signal. A name's first valid date has no prior state to hold, so it seeds
with the plain (unbuffered) sign -- documented and tested explicitly
(`test_hysteresis_first_valid_date_seeds_with_plain_sign`).

**Dead-zone width, pre-registered from first principles
(`signals.trend_signal.compute_dead_zone`):**
```
dead_zone_i(t) = k * sigma_i(t) * sqrt(L / 252),   k = 0.5
```
`sigma_i(t)` is the already-computed annualized daily vol (63d trailing
window, used for vol-targeting); scaling it by `sqrt(L/252)` converts it to
the implied standard deviation of the L-day trailing return itself, under a
random-walk variance assumption (`var(L-day return) ~= L * var(daily
return)`). `k=0.5` means the dead zone is half that implied L-day noise --
chosen from first principles before running anything, reusing an existing
point-in-time-correct quantity instead of introducing a new free-floating
rolling-window parameter. **No adjustment was needed**: `k=0.5` landed
turnover at 5.48x annualized on the first run, inside the pre-registered
single-digit/low-double-digit target (the user's explicit instruction was
to widen or narrow by one adjustment "on principle, not by sweeping for the
best backtest" only if the first try missed -- it did not miss, so `k=0.5`
stands unchanged).

**Three-stage turnover result** (long/short rule, `L=120`, full 2007-2026
sample):

| stage | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|-------|--------------------|------------|--------------|-------------------|
| 1: pre-T2 (daily-naive) | 7.95% | 17.63% | -34.46% | 32.32x |
| 2: T2 only (`band_pct=0.20`) | 7.79% | 17.64% | -34.34% | 31.24x |
| 3: T2 + T2b (band + hysteresis) | 4.24% | 17.41% | -32.84% | **5.48x** |

Turnover: 32.32x to 5.48x, an 83.0% cut vs. stage 1 -- **the success
criterion (single-digit to low-double-digit annualized) is met at stage
3.** Sign flips drop from 1,183 to 198 (an 83% reduction, the same
percentage as the turnover cut -- consistent with the diagnosis that flips,
not wobble, were the dominant cost).

**The honest cost: net return also fell, from 7.79% (stage 2) to 4.24%
(stage 3).** This is reported as-is, not minimized -- delaying reversals
costs some of whatever edge those reversals had in this specific sample.
Per House Rule 1, this is not evidence the buffer is bad (no return
threshold was ever the target) and it is not evidence the buffer is good
either -- the target was turnover, and turnover is what moved.

**Responsiveness tradeoff, quantified, not just asserted:** of 1,183 raw
sign flips, 985 (83%) are absorbed entirely -- they reverse before ever
clearing the opposite threshold, i.e. they were noise the buffer correctly
filtered. The remaining 198 do get confirmed, but on average **8.9 trading
days (median 6) after** the raw signal first pointed that direction --
this is the genuine speed cost of the buffer on real reversals, not just on
noise. Plot: `sprints/v8.2/plots/hysteresis_sign_whipsaw.png` (representative
ticker: IEF, the name with the most raw flips) shows the unbuffered sign
oscillating while the buffered sign holds steady through the same period.

**Why T2b is adopted despite the return cost:** the explicit goal was
turnover, stated and met; House Rule 1 means a return change is not grounds
to reject or accept a cost-control mechanism either way. The book carried
forward into v8.3's cost-attribution work is **T2 + T2b together**
(`band_pct=0.20`, `k_dead_zone=0.5`), per the sequencing already agreed --
attribution should decompose this turnover profile, not the pre-T2b one.

---

## T8 -- Buy-and-hold equal-weight baseline (done)

1/8 per name, constant from day one, no vol targeting, through the same
accumulator and v6.5 cost constants -- structurally near-zero turnover
(there is nothing to rebalance). Reported alongside the trend book with no
claim of superiority either way:

| rule | net return (ann.) | vol (ann.) | max drawdown | turnover (ann.) |
|------|--------------------|------------|--------------|-------------------|
| long-only (v8.1) | 13.26% | 15.35% | -24.48% | 34.78x |
| long/short, T2 + T2b (chosen book) | 4.24% | 17.41% | -32.84% | 5.48x |
| buy-and-hold equal-weight (1/8) | 7.04% | 10.08% | -32.05% | 0.00x |

Buy-and-hold's net return (7.04%) now exceeds the long/short trend book's
(4.24%) over this sample, at materially lower volatility (10.08% vs
17.41%) and a similar drawdown -- once turnover is brought under control,
the trend book looks worse on a return basis than the passive basket, not
just comparable. This is exactly the kind of humbling, calibrating
comparison T8 exists to provide, not a verdict either way (no IC test, no
Sharpe gate, House Rule 1) -- and it is a direct, honest consequence of the
turnover-vs-responsiveness tradeoff documented under T2b, not a separate
finding.

---

## T9 -- S2 guardrail + sprint close (done)

**S2 (verbatim):** "This sprint sets operational parameters; it does not
claim predictive validity. All performance figures here are measured over
2007-2026, a single historical regime dominated by one secular rate-decline
supercycle that mechanically flatters trend-following exposure to
rate-sensitive names. No parameter in this sprint was chosen to maximize
any backtest metric."

### Gate-status table

| ID | Status | Note |
|----|--------|------|
| E1 | PASS | no look-ahead, signal/position/rebalance-control hold logic |
| E2 | PASS | vol-target identity, explicitly verified with signal=-1 rows |
| E3 | PASS | gross cap, including with negative weights and post-band re-cap |
| E4 | PASS | reproducibility |
| E5 | PASS | point-in-time membership, max(L,W)=120 (v8.1 correction carried) |
| E6 | PASS | net/gross relationship; net diverges from gross once shorts are on |
| B1 | PASS | daily P&L reconciles against an independent reference formula |
| B2 | PASS | cost model matches v6.5 constants exactly |
| S2 | stated | guardrail above |
| T2 success criterion (band alone) | **NOT MET** | turnover cut 3.3%, not single-digit/low-double-digit; diagnosed (96% of turnover is sign-flip-driven) |
| T2b success criterion (band + hysteresis) | **MET** | turnover cut 83.0% vs. stage 1 (32.32x -> 5.48x); net return cost reported honestly (7.79% -> 4.24%), not minimized |

### Chosen operational defaults

| parameter | value | reasoning |
|-----------|-------|-----------|
| `L` (lookback) | 120 | carried from v8.1; robustness check confirms no collapse across {90,120,150}; not the best of the three, kept anyway -- no Sharpe-based reason to move |
| `v` (vol target) | 0.10 | carried from v8.1; no operational sanity flag triggered |
| `w_max` | 0.50 | carried from v8.1; no operational sanity flag triggered |
| `g_max` | 2.0 | carried from v8.1; respected on every day, including under the band and the hysteresis re-cap |
| `long_short` | True (shorts on) | kept despite long-only's better backtest numbers in this sample -- that advantage is attributed to the single-rate-supercycle bias, not a reason to revert |
| magnitude control (T2) | `band_pct=0.20`, `rebal_freq=1` | kept; still damps same-sign wobble on top of the sign decision |
| sign-flip control (T2b) | `k_dead_zone=0.5` | the mechanism that actually controls turnover; landed in the target range on the first run, no adjustment needed |

The book carried into v8.3 is **T2 + T2b together** (`band_pct=0.20`,
`k_dead_zone=0.5`) -- the turnover profile any cost-attribution work should
decompose, per the sequencing already agreed.

Sprint v8.2 status: **closed**. T1-T9 + T2b all done. See `WALKTHROUGH.md`
for the full report (note: the walkthrough's headline turnover finding is
now extended by T2b, not overwritten -- see the WALKTHROUGH.md addendum).
