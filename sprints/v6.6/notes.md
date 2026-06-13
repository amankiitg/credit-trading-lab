# Sprint v6.6 — Notes
**Signal: hy_ig (hy_spread − ig_spread, z-scored 252d) | 2007–2026**

---

## T1 — Signal Characterisation vs RV1_A (2026-06-13)

| metric | value |
|--------|-------|
| Pearson corr(z_rv_hy_ig, hy_ig_z252) | **0.252** |
| RV1_A entries with hy_ig \|z\|>2 within ±2d | **26/94 = 27.7%** |
| hy_ig_z252 at RV1_A entries: mean / std | **0.207 / 1.624** |
| hy_ig entries "new" (no RV1_A overlap ±2d) | **660 / 726 = 90.9%** |

**Interpretation:** Low correlation (0.25) and only 28% entry overlap confirm these
are genuinely different signals. When RV1_A fires, hy_ig_z252 is distributed roughly
normally around 0 (mean 0.21) — hy_ig does not agree with RV1_A on most entry dates.
91% of hy_ig candidate entries are on dates where RV1_A never fired. The 68% hit rate
retained by RV1_A under corrected accounting does not transfer to this signal.

Plot: `sprints/v6.6/plots/z_overlap.png`

---

## T2 — Stationarity, OU Half-Life, IC Test — Hard Gate — REVISED (2026-06-13)

### ADF on hy_ig levels (H0: unit root, autolag=AIC)

| window | stat | p-value | 5% cv | verdict |
|--------|------|---------|-------|---------|
| full (4784 obs) | −1.596 | **0.4857** | −2.862 | FAIL TO REJECT |
| pre-2017 (2451 obs) | −2.604 | **0.0921** | −2.863 | FAIL TO REJECT |
| post-2017 (2333 obs) | −0.522 | **0.8876** | −2.863 | FAIL TO REJECT |

C32 FAIL. ADF cannot reject the unit root hypothesis in any of the three windows.

### OU half-life on hy_ig levels

AR(1): `hy_ig[t] = κ·hy_ig[t-1] + c + ε[t]`
κ = 0.996761, half-life = **213.7 trading days (10.2 months)**
C33 FAIL. Half-life exceeds the 90d threshold by 2.4×.

### Gate verdict (original, now superseded): CLOSED on C32/C33

---

## T2 — Revised Gates C32/C33/C36 (2026-06-13)

PRD and TASKS updated: C32 tests Δhy_ig, C33 tests the z-score half-life, C36 added
as IC test. Rationale: P&L formula is `side × Δhy_ig × notional` so raw level
stationarity is not the right gate — first differences and entry-signal predictiveness are.

### C32 — ADF on Δhy_ig (first differences)

stat = −13.32, p = 0.000 → **C32 PASS**. Daily changes are stationary white noise, confirming hy_ig is I(1).

### C33 — OU half-life on hy_ig_z252 (z-score)

κ = 0.9817, half-life = **37.6 trading days** (1.8 months) → **C33 PASS**. The z-score reverts within the 90d threshold.

### C36 — IC test: does entry signal predict direction of Δhy_ig?

726 entry dates where |hy_ig_z252| > 2. For each, `hit = 1` if `sign(z) == sign(hy_ig[t+h] − hy_ig[t])`.

| horizon | n_obs | hit rate | t-stat | verdict |
|---------|-------|----------|--------|---------|
| 5d | 726 | **49.6%** | −0.22 | fail |
| 10d | 726 | **50.7%** | +0.37 | fail |
| 20d | 726 | **49.9%** | −0.07 | fail |

**C36 FAIL.** 0/3 horizons pass. All hit rates are within noise of 50%.

### Gate verdict: **CLOSED on C36**

---

### What C36 failure means

The z-score is stationary (C33 passes, 37.6d half-life) but the signal has ZERO
directional predictive power over actual hy_ig level moves. When z < −2, the
subsequent price change of hy_ig is a coin flip at every tested horizon. The
z-score reverts to zero primarily because the 252-day rolling mean catches up to
the current level — not because the price level moves in the direction of the entry
signal. This is the rolling-window re-centring problem, expressed in a different form:

- For OLS residuals (v6.5): the rolling α re-centred DURING THE HOLD, inflating P&L
- For hy_ig z-score: the rolling mean re-centres after entry, making the z-score
  appear to revert — but the underlying price move is random

The entry signal `z < −2` is identifying "the level is far from its 252-day rolling
mean" — but for an I(1) series this is just drift, not a dislocation that will revert.
The z-score's half-life (37.6d) is the speed at which the window catches up, not the
speed of price mean-reversion.

**The C36 IC test is the definitive gate.** C32 and C33 pass, but C36 catches what
they miss: the entry signal is uninformative about future price direction.

### Plots

- `sprints/v6.6/plots/hyig_zscore.png` — z-score time series with ±2σ entry bands
- `sprints/v6.6/plots/ic_decay.png` — IC hit rate at 5/10/20d horizons (all ≈50%)

### Recommendation for next steps

The hy_ig z-score (rolling demeaned) is not the right entry signal. The issue is
using an I(1) series with a rolling window mean as the reference level.

Alternative approaches that avoid the rolling-window problem:
1. **First-difference z-score**: z-score of Δhy_ig (daily changes) rather than
   the level. This uses stationary data directly. Entry when recent daily changes
   have been extreme (momentum/reversal on the stationary series).
2. **Cointegration residual with a stationary anchor**: find a stationary linear
   combination of hy_ig with another series that is genuinely I(0), not via
   a rolling window. E.g., hy_ig minus a long-run VECM cointegration residual.
3. **Cross-sectional z-score**: at each date, z-score hy_ig relative to a
   cross-section of credit spread pairs (requires more data).

Sprint v6.6 is closed at T2. T3–T7 skipped.

---

### Diagnostic context (original T2, retained for record)

Two additional tests clarify what these failures mean:

**ADF on Δhy_ig (first differences):**
stat = −13.32, p = 0.000 — strongly stationary.
Interpretation: hy_ig is I(1). Daily changes are stationary; the level has a unit root.
The slow drift visible in the level (~−0.45 in 2007 → −0.51 in 2010 → −0.32 in 2026)
is a genuine secular trend, not a stationary process.

**ADF on hy_ig_z252 (the z-score itself):**
stat = −3.91, p = 0.0019 — stationary.
OU half-life on z-score: κ = 0.9817, **half-life = 37.6 trading days (1.8 months)**.

This is the key distinction: the z-score IS stationary with a 37.6d half-life (which
would pass C33 if it had been tested). But the raw level that generates the z-score is
not.

### Why C32/C33 fail on levels but the z-score passes

The rolling 252d mean removes the secular trend from the signal — it acts as a
detrending filter. The z-score is stationary by construction because the rolling mean
tracks the I(1) level. When the z-score "reverts," it can be due to:
(a) The level actually rising/falling toward its equilibrium (real mean reversion)
(b) The rolling mean catching up to the current level (statistical re-centring)

This is structurally analogous to the rolling OLS intercept α — the same concern that
disqualified RV1_A in v6.5.

### Critical difference from the OLS case

However, there is a materially important difference from the OLS residual P&L problem:

For OLS signals (RV1_A/RV2_A/RV3_A): the P&L formula was
`side × (rv[exit] − rv[entry]) × notional`
where `rv[t] = hy[t] − α[t] − β[t]·ig[t]`. The α and β changed DURING THE HOLD,
so the measured "change in rv" included model re-centring that happened after entry.
That was the source of the $486k–$13M accounting artifact.

For hy_ig: the P&L formula is
`side × (hy_ig[exit] − hy_ig[entry]) × notional`
The raw level change is computed at fixed entry/exit dates. The rolling mean does NOT
enter the P&L formula at all. Whether the 252d rolling mean re-centres during the hold
is irrelevant to whether the position made money — the position makes money only if
`hy_ig[exit] > hy_ig[entry]` (for a LONG). This is a genuine price move question.

**The model re-centring problem from v6.5 does NOT apply to hy_ig P&L.**
The stationarity failure does affect signal quality (the entry rule may not reliably
identify reversion episodes), but it does NOT create the same accounting artifact
that disqualified the OLS signals.

### What the level non-stationarity actually means for the signal

The entry z < −2 identifies dates where hy_ig is 2σ below its trailing 1-year mean.
If hy_ig is I(1), this may be because:
- The spread genuinely dislocated and will revert (hypothesis correct)
- The spread drifted to a new level and the 252d window hasn't caught up (false signal)

The secular drift in the 2026 post-COVID period (hy_ig near −0.32, much less negative
than the historical mean) illustrates this: in 2024–2026, a short hy_ig signal
(z > +2) would fire as the spread normalises post-COVID, possibly confusing secular
normalisation with a tradeable short opportunity.

### Recommendation for next sprint

The gate as written closes this sprint. But C32/C33 were designed to guard against
the OLS α problem — which doesn't exist here. For a subsequent sprint:

1. Replace C32 with: ADF on Δhy_ig (first differences) p < 0.05 — this tests whether
   daily changes are tradeable, which is what the P&L formula uses. **This passes.**

2. Replace C33 with: OU half-life on hy_ig_z252 (the z-score) ≤ 90d — this tests
   how quickly the entry signal reverts to neutral, which is economically relevant
   for sizing the stop and exit. **This passes at 37.6d.**

3. Add an IC test (not in C32/C33): does z < −2 predict positive Δhy_ig over the
   next 5/10/20 days? This directly tests whether the entry rule picks up real
   price reversion rather than just signal reversion.

These changes would let a revised v6.6 or v6.7 proceed to T3 and the backtest.

### Sprint state: T1 [x], T2 [x], T3–T7 skipped per gate protocol.

---

## Level plot

`sprints/v6.6/plots/hyig_level.png` — hy_ig level + rolling mean 2007–2026 with
rolling std in lower panel. Visible secular drift is the root cause of the ADF failure.
