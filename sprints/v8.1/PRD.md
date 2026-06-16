# Sprint v8.1 — Universe and Trend Signal (Programme v8, Sprint 1)

## Context: Why v8 Is a Different Kind of Sprint

Programmes v1–v6.6 (HY/IG ETF spread family) and v7.1 (NAV wedge) were both
research programmes: each proposed a falsifiable economic hypothesis and a
pre-registered gate that could kill the signal. v6.6 closed with no deployable
signal. v7.1 closed at the data-availability gate before a hypothesis could even
be tested.

**v8 is not a research programme. It builds a reference instrument: a small,
transparent, mechanical cross-asset trend rule, used to exercise the parts of
the platform that v1–v7.1 never needed** — a multi-asset universe, vol-targeted
position sizing, a per-name risk budget, and a daily target-position vector.
Time-series momentum (sign of a trailing return) is a well-documented, generic
effect (Moskowitz, Ooi & Pedersen, 2012, among others). This sprint does not
test whether it works here. It builds the engineering around it correctly.

## v8 House Rules

1. **No edge claim.** This signal is not being validated for predictive content.
   There is no IC test, no Sharpe gate, no hit-rate threshold in this sprint.
   Any performance chart shown is explicitly illustrative, not a result.
2. **No look-ahead, enforced in code, not just asserted.** The position applied
   on day t+1 is a pure function of data available through the close of day t.
   This is tested directly (perturb future inputs, confirm past outputs are
   unchanged) — the same discipline used for `z_wedge` in v7.1.
3. **Risk budgeting, not signal optimization.** Position size is set by a fixed
   per-name volatility target and a gross leverage cap, so that no single name
   or low-volatility regime can dominate the book. The 0/1 trend signal itself
   is not tuned, weighted, or thresholded beyond its sign.
4. **Universe chosen for liquidity and data availability only.** Tickers are not
   selected, added, or dropped based on how well the trend rule performs on
   them. Doing so would be selection bias dressed up as a universe decision.
5. **Mechanical and reproducible.** Every parameter (lookback, vol window, risk
   budget, leverage cap) is named and fixed before this sprint's code is run
   against real data. No per-name overrides, no discretionary exceptions.

---

## Economic Hypothesis

There is no economic hypothesis under test in this sprint. The signal —
go long an asset when its trailing return is positive, otherwise hold flat —
is a long-documented, generic stylized fact (time-series momentum) used here
purely as a known, simple rule to build and exercise position-construction
machinery: vol targeting, per-name risk budgeting, gross leverage control, and
a clean daily target-position vector output. Whether this rule has positive
expected value on this universe is explicitly out of scope (House Rule 1).

---

## Falsification Criteria

These are engineering-correctness gates, not signal-quality gates — consistent
with House Rule 1 (no edge claim, no IC test). New `E`-prefixed series for
programme v8.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|----------------|------------------|
| E1 | No look-ahead: position for day t+1 is unchanged when any input dated after close t is perturbed | exact match (bit-for-bit) on the unperturbed prefix | Construction has a look-ahead bug; fix before any output is used anywhere |
| E2 | Per-name realized vol (trailing 63d, annualized, on the *active* days for that name) tracks its target within a stated tolerance | within 2x of the v=10% target (i.e. 5%–20% realized, on average across active windows) — vol-targeting is approximate by construction (using a trailing estimate of a forward quantity), not exact | Vol-targeting formula has a unit or scaling bug |
| E3 | Gross leverage cap is respected on every day | `sum(abs(target_position_vector))` ≤ `G_max` = 2.0 on 100% of days | Leverage-cap logic has a bug; fix |
| E4 | Reproducibility: identical inputs produce an identical target-position vector | bit-for-bit identical across two independent runs | Hidden state, unseeded randomness, or non-determinism in the pipeline |
| E5 | Point-in-time universe membership: a ticker is never included in the position vector before it has enough history for both the trend lookback (L=120d) and the vol window (W=63d) | 0 violations | Look-ahead via synthetic backfill or premature inclusion |
| S1 | Guardrail statement, not a test | N/A — must be written verbatim in `notes.md`: **"This is a learning instrument. The rule is mechanical and fully documented. No predictive claim, IC test, or Sharpe/performance threshold is in scope for v8.1."** | N/A |

**Gate structure:** E1–E5 are hard — any failure is a bug to fix before the
sprint closes, not a research dead-end (there is no hypothesis to falsify, only
code to get right). S1 is mandatory and non-gating.

---

## Signal Definition

**Universe** (chosen for liquidity and long, clean daily history — not for
predictive performance, per House Rule 4):

| ticker | asset class | role |
|--------|------------|------|
| SPY | US large-cap equity | |
| EFA | developed ex-US equity | regional diversification |
| EEM | emerging-market equity | regional diversification |
| TLT | 20+y US Treasury | long-duration rates |
| IEF | 7–10y US Treasury | intermediate-duration rates |
| HYG | US high-yield credit | |
| LQD | US investment-grade credit | |
| GLD | gold | non-equity, non-rate diversifier |

8 names. Each ticker enters the book independently once its own history covers
`L + W` trading days (E5) — no common start date is imposed; this is itself a
deliberate test of point-in-time, staggered-inception handling.

**Trend signal (time-series, sign-only, long-only/flat):**
```
trail_ret_i(t) = adj_close_i(t) / adj_close_i(t - L) - 1,   L = 120 trading days

signal_i(t) = 1   if trail_ret_i(t) > 0
            = 0   otherwise
```
`L = 120` is pre-registered and fixed for this sprint — not retuned after
looking at any output (House Rule 5). No deadband, no secondary filter: the
rule is deliberately the simplest mechanical version of itself.

**Per-name volatility targeting:**
```
sigma_i(t) = annualized realized vol of daily log returns of asset i,
             trailing window W = 63 trading days
           = std(log_ret_i, window=W) * sqrt(252)

v = 0.10                      # target annualized vol contribution per active name (10%)
w_max = 0.50                  # per-name weight cap, independent safety rail

raw_weight_i(t) = signal_i(t) * min(v / sigma_i(t), w_max)
```
`v` and `w_max` are illustrative, round, pre-registered numbers — not fit to
this universe or this sample.

**Gross leverage cap (applied across the book each day):**
```
G_max = 2.0   # 200% gross
gross(t) = sum_i |raw_weight_i(t)|
scale(t)  = min(1, G_max / gross(t))   if gross(t) > 0 else 1
weight_i(t) = raw_weight_i(t) * scale(t)
```

**Output — daily target-position vector:**
```
target_position_vector(t+1) = { weight_i(t) for all i in universe }
```
Computed entirely from data through the close of day t (E1). Units are
fraction of NAV per name; the vector is the sprint's sole deliverable.

---

## Data

| source | contents | status |
|--------|----------|--------|
| `data/raw/SPY.parquet`, `HYG.parquet`, `LQD.parquet`, `IEF.parquet` | existing OHLCV via `signals.load.fetch` | present |
| `data/raw/TLT.parquet`, `EFA.parquet`, `EEM.parquet`, `GLD.parquet` | new tickers, same yfinance ingest path | to be fetched in T1 |

- **Source:** yfinance, daily OHLCV + adjusted close, via the existing sole I/O
  boundary `signals.load.fetch` / `signals.load.write_raw` (no new ingest path).
- **Frequency:** daily closes. No intraday data.
- **Adjusted close convention:** `adj_close` is yfinance's back-adjusted total
  return series (splits and dividends folded in retroactively). This is the
  existing convention used throughout the repo (see `signals/pipeline.py`) — it
  is a measurement convention for total return, not a source of predictive
  look-ahead, and is applied identically here.
- **Known biases:** no survivorship bias (all 8 tickers are large, currently-
  listed funds; none have been delisted). Staggered inception (GLD 2004, EEM
  2003, EFA 2001, HYG/LQD 2007 vs SPY 1993) means the early-sample universe is
  smaller than the late-sample universe — this is handled by point-in-time
  inclusion (E5), not backfill.
- **Missing data / corporate actions:** handled identically to the existing
  pipeline (yfinance's adjusted close already reflects splits/dividends; no
  separate corporate-action handling needed for these ETFs).

---

## Success Metrics

No Sharpe, IC, hit-rate, or drawdown threshold (House Rule 1). Metrics here are
purely about whether the construction is correct:

| metric | threshold | gate? |
|--------|-----------|-------|
| No-look-ahead perturbation test | exact match on unperturbed prefix | hard (E1) |
| Per-name realized vol vs target | within 2x of v=10% on average | hard (E2) |
| Gross leverage | ≤ 2.0 every day | hard (E3) |
| Reproducibility across runs | bit-for-bit identical | hard (E4) |
| Point-in-time universe membership | 0 violations | hard (E5) |
| Guardrail statement | present verbatim | mandatory (S1) |

---

## Research Architecture

```
[T1] Universe ingest: extend signals.load to TLT, EFA, EEM, GLD
      -> data/raw/{ticker}.parquet
      |
[T2] Trend signal: trail_ret_i, signal_i (L=120, sign-only, long-only/flat)
      |
[T3] Vol targeting + per-name weight cap + gross leverage cap
      -> target_position_vector(t+1)
      |
[T4] No-look-ahead test (E1) + reproducibility test (E4)
      |
[T5] Point-in-time universe membership check (E5)
      |
[T6] Vol-target tracking check (E2) + leverage-cap check (E3)
      |
[T7] Illustrative visualization (positions, gross/net exposure) — explicitly
     not a performance claim
      |
[T8] S1 guardrail statement + sprint close
```

---

## Risks and Biases

- **No performance claim, so no performance risk to manage here.** The main
  engineering risk is a silent look-ahead or sizing bug masquerading as a
  "reasonable-looking" position vector — E1/E4 exist specifically to catch
  that class of bug mechanically rather than by eyeballing a chart.
- **Vol-of-vol risk in the sizing formula itself**: `v / sigma_i(t)` can spike
  when `sigma_i(t)` is small (e.g. a quiet regime just before a vol shock,
  classically 2017 -> early 2018). `w_max` and `G_max` exist as independent
  safety rails for exactly this failure mode, not as tuned risk parameters.
- **Selection bias risk in the universe.** House Rule 4 exists because it would
  be easy, even unintentionally, to justify keeping or dropping a ticker based
  on how its trend signal "looked." The universe is fixed before T2 runs.
- **Staggered inception is a real test of the point-in-time logic** (E5), not
  just a data-availability footnote — get this wrong and the early-sample book
  silently has fewer effective diversifiers than it appears to.

---

## Out of Scope

- Any IC test, Sharpe/hit-rate claim, or backtest performance threshold
  (House Rule 1 — explicitly not what this sprint validates)
- Transaction costs, slippage, or borrow (no PnL claim is being made, so no
  cost model is needed yet)
- Cross-sectional ranking, risk parity across the whole book, or any signal
  beyond the univariate trailing-return sign
- Tuning `L`, `W`, `v`, `w_max`, or `G_max` within this sprint (House Rule 5 —
  any change is a new pre-registration, not a retune)
- Shorting (long-only/flat per this sprint's explicit design choice)

---

## Dependencies

- `signals/load.py` — existing yfinance ingest boundary, extended with 4 new tickers
- `data/raw/{SPY,HYG,LQD,IEF}.parquet` — existing
- No dependency on the v1–v7.1 HY/IG research programme; this is a fresh,
  independent instrument
