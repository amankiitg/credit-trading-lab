# Sprint v7.1 — NAV Wedge: Signal Construction (Tier 1, Programme v7)

## Context: Why v7 and Not Another v6.x Sub-Sprint

The v1–v6.6 programme (HY/IG ETF spread family — RV1_A, RV2_A, RV3_A, hy_ig level,
hy_ig first-difference) is closed. No deployable signal survived honest accounting
and IC testing. The closing finding (README §Research Conclusion) is explicit: every
variant tested was a transform of the same `ln(HYG/LQD)` series, and the failures
were structural (rolling-OLS model drift; I(1) levels masquerading as mean-reverting
via rolling z-scores) rather than parameter mis-specification. Re-parameterising that
series further is not new research.

**v7 starts a new signal family on a different economic mechanism: the ETF
premium/discount to NAV ("NAV wedge"), not the credit-quality spread.** This is the
first v7 sprint, scoped narrowly to data feasibility and signal construction only —
no backtest, no IC test, no tradeability claim. Those are explicitly out of scope
here and reserved for v7.2, contingent on this sprint's gates passing.

## v7 House Rules

Instituted directly from the four retained findings of the v6.6 closure
(README §Research Conclusion). These apply to this sprint and are intended to apply
to the rest of the v7 programme:

1. **Pre-register every transformation parameter before looking at any result.**
   The window length, threshold, etc. are fixed at design time and not retuned
   after seeing output. (A 21-day-only effect surfaced in a post-v6.6 spot-check
   on Δhy_ig and vanished at 63d/126d — exactly the failure mode this rule exists
   to prevent.)
2. **No regression anywhere.** No OLS, no Kalman filter, no rolling β, no fitted
   intercept. Any construction step with a parameter that can drift during a
   holding period is banned outright — this is what disqualified RV1_A/RV2_A/RV3_A
   in v6.5 (rolling-α re-centring booked as P&L).
3. **Stationarity is necessary but never sufficient for a tradeability claim.**
   ADF / OU half-life results must be paired with an out-of-sample IC test before
   any signal is called predictive. (This is what hy_ig_z252 passed and C36 caught
   in v6.6 — a stationary z-score with zero directional content.)
4. **Data-availability and data-quality gates run before any signal construction.**
   If the data needed to test a hypothesis is not retrievable or not clean, the
   programme documents that finding and stops. It does not improvise a proxy.

---

## Economic Hypothesis

HYG and LQD are open-end ETFs whose secondary-market price is kept close to the
NAV of their underlying bond baskets by authorized-participant (AP) creation/
redemption arbitrage. For corporate bond ETFs this arbitrage is friction-bound:
the underlying bonds trade OTC, are less liquid than the ETF itself, and are
priced by an end-of-day pricing service that can lag real-time conditions, while
the ETF trades continuously on an exchange with continuous price discovery. In
stress, the two can decouple — HYG and LQD both traded at documented discounts to
NAV in March 2020 as bond-market liquidity dried up while ETF trading continued
uninterrupted; the ETF price was arguably the more current signal, and NAV the
stale one.

The wedge `close/NAV − 1` captures this premium/discount directly. The hypothesis:
extreme dislocations in the wedge reflect a temporary AP-arbitrage gap (liquidity
friction), not new information about the bonds' fair value, and should close as
creation/redemption flow and bond pricing catch up.

**Why this is a different mechanism from the v1–v6.6 family, not a re-parameterisation:**
hy_ig (`ln(HYG/LQD)`) is a bet on relative corporate credit quality. The NAV wedge is a
bet on fund-structure liquidity friction — it can move in either ETF independently of
the other, is not a credit-quality statement, and has a distinct causal mechanism (AP
arbitrage speed, bond-market liquidity) with a different counterparty: liquidity-
constrained sellers in the underlying bond market during stress, who must accept ETF-
implied pricing instead of (stale) NAV-implied pricing.

**What would falsify this hypothesis** (deferred to v7.2, stated here for the record):
the wedge does not predict its own subsequent direction (IC test fails, analogous to
C36); or the wedge's stationarity is itself a rolling-window artifact rather than real
AP-arbitrage mean reversion.

---

## Falsification Criteria

Pre-registered. New ID series (`G0`, `S1`) — this is a new programme generation, not
a continuation of the v1–v6.6 `C`/`M` numbering.

| ID | Criterion | Pass threshold | Outcome if fail |
|-----|-----------|----------------|------------------|
| G0a | Daily NAV history retrievable for both HYG (iShares product 239565) and LQD (iShares product 239566) | ≥ 5 years (≥ 1260 trading days), both tickers | **Signal is not testable on free data. Document the finding and stop the programme here — no proxy NAV, no synthetic construction.** |
| G0b | Clean date alignment between NAV series and `data/raw/{HYG,LQD}.parquet` close prices | Lag-correlation of daily returns (NAV vs close, lags −2..+2) peaks at lag 0 for both tickers | NAV series has a systematic date-shift (timezone / as-of-date convention). Attempt one corrective realignment with documented rationale and retest; if still failing, NAV series is unusable — stop. |
| G0c | NAV reflects end-of-day striking, not an intraday indicative value (IIV/INAV) or stale T-1 print | Spot-check NAV against an independently sourced EOD reference on ≥ 3 dates (must include one date inside the 2020-03 stress window) — match within rounding | Wrong series was retrieved (e.g. IIV instead of official NAV). Fix the data source; do not proceed to signal construction until resolved. |
| S1a | Wedge sign/magnitude sanity vs the 2020-03 and 2022 stress episodes | Informational — no numeric pass threshold. Report sign and magnitude at the trough of each episode for both tickers (4 numbers) and state explicitly whether it matches the "discount during liquidity stress" prior. | Does not stop the sprint. A mismatch is itself a finding to document (e.g. if 2022 — a rate-driven, not liquidity-driven, drawdown — shows no comparable wedge response, that is consistent with the hypothesis, not against it). |
| S1b | Guardrail statement, not a test | N/A — must be written verbatim in `notes.md`: **"z_wedge stationarity, if observed, is not evidence of tradeability. No IC test, no backtest, and no Sharpe/hit-rate claim is in scope for v7.1."** | N/A |

**Gate structure:** G0a → G0b → G0c are a strict sequential hard gate. If any fails,
the sprint stops and the failure is the deliverable (same protocol as v6.6's C36
closure). S1a and S1b are mandatory but non-gating — they must appear in `notes.md`
regardless of the G0 outcome (if G0 passes far enough to construct the wedge).

---

## Signal Definition

**Wedge (no fitted parameters):**
```
wedge_t = market_close_t / nav_t − 1
```
Computed independently for HYG and LQD. `market_close_t` is the existing `close`
column in `data/raw/HYG.parquet` / `data/raw/LQD.parquet`. `nav_t` is the new series
retrieved in T1.

**z-score (trailing window, pre-registered, fixed for the sprint):**
```
mu_t    = mean(wedge, window=63)
sigma_t = std(wedge, window=63)
z_wedge_t = (wedge_t − mu_t) / sigma_t
```
Window = 63 trading days (~1 quarter), chosen before any result is observed.
**This window is not tuned within this sprint.** A future sprint may pre-register a
different window as a separate, explicitly logged design decision — never as a
retune of this one after seeing v7.1 or v7.2 results.

No regression, no OLS, no Kalman filter, no rolling β, no intercept anywhere in this
construction — wedge and z_wedge are ratios and rolling moments only, per House Rule 2.

---

## Data

| source | contents | status |
|--------|----------|--------|
| `data/raw/HYG.parquet`, `data/raw/LQD.parquet` | daily OHLCV, 2007-04-11 → 2026-04-15, 4784 rows each | existing |
| iShares product 239565 (HYG) NAV history | daily NAV | **to be retrieved — feasibility is G0a, not assumed** |
| iShares product 239566 (LQD) NAV history | daily NAV | **to be retrieved — feasibility is G0a, not assumed** |

**Known biases / risks:**
- Free retail NAV history exports are frequently shallow (iShares' own interactive
  chart UI often exposes a much shorter window than the full fund history). The 5-year
  G0a threshold is a real risk to this sprint, not a formality — if it fails, that is
  the result, and the documented conclusion is "not testable on free data."
- NAV is computed by the fund accountant from a bond pricing service, which can mark
  illiquid bonds with a lag. This staleness is *part of the economic hypothesis*
  (it is the source of the wedge), not a defect to be filtered out.
- Possible NAV restatement: if iShares ever republishes a corrected historical NAV
  (rare, but documented for some bond funds around March 2020), decide and record
  whether the point-in-time (as-originally-published) or restated value is used —
  point-in-time is the default per general look-ahead discipline.
- No survivorship bias: HYG and LQD both existed continuously over the full sample.

---

## Success Metrics

This sprint has no Sharpe / IC / hit-rate metrics — those are out of scope (House
Rule 3, deferred to v7.2). Metrics here are feasibility and construction-quality only:

| metric | threshold | gate? |
|--------|-----------|-------|
| NAV trading days retrieved, each ticker | ≥ 1260 (5y) | hard (G0a) |
| Lag-correlation argmax (NAV return vs close return) | lag = 0, both tickers | hard (G0b) |
| EOD spot-check match, ≥ 3 dates incl. one in 2020-03 | match within rounding | hard (G0c) |
| wedge / z_wedge: no NaN/inf, monotonic unique date index | 0 violations | hard (data hygiene) |
| z_wedge rolling window | exactly 63d, unchanged from pre-registration | hard (house rule audit) |
| Stress-episode sign/magnitude (2020-03, 2022) | reported, no threshold | informational (S1a) |

---

## Research Architecture

```
[T1] NAV data-availability probe (HYG 239565, LQD 239566)          → G0a
      ↓  HARD GATE — stop + document if G0a fails
[T2] Date-alignment check vs data/raw/{HYG,LQD}.parquet             → G0b
      ↓  HARD GATE — stop + document if G0b fails (after one corrective attempt)
[T3] End-of-day striking spot-check                                 → G0c
      ↓  HARD GATE — stop + document if G0c fails
[T4] Persist merged NAV + close dataset, hygiene assertions
      ↓
[T5] Construct wedge_t and z_wedge_t (63d, pre-registered, no regression)
      ↓
[T6] Leakage / look-ahead check (rolling window alignment, NAV point-in-time)
      ↓
[T7] S1a — stress-episode sign/magnitude sanity check (2020-03, 2022)
      ↓
[T8] S1b guardrail statement + sprint close
```

---

## Risks and Biases

- **G0a is the dominant risk to this entire sprint.** If 5 years of clean daily NAV
  is not freely retrievable, the sprint stops at T1 and the documented finding is
  "not testable on free data" — this is a valid and complete outcome, not a failure
  of execution.
- **Vendor cost risk:** if NAV is only obtainable from a paid vendor, that decision
  (pay vs. stop) must be made explicitly before T2, not assumed.
- **Look-ahead via restatement:** if iShares historical NAV was ever corrected after
  initial publication, using the restated series would leak information not available
  at the time. Default to point-in-time values; document if this cannot be confirmed.
- **Multiple-comparison context:** this is the fifth signal family attempted in this
  research programme (after RV1_A, RV2_A, RV3_A, hy_ig). No statistical test is run in
  this sprint, so there is no Sharpe-inflation risk yet — but the count should be
  carried forward into v7.2's significance thresholds.
- **Mechanism overlap with stress, not normal times:** the hypothesis is strongest
  during liquidity events and may have little or no signal in calm regimes. This is a
  question for v7.2's IC test (does it work outside 2020-03-style episodes?), not
  something to resolve here.

---

## Out of Scope

- Backtest, IC test, Sharpe / hit-rate / drawdown metrics (v7.2, contingent on G0a–c)
- Any regression: OLS, Kalman filter, rolling β, fitted intercept (House Rule 2 — banned outright)
- Tuning the 63-day window within this sprint (any change is a new pre-registration, not a retune)
- DV01 / duration hedging analysis (wedge is a within-ticker ratio, not a cross-instrument spread; defer if v7.2 needs it)
- Production / real-time NAV ingestion — historical research data probe only
- Re-testing or extending the v1–v6.6 hy_ig family

---

## Dependencies

- `data/raw/HYG.parquet`, `data/raw/LQD.parquet` — existing close prices (confirmed present)
- New external source: iShares NAV history for products 239565 / 239566 — retrievability
  unconfirmed, this is exactly what T1/G0a determines
- `sprints/v6.6/notes.md`, `README.md` §Research Conclusion — source of the four retained
  findings that motivate the v7 House Rules above
