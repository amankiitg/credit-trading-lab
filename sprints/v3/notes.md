# Sprint v3 — Notes

## V1 — Regime classifiers

Implemented `signals/regimes.py` with `vol_regime`, `equity_regime`,
`equity_credit_lag`. All three trailing-only (no `center=True`,
no future data).

### V1 own validation (the task's bar)

- Pure functions, no I/O ✓
- No NaN inside post-warmup (252 days) for any classifier ✓
  - vol_regime: 4532/4532
  - equity_regime: 4532/4532
  - equity_credit_lag: 4532/4532

### V2 / C18 outlook (testing this in V2)

Sample 2007-04-11 → 2026-04-15, 4784 rows, post-warmup 4532.

| classifier | coverage | top label | top share | C18(b) <70% |
|---|---|---|---|---|
| vol_regime | 100% | low | 61.8% | PASS |
| equity_regime | 100% | bull | 74.1% | **FAIL** |
| equity_credit_lag | 100% | neither | 85.1% | **FAIL** |

Decision: **keep PRD-as-written, let C18 fail honestly** (same posture
as v1 — falsification criteria are pre-registered, we don't move
goalposts).

Why each fails:
- `equity_regime`: 19-year sample is structurally bull-heavy; 63d
  cumulative SPY return > 0 on 74% of days.
- `equity_credit_lag`: noise floor 0.15 is below the natural
  correlation noise level (~0.22 for n=21), but max-over-11-lags
  doesn't push enough days above it because SPY-vs-Δhy_spread is
  genuinely weak day-by-day; 85% are labeled `neither`.

These are real properties of the data, not bugs in the classifier.

## V2 — Regime tests

`tests/test_regimes.py` encodes C18(a)–(d). 10 tests; **8 pass, 2 fail**:

- `test_c18_non_degenerate[equity_regime]` — top share 74.1% > 70%
- `test_c18_non_degenerate[equity_credit_lag]` — top share 85.1% > 70%

Test code is correct — failures reflect actual data, pre-registered
under option-3 (let C18 fail honestly).

## V3 — OLS rolling hedge

`signals/rv_signals.py::ols_hedge(y, x, window=126)` — computes β
from rolling means, var, cov over a 126-day trailing window. Pure
function, no I/O.

Validation on the 3 RV families (post-warmup = 378+):

| pair | NaN post | β std | resid var | raw y var | reduce? |
|---|---|---|---|---|---|
| rv_hy_ig_ols | 0 | 0.9473 | 5.4e-4 | 2.5e-2 | OK (47×) |
| rv_credit_rates_ols | 0 | 5.7459 | 9.7e-4 | 2.5e-2 | OK (26×) |
| rv_xterm_ols | 0 | 7.5711 | 5.9e-4 | 6.4e-3 | OK (11×) |

β std non-zero everywhere → OLS actually rolling. Credit/rates and
x-term β are ~100× scale because dgs is divided by 100 (decimal),
so β_decimal ≈ 100·β_percent.

## V4 — Kalman filter hedge

`signals/rv_signals.py::kalman_hedge(y, x, Q=1e-5, init_window=63)`.

First implemented as a 1-state filter (β only, α held constant from
OLS init). Residual variance ratios vs OLS were 1.87 / 2.90 / 5.59 —
two of three failed the V4 ≤2× gate. Root cause: holding α constant
forces a fixed-mean fit, and over 19 years the spread mean drifts.

Fixed by upgrading to a **2-state Kalman** (α and β both random-walk
states, Q·I covariance), which is the standard pairs-trading
formulation and matches the PRD's `α_t + β_t·x_t` notation.

Validation on the 3 RV families (post-warmup = 378+):

| pair | NaN | inf | mean Δβ vs OLS | k_resid var | o_resid var | ratio |
|---|---|---|---|---|---|---|
| rv_hy_ig | 0 | 0 | 0.7448 | 2.0e-5 | 5.4e-4 | 0.04 |
| rv_credit_rates | 0 | 0 | 4.3502 | 4.4e-5 | 9.7e-4 | 0.05 |
| rv_xterm | 0 | 0 | 5.8106 | 3.0e-5 | 5.9e-4 | 0.05 |

All three differ from OLS, no NaN/inf, residual variance ~20× smaller
than OLS (well under 2× cap). The 2-state filter is highly adaptive —
whether the residual is *tradeable* (slow enough mean-reversion) is
judged by V7's half-life test.

