# Sprint v8.2 — Add Shorts and Set Parameters (Programme v8, Sprint 2)

## Context: What This Sprint Is and Is Not

v8.1 built a long-only/flat trend instrument (sign of a 120-day trailing
return, vol-targeted) and explicitly ran no backtest — no cost model, no
net return, no Sharpe (House Rule 1). v8.2 does two things: (1) makes the
signal symmetric (downtrends now produce a short, not flat), and (2) for
the first time runs the resulting book through a cost-aware daily P&L
calculation to set **operational** defaults — primarily rebalance
frequency, and a sanity check (not a re-discovery) of the lookback and vol
target carried from v8.1.

**This sprint computes performance numbers it did not compute before. That
does not make it a research sprint.** Net return, vol, max drawdown, and
turnover are reported because an operational instrument needs to know its
own turnover and drawdown character before anyone runs it — not because
any of these numbers are being used to argue the rule has positive expected
value going forward. No parameter in this sprint is chosen to maximize a
backtest metric. The single largest risk to misreading this sprint's output
is treated as a first-class topic below, not a footnote: **2007–2026 is one
historical regime, dominated by a single secular decline-then-partial-
reversal in interest rates, that mechanically flatters any trend-following
strategy that spent most of the sample long rate-sensitive assets (TLT,
IEF).** Every reported performance number in this sprint inherits that bias
and is captioned accordingly.

## v8 House Rules (carried from v8.1, amended)

1. **No edge claim — amended, not weakened.** v8.1 said "no Sharpe gate, no
   IC test." v8.2 *does* compute Sharpe-adjacent figures (net return, vol,
   drawdown, turnover), because operational parameter-setting needs them.
   The amendment: these figures are used only to verify operational sanity
   and choose rebalance frequency — **never** to select or justify `L`, `v`,
   `w_max`, or `g_max`. If any of those four needs revisiting, the reason
   must be operational (turnover, leverage, drawdown sanity), stated
   explicitly, never "this produced a higher Sharpe."
2. **No look-ahead** — carried, re-verified for the signed/symmetric signal
   and for sub-daily rebalance frequencies (a stale-but-valid held weight
   must never use data from after its last recompute date).
3. **Risk budgeting, not signal optimization** — carried; the same
   `v=0.10`/`w_max=0.50`/`g_max=2.0` formula now applies symmetrically to
   shorts, verified, not assumed.
4. **Universe chosen for liquidity, not performance** — carried unchanged
   from v8.1; this sprint does not add, drop, or reweight tickers.
5. **Mechanical and reproducible** — carried. The only genuinely new free
   design choice this sprint is rebalance frequency, decided by a pre-
   registered, non-Sharpe operational rule (§Success Metrics), chosen
   *before* any backtest number is observed.
6. **Single-regime caveat, stated wherever a performance number appears.**
   2007–2026 contains one secular rate cycle (~5% → ~0% → partial reversal
   from 2022). A trend rule that holds TLT/IEF long for most of a one-
   directional multi-decade decline is not facing the same conditions going
   forward. This is disclosed, not corrected for — no regime reweighting,
   no synthetic alternate-history sample, no claim that these numbers
   generalize.

---

## Economic Hypothesis

None is under test (House Rule 1, amended). The rationale for this sprint's
existence: making the signal symmetric (long *and* short) changes the
position-construction surface in ways that need re-verification, not
assumption — borrow now accrues on every short name rather than never, the
vol-target formula must be checked to still hold its sign correctly, and net
exposure (which was always equal to gross exposure in the long-only v8.1
book) now becomes a genuinely informative, independent series. Running a
cost-aware backtest is the only way to find out whether the carried v8.1
defaults (`L=120`, `v=0.10`, `w_max=0.50`, `g_max=2.0`) produce sane turnover
and leverage once shorting and realistic costs are in the picture, and to
choose a rebalance frequency that doesn't churn the book pointlessly against
a signal that itself only updates over a 120-day window.

---

## Falsification Criteria

These remain engineering/operational gates, not signal-quality gates (House
Rule 1). `E1–E5` are carried from v8.1 (with v8.1's own corrections — see
below); `E6` is new for the signed case. `B1–B2` are new backtest-engineering
correctness gates. `S2` is the mandatory non-gating guardrail.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|----------------|------------------|
| E1 | No look-ahead (signal, position, *and* the rebalance-frequency hold logic) | exact match on unperturbed prefix, all `rebal_freq` values | bug; fix before any number is reported |
| E2 | Vol-target formula identity, now signed: `raw_weight_i(t) * sigma_i(t) == signal_i(t) * min(v, w_max * sigma_i(t))` for `signal_i(t) ∈ {-1, 0, +1}` | holds exactly (`atol=1e-9`) | scaling/sign bug; fix |
| E3 | Gross leverage cap respected, now with negative weights | `sum(abs(weight)) <= g_max=2.0` every day | bug in the abs-based scale logic; fix |
| E4 | Reproducibility | bit-for-bit identical across runs | non-determinism; fix |
| E5 | Point-in-time universe membership | no value before `max(L, W) = 120` days of a ticker's own history (v8.1 correction carried — *not* the PRD's original `L+W`) | bug; fix |
| E6 | Net/gross exposure relationship (new) | `abs(net(t)) <= gross(t)` every day (a mathematical identity — `\|Σx_i\| <= Σ\|x_i\|` — verified directly, not assumed) | bug in the net or gross computation; fix |
| B1 | Daily portfolio P&L reconciles exactly: `daily_pnl(t) = Σ_i [weight_i(t-1) * asset_return_i(t)] * NAV - daily_cost(t)` | exact recomputation matches, no off-by-one | leakage or mis-lagged P&L; fix |
| B2 | Cost model matches the v6.5 constants exactly: `half_spread_bp=1.5`, `slippage_bp=0.5`, `borrow_annual=0.004` (`execution.costs.CostParams` defaults) | exact constant match, no loosening | wrong/loosened cost assumption; fix |
| S2 | Guardrail statement, not a test | must appear verbatim in `notes.md`: **"This sprint sets operational parameters; it does not claim predictive validity. All performance figures here are measured over 2007–2026, a single historical regime dominated by one secular rate-decline supercycle that mechanically flatters trend-following exposure to rate-sensitive names. No parameter in this sprint was chosen to maximize any backtest metric."** | N/A |
| E7 | T2b hysteresis: no look-ahead, vol-target identity, and gross cap all hold with `k_dead_zone>0` (re-verification of E1/E2/E3 for the buffered signal, not new claims) | same thresholds as E1/E2/E3 | bug; fix |

---

## Signal Definition

**Symmetric trend signal** (replaces v8.1's long-only/flat rule):
```
trail_ret_i(t) = adj_close_i(t) / adj_close_i(t - L) - 1,   L = 120   (carried, not retuned)

signal_i(t) =  +1   if trail_ret_i(t) > 0
            =  -1   if trail_ret_i(t) < 0
            =   0   if trail_ret_i(t) == 0    (exact boundary; expected to be
                                                 effectively never hit on real
                                                 floating-point price data —
                                                 included for well-definedness,
                                                 not because it materially
                                                 occurs)
```
Defined only once both the `L=120` and `W=63` warmups are satisfied (E5),
exactly as in v8.1.

**Vol-targeted sizing, now symmetric** (same formula as v8.1, `signal_i(t)`
now signed):
```
sigma_i(t)      = std(log_ret_i, window=W) * sqrt(252),   W = 63   (carried)
raw_weight_i(t) = signal_i(t) * min(v / sigma_i(t), w_max)
                  v = 0.10, w_max = 0.50   (carried, both expected unchanged
                                             — see Success Metrics for the
                                             operational sanity check)
```

**Leverage cap** (unchanged formula, now operating on signed weights):
```
gross(t)  = Σ_i |raw_weight_i(t)|
scale(t)  = min(1, g_max / gross(t)) if gross(t) > 0 else 1,   g_max = 2.0
weight_i(t) = raw_weight_i(t) * scale(t)
```

**Net and gross exposure — first-class tracked series (new):**
```
net(t)   = Σ_i weight_i(t)        # signed; can range from -gross(t) to +gross(t)
gross(t) = Σ_i |weight_i(t)|       # unsigned; bounded by g_max
```
In the v8.1 long-only book these were always equal. They are no longer
expected to be, and the relationship between them (E6) is exactly the kind
of thing this sprint exists to observe, not assume.

**No-trade band (the new free parameter — addendum, refined during T2
implementation):**
```
band_pct = 0.20   # proportional: only trade a name if |desired - held|
                  # exceeds 20% of that day's desired (target) weight
```
**Scope refinement, recorded explicitly (not silently substituted):** T2
was originally written above as "rebalance frequency"
(`rebal_freq ∈ {1, 5}`, a discrete daily-vs-weekly schedule). T2 is refined
to be specifically a **state-based, proportional no-trade band**: the book
checks its target every day (`rebal_freq=1`); a name only trades when the
gap between its currently held weight and the newly desired weight exceeds
`band_pct` of that day's desired weight. Proportional, not flat, because
vol-targeted weights vary widely across the universe (a high-vol name like
EEM carries a much smaller weight than a low-vol name like IEF, so a single
flat band would be relatively tight for one and loose for the other). On
breach, the trade goes all the way to the new target, not partway to the
band edge. `rebal_freq` and a flat `no_trade_band` remain available in the
code (already implemented and tested, an earlier pass through this sprint
used them and found `rebal_freq=5` cut turnover ~54% under the original
decision rule) but are superseded as the chosen mechanism by this
refinement — see `sprints/v8.2/notes.md` T2 for the full record, including
why the chosen (more principled) mechanism achieves a smaller cut than the
superseded one.

Both mechanisms use only already-known data (the newly desired value,
computed point-in-time, and the previously held value) — holding a
stale-but-valid weight is conservative, it does not introduce look-ahead.

A consequence found during implementation, not assumed at design time:
because the gross-leverage cap is enforced jointly across all 8 names,
letting individual names go stale (held rather than traded) means the
*held* combination is no longer automatically guaranteed to satisfy
`gross <= g_max` by the same proof that covers the fully-synchronized
desired vector. The implementation re-applies the cap to the held weights
themselves (`signals.trend_signal.apply_rebalance_control`) so this holds
by construction, not by empirical luck — tested directly, including an
engineered case where partial staleness would otherwise breach the cap.

**T2b — signal-level hysteresis (addendum, added after T2 was found to miss
its own success criterion):**
```
dead_zone_i(t) = k * sigma_i(t) * sqrt(L / 252),   k = 0.5
```
T2's magnitude band can only ever address same-sign weight wobble (4% of
this signal's turnover, per the decomposition in `notes.md`); 96% comes
from outright sign flips, which a position-size band cannot touch by
construction. T2b adds a dead zone directly on the sign decision in
`compute_trend`: go long when `trail_ret_i(t)` is clearly positive (above
the dead zone), short when clearly negative (below the symmetric negative
dead zone), and hold the previous sign inside the dead zone. It stacks on
T2, it does not replace it.

**Width rationale:** `sigma_i(t)` (the 63d annualized vol already computed
for vol-targeting) is rescaled by `sqrt(L/252)` to the implied standard
deviation of the L-day trailing return itself, under a random-walk
variance assumption. `k=0.5` (half that implied noise) is chosen from
first principles, before observing any backtest result, and reuses an
existing point-in-time-correct quantity rather than introducing a new
free-floating rolling-window parameter. Per the explicit instruction this
addendum follows: if the first run had missed the turnover target, the
correct response would have been exactly one adjustment to `k` "on
principle, not by sweeping for the best backtest" — widen if turnover
stayed too high, narrow if the book barely traded. **No adjustment was
needed**: `k=0.5` produced 5.48x annualized turnover on the first run,
inside the pre-registered single-digit/low-double-digit target.

This is a turnover-control parameter. It is not tuned to maximize return,
and the recorded outcome includes the return cost honestly (net return
fell from 7.79% to 4.24% once T2b was added) — see `notes.md` and
`WALKTHROUGH.md` for the full three-stage result and the responsiveness
tradeoff (how much slower the buffered book is to act on genuine
reversals).

---

## Data

Same 8-name universe and `data/raw/{ticker}.parquet` sources as v8.1 — no
new ingestion. The daily P&L calculation additionally uses each name's own
daily simple or log return (already derivable from `adj_close`, not a new
data source).

---

## Success Metrics

No Sharpe gate, no IC threshold (House Rule 1). Two distinct tables:

**(a) Engineering/operational gates** — pass/fail, from §Falsification
Criteria: `E1–E6`, `B1–B2`, `S2`.

**(b) Reporting table — informational, explicitly captioned, not gated:**
for each of {long-only (v8.1 rule), long/short (v8.2 rule)} × {`rebal_freq`
∈ {1, 5}} (4 cells):
- annualized net return
- annualized vol
- max drawdown
- turnover, defined for a continuously-weighted book as the trailing-252d
  average of `Σ_i |weight_i(t) - weight_i(t-1)|`, annualized (this is *not*
  "trades per year" from `backtest.metrics.summary` — that metric assumes
  discrete round-trip trades, which this book does not have)

**Rebalance-control success criterion (pre-registered, non-Sharpe, refined
during T2 — see addendum above):** the chosen mechanism is the proportional
no-trade band (`band_pct=0.20`), checked every day. Success is annualized
turnover falling to a sane (single-digit to low-double-digit) level while
the book still tracks the signal — explicitly not that net return improved.
**Recorded outcome:** this criterion was not met (turnover fell only ~3%,
not into single digits) — see `notes.md` for the turnover decomposition
that explains why (96% of turnover comes from outright sign flips, which a
magnitude-based band cannot and should not suppress) and why the band is
still the chosen mechanism despite the miss. The discrete `rebal_freq=5`
schedule tested earlier in this sprint did clear a 30%-turnover-cut /
10%-drawdown-tolerance bar (the rule originally specified here) but is
superseded as a less targeted, blunter mechanism.

**Operational sanity flags on the carried defaults** (`L=120`, `v=0.10`,
`w_max=0.50`, `g_max=2.0`) — informational flags, not falsification of a
hypothesis, but a documented reason to revisit a parameter if triggered:
- average daily gross turnover exceeds 50% of average gross exposure (book
  churning faster than a 120-day signal should justify)
- gross exposure sits at the `g_max=2.0` cap on more than 80% of days (most
  names capped simultaneously most of the time defeats per-name risk
  budgeting)
- either of these, if triggered, is documented as the explicit operational
  reason for revisiting `L`/`v`/`w_max` — never "this produced a higher
  Sharpe"

---

## Research Architecture

**What is reused exactly, and what is not — stated now, not discovered
mid-implementation:**

- **Reused as-is:** `execution.costs.CostParams` — the v6.5 bp constants
  (`half_spread_bp=1.5`, `slippage_bp=0.5`, `borrow_annual=0.004`). Same
  numbers, same module.
- **Reused as-is:** `backtest.metrics.sharpe`, `.sortino`, `.max_drawdown` —
  these are pure functions of a daily P&L series with no assumptions about
  how positions were constructed; they apply unchanged to this multi-asset
  book.
- **Not reused as-is, and not expected to be:** `backtest.engine.run()`. It
  is built for a single pair, a single hedge ratio, discrete `{-1,0,+1}`
  positions, and emits a round-trip trade ledger — none of which matches an
  8-name, continuously fractional-weighted, daily-rebalanced book. Forcing
  this book through that function's API would require synthesizing fake
  single-pair trades, which would obscure rather than clarify the P&L. A
  new, small daily P&L accumulator is built instead (`backtest/multi_asset.py`),
  applying the *same* `CostParams` constants via a turnover-based daily cost
  (`(half_spread_bp + slippage_bp) * |Δweight_i(t)| * NAV` plus
  `borrow_annual/252 * NAV * Σ_i max(-weight_i(t), 0)` for short borrow)
  rather than `trade_cost()`'s per-round-trip formula.

```
[T1] signals/trend_signal.py: symmetric signal, net/gross exposure outputs
      |
[T2] rebal_freq parameter: hold weight between recompute dates
      |
[T3] backtest/multi_asset.py: daily P&L accumulator, v6.5 cost constants (B1, B2)
      |
[T4] Long-only (v8.1 rule) through the accumulator, rebal_freq in {1, 5}
      |
[T5] Long/short (v8.2 rule) through the accumulator, rebal_freq in {1, 5}
      |
[T6] Comparison table + rebalance-frequency decision (pre-registered rule)
      |
[T7] Leakage / look-ahead re-verification, full pipeline
      |
[T8] Sanity baseline: buy-and-hold equal-weight basket, same 8 names
      |
[T9] S2 guardrail + sprint close
```

---

## Risks and Biases

- **Single rate supercycle (the central risk of this sprint).** 2007–2026
  is dominated by one secular rate decline (partially reversed 2022+). A
  trend rule with sustained long exposure to TLT/IEF over most of that
  window earns P&L mechanically tied to a regime that will not repeat in
  the same direction by construction. Every reported return/Sharpe-like
  number in this sprint inherits this and is captioned accordingly — it is
  disclosed, not corrected for.
- **No out-of-sample holdout.** The full 2007–2026 sample is used for the
  operational sanity check; this sprint makes no forward-looking claim, so
  no holdout is reserved. A future sprint that *does* make a performance
  claim would need one.
- **New borrow exposure.** v8.1 never shorted, so borrow cost was always
  zero. v8.2 introduces it on every short name — B2 exists specifically to
  confirm the same v6.5 constant (`borrow_annual=0.004`) is applied, not a
  cheaper or more lenient assumption.
- **Net exposure can now swing meaningfully** (toward 0 when names disagree
  on trend direction, toward `±gross` when they agree) — this is exactly
  why it is tracked as a first-class series rather than inferred after the
  fact.
- **Rebalance-frequency decision risk.** Even a "non-Sharpe" operational
  rule could be unconsciously bent if the thresholds were chosen after
  glancing at results. Mitigated by fixing the 30%/10% thresholds in this
  PRD before either `rebal_freq` cell is computed.

---

## Out of Scope

- Any IC test, alpha claim, or Sharpe-maximization exercise
- Tuning `L`, `v`, `w_max`, or `g_max` — carried fixed from v8.1; only
  revisited if an operational sanity flag triggers, and then only with a
  stated non-Sharpe reason
- A wide rebalance-frequency or parameter grid (only the two pre-registered
  `rebal_freq` candidates)
- Universe changes
- Cross-sectional or risk-parity-across-the-book construction (still
  independent per-name vol targeting, no cross terms)
- Forward-looking or out-of-sample performance claims of any kind

---

## Dependencies

- `signals/etf_universe.py`, `signals/trend_signal.py` — v8.1, extended in
  place for the symmetric signal and `rebal_freq`
- `data/raw/{SPY,EFA,EEM,TLT,IEF,HYG,LQD,GLD}.parquet` — v8.1, unchanged
- `execution/costs.py` — v6.5 cost-model constants, reused exactly
- `backtest/metrics.py` — `sharpe`, `sortino`, `max_drawdown`, reused as-is
- New: `backtest/multi_asset.py` — the daily P&L accumulator for a
  continuously-weighted multi-asset book (this sprint's own deliverable)
