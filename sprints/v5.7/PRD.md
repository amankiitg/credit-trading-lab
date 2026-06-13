# Sprint v5.7 — hy_ig Signal: Tier 1 Validation (Fresh Hypothesis)

## Context: Why v5.7 and Not a Tier 2 Sprint

Sprint v6.5 disqualified all three Tier 1 signals (RV1_A, RV2_A, RV3_A) under
fixed-entry accounting. The rolling OLS intercept α was booking model re-centring
as tradeable P&L. Sprint v6.5 also identified Option B: use `hy_ig = hy_spread −
ig_spread` (the raw HY/IG log-spread difference) z-scored over 252 days, with no
OLS, no rolling β, no α — and therefore no model drift.

**This is a fresh Tier 1 hypothesis, not a purified version of RV1_A.** The key
differences:

| dimension | RV1_A | hy_ig (Option B) |
|-----------|-------|-----------------|
| P&L driver | `Δhy − β(t)·Δig` (rolling β) | `Δhy − Δig` (β fixed at 1, α fixed at 0) |
| Entry signal | z-score of `hy − β(t)·ig − α(t)` | z-score of `hy − ig` |
| Model drift | 57% of gross P&L (fatal) | impossible by construction |
| Rate exposure | partial hedge via β(t) ≈ 0.9–1.1 | unhedged — see §Rate Exposure |

The 68% hit rate retained by RV1_A under corrected accounting does **not** transfer
mechanically — the entry z-score operates on a different series, fires on different
dates, and has different duration exposure. This sprint treats hy_ig as a fresh signal
that must earn its way through all M1–M8 gates independently.

---

## Economic Hypothesis

HY and IG credit ETFs (HYG and LQD) are both corporate bond indices benchmarked
against Treasuries. Their log price ratios (`hy_spread = ln(HYG/IEF)` and
`ig_spread = ln(LQD/IEF)`) share a common Treasury exposure that partially cancels
in the difference: `hy_ig = hy_spread − ig_spread = ln(HYG/LQD)`. What remains is
roughly the credit quality spread — HY premia over IG.

The hypothesis is that this quality spread is mean-reverting around a slowly drifting
level. When HY cheapens sharply relative to IG (hy_ig_z252 << −2), credit stress is
amplified in the HY segment beyond what IG implies — a dislocation that should revert
as sentiment normalises. The same logic holds in reverse (HY rich relative to IG).

**Why this might be a better anchor than RV1_A's OLS residual:**
The OLS rolling window was fitting a cross-sectional β that varied from 0.8 to 1.2
across regimes. `hy_ig` assumes β=1 permanently, which is only correct if HYG and LQD
have equal rate sensitivity — they don't (see §Rate Exposure). But it avoids the
intercept re-centring problem entirely. The question is whether the directional signal
in the quality spread survives the rate exposure and cost.

**What would falsify this hypothesis:**
- hy_ig is not mean-reverting (ADF fails, OU half-life > 120d) → the z-score entry
  has no statistical basis
- The implicit short-duration position (see §Rate Exposure) explains the P&L
  entirely — the signal is really a rates bet, not a credit quality signal
- Corrected-engine Sharpe < 0.40 → insufficient magnitude to trade

---

## Rate Exposure: The Unhedged Duration Mismatch

**This is the primary new risk not present in RV1_A.**

HYG has effective duration ~3.5 years. LQD has effective duration ~8.5 years.
A dollar-equal long HYG / short LQD position has:

```
net_DV01 = DV01_HYG − DV01_LQD
         ≈ (3.5y × $1M × 1bp) − (8.5y × $1M × 1bp)
         ≈ $350/bp − $850/bp = −$500/bp
```

This is SHORT duration by ~$500/bp — a bet that rates RISE. Over a 30-day hold,
US 10-year rates move ~30bp per month (annualised vol ~100bp). That implies
`$500 × 30 = $15,000` of rate P&L per trade, or `±$15k` of noise vs expected
credit-quality-spread P&L.

Across 2007–2026, the rate cycle went 5% → 0.5% → 5%. Dollar-equal hy_ig trades
entered during the rate-decline phase (2008–2020) have a systematic tailwind from
the short-duration position; trades entered during rate-rise phases have a headwind.
**This is the same trap that disqualified RV2_A and RV3_A, expressed indirectly.**

**Two honest paths — one must be chosen before the backtest runs:**

**Path A — DV01-neutral sizing:**
Size LQD to match HYG's DV01:
```
N_LQD = N_HYG × (dur_HYG / dur_LQD) ≈ $1M × (3.5 / 8.5) ≈ $412k
```
P&L formula: `side × (Δhy_spread × N_HYG − Δig_spread × N_LQD)`.
This removes the rate bet but changes the signal economics — the IG leg is only 41%
the size of the HY leg. More exposure to HY direction, less to quality spread.

**Path B — Dollar-equal with explicit rate attribution:**
Keep $1M / $1M sizing. Decompose P&L into:
```
quality_pnl = side × Δhy_ig × notional × duration_neutral_fraction
rate_pnl    = residual (from parallel rate move × net_DV01)
```
Run the corrected-engine backtest dollar-equal, then report what fraction of gross
P&L is attributable to parallel rate moves. If rate_pnl dominates quality_pnl,
the signal is a rates bet, not a credit signal.

**Recommendation:** Start with Path B (dollar-equal, explicit attribution) so the
baseline backtest is comparable to v5. If rate attribution is large (>30% of gross),
rerun under Path A and compare. Pre-register the choice in T3 before seeing the
backtest result.

---

## Falsification Criteria

Pre-registered. Continuing C25–C31 numbering from v5.

| ID | Criterion | Pass threshold | Outcome if fail |
|----|-----------|---------------|----------------|
| C32 | ADF test on hy_ig levels — full sample, both halves | p < 0.05 in all three windows | Signal has no statistical basis for entry rule; stop sprint |
| C33 | OU half-life on hy_ig | ≤ 90 trading days full-sample | Hold-to-mean-reversion is too slow relative to v5 stop logic |
| C34 | Rate attribution ≤ 30% of gross P&L (Path B) | ≤ 30% | Signal is predominantly a rates bet; switch to Path A or stop |
| M1ʹ | Corrected-engine net Sharpe ≥ 0.40 | ≥ 0.40 | Signal does not clear minimum viability; do not proceed to Tier 2 |
| M2ʹ | Hit rate ≥ 65% | ≥ 65% | — |
| M3ʹ | No single trade > 25% of total net P&L | < 25% | — |
| M4ʹ | Parameter grid: Sharpe > 0 in ≥ 60% of 27 cells | ≥ 60% | — |
| M5ʹ | Both subperiods (2007–2016, 2017–2026) Sharpe > 0 | both > 0 | — |
| M6ʹ | Per-trade Sharpe vs random p95 | > random p95 | — |
| C35 | Incremental Sharpe vs passive buy-hold hy_ig position (no entry timing) | ΔS > 0, CI lower > 0 via block bootstrap | Entry timing adds no value over passive; signal is passive carry |

**Gate structure:**
- C32/C33 must pass before running any backtest (T2 is a hard gate for T4)
- C34 must be evaluated before backtest results are interpreted (T3 pre-registers the sizing choice)
- M1ʹ is the headline; failing M1ʹ stops the sprint regardless of other metrics

---

## Signal Definition

**Raw spread:**
```
hy_ig[t] = hy_spread[t] − ig_spread[t]
         = ln(HYG[t] / IEF[t]) − ln(LQD[t] / IEF[t])
         = ln(HYG[t] / LQD[t])
```
Column `hy_ig` already in `data/processed/features.parquet`.

**Entry signal (z-score over trailing window):**
```
mu_t    = mean(hy_ig, window=252)
sigma_t = std(hy_ig, window=252)
z_t     = (hy_ig[t] − mu_t) / sigma_t
```
Column `hy_ig_z252` already in `data/processed/features.parquet`.

**Trade rules (same thresholds as v5 defaults):**
- Entry LONG:  z < −2.0 (HY cheap vs IG on a 1-year basis)
- Entry SHORT: z > +2.0
- Exit:        |z| < 0.5
- Stop:        |z| > 4.0 (stop-out on further dislocation)
- Fill lag:    1 day (signal on t, fill on t+1)

**P&L formula (Path B, dollar-equal):**
```
gross[trade] = side × (hy_ig[exit_fill] − hy_ig[entry_fill]) × notional
daily MTM:   = side × (hy_ig[d] − hy_ig[d−1]) × notional − borrow_per_day
```
No OLS, no hedge ratio, no model drift.

**Cost parameters (same as v5):**
- Half-spread: 1.5bp of notional
- Slippage: 0.5bp of notional
- Borrow: 0.40%/yr accrued daily

**Note on StrategySpec:** `hy_ig` may need a new pair entry in `ab_test.py` if it
does not already exist as a valid pair key. The engine already supports any residual
series — just pass the hy_ig series and a unit hedge ratio (β=1) or bypass the hedge
ratio column entirely.

---

## Signal Characterisation vs RV1_A

Before running the backtest, characterise whether hy_ig and RV1_A are materially
different signals:
- Overlap in entry dates: what fraction of hy_ig entries coincide with RV1_A entries
  (within ±2 days)?
- Correlation of z-scores: `corr(z_rv_hy_ig, hy_ig_z252)`
- Distribution of RV1_A z-score at hy_ig entry dates (and vice versa)

If >80% of hy_ig entries coincide with RV1_A entries, the signals are effectively the
same and the comparison is largely cosmetic. If <40% overlap, they are genuinely
different signals with independent value.

---

## Data

| source | contents | columns used |
|--------|----------|-------------|
| `data/processed/features.parquet` | 4784 rows, 2007–2026 | `hy_ig`, `hy_ig_z252`, `hy_spread`, `ig_spread` |
| `data/raw/credit_market_data.parquet` | FRED rates | `dgs10`, `dgs2` (for DV01 parallel-shift test) |
| `data/benchmarks/random_baseline.parquet` | 3000 random paths | C35 random p95 |

**Known biases:**
- `hy_ig` is derived from ETF prices (HYG/LQD), not OAS. Bid-ask and management
  expense ratios create a small systematic drag not captured in the borrow cost alone.
  The 1.5bp half-spread partially compensates but ETF tracking error is not modelled.
- Duration values (HYG ~3.5y, LQD ~8.5y) are approximations from published fact sheets
  and change over time. The DV01 audit in T3 uses fixed values; actual rate sensitivity
  varies across the sample.
- Survivorship: HYG and LQD existed continuously over this period. No survivorship bias.
- Rate cycle dominance: 2007–2026 includes the full zero-rate cycle. Any short-duration
  position benefits from the 2008–2020 secular decline. This is a bias in the sample,
  not a look-ahead issue. Subperiod splits (T5) will expose it.

---

## Success Metrics

| metric | threshold | gate? |
|--------|-----------|-------|
| ADF p-value on hy_ig (full, both halves) | < 0.05 | hard (C32) |
| OU half-life | ≤ 90d | hard (C33) |
| Rate attribution share of gross P&L | ≤ 30% | soft → rerun Path A if fails (C34) |
| Net Sharpe (corrected engine) | ≥ 0.40 | hard (M1ʹ) |
| Hit rate | ≥ 65% | soft |
| Max single-trade P&L share | < 25% | soft (M3ʹ) |
| Parameter grid coverage | ≥ 60% of 27 cells Sharpe > 0 | soft (M4ʹ) |
| Subperiod Sharpe | both halves > 0 | soft (M5ʹ) |
| Per-trade Sharpe vs random p95 | > p95 | soft (M6ʹ) |
| Incremental Sharpe vs passive hy_ig | ΔS > 0, CI lower > 0 | hard (C35) |
| Entry overlap with RV1_A | reported, no threshold | diagnostic only |

---

## Research Architecture

```
features.parquet (hy_ig, hy_ig_z252)
      ↓
[T1] Signal characterisation vs RV1_A (overlap, z-score correlation)
      ↓
[T2] Stationarity gate: ADF + OU half-life on hy_ig (C32, C33)
      ↓  ← HARD GATE: stop if C32 or C33 fails
[T3] Rate exposure audit: DV01 quantification + pre-register Path A/B
      ↓
[T4] Corrected-engine backtest: M1ʹ–M6ʹ scorecard
      ↓  ← HARD GATE: stop if M1ʹ fails
[T5] Subperiod + parameter grid (M4ʹ, M5ʹ)
      ↓
[T6] Bootstrap vs passive buy-hold hy_ig (C35)
      ↓
[T7] Notebook notebooks/05_7_hyig_validation.ipynb + sprint close
```

---

## Risks and Biases

- **Rate exposure dominance**: the implicit short-duration position may explain most
  P&L over this sample. The DV01 audit (T3) and rate attribution (T4) are the primary
  guards. If rate P&L > 30% of gross, the result is suspect even if M1ʹ passes.
- **Same secular sample as RV2/RV3**: the 2007–2026 period contains a full rate cycle
  that was the undoing of RV2/RV3. hy_ig has a smaller rate sensitivity (β_eff ≈ 1 vs
  up to 34 for RV2_A), but it is not zero. Subperiod analysis must split pre-2017 /
  post-2017 to separate the zero-rate era from the rate-normalisation era.
- **β=1 assumption**: the "true" hedge ratio between HYG and LQD is not exactly 1 in
  any OLS sense. If the true ratio drifts (e.g., as HYG changes its duration profile),
  hy_ig may be non-stationary over long sub-samples. Check rolling 252-day ADF.
- **Passive carry**: holding a long HYG / short LQD position passively also earns a
  credit quality spread. C35 (incremental Sharpe vs passive) guards against the strategy
  being passive carry dressed up as a timing signal.
- **Multiple comparison**: this is the fourth signal variant tested in Tier 1 (after
  RV1_A, RV2_A, RV3_A were disqualified). A Bonferroni correction on the M1ʹ threshold
  would require Sharpe ≥ 0.46 at the same family-wise 5% level. This sprint uses the
  same 0.40 threshold for comparability but the multiple-testing concern should be noted
  in the sprint close.

---

## Out of Scope

- DV01-exact sizing using the C++ pricer (use published fact-sheet duration as proxy)
- Kalman filter or dynamic β estimation (explicitly disqualified in v5.5)
- Re-testing RV2_A or RV3_A (both disqualified; rate exposure is too large to repair)
- Tier 2 work (attribution, scenario risk, paper trading) until M1ʹ and C35 are confirmed
- Intraday or tick data

---

## Dependencies

- `data/processed/features.parquet` — `hy_ig`, `hy_ig_z252` (confirmed present)
- `data/raw/credit_market_data.parquet` — `dgs10`, `dgs2` for DV01 parallel-shift test
- `data/benchmarks/random_baseline.parquet` — random p95 baseline for M6ʹ/C35
- `backtest/engine.py` — pass `hy_ig` series as the residual; set hedge_ratio=1 or
  bypass (no OLS call needed)
- `sprints/v5/PRD.md` — C25–C31 definitions (C32–C35 continue that numbering)
- `sprints/v5.6/signal_selection.md` — M1–M8 definitions (M1ʹ–M6ʹ mirror these)
- `sprints/v6.5/correction_summary.md` — withdrawal statement; v5.7 is the response
