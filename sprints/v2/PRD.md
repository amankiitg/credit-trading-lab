# Sprint v2 — C++ Credit Pricing Engine

Weeks 2–5. Sprint 1 (`sprint-v1`) delivered statistical features + a
FRED-backed credit/rates frame. Sprint 2 builds the **pricing layer**
that converts raw market data into risk-exact dollar sensitivities
(DV01, CR01). Sprint 3 will consume those sensitivities to construct
the duration-neutral HY/IG relative-value trade.

This sprint is pure infrastructure. There is no P&L, no signal, and no
hypothesis about future returns. The hypothesis we pre-register here is
**numerical** — that we can reproduce industry-standard pricing within
tight tolerances. Everything is testable against reference vectors
before any strategy sees the numbers.

## Overview

Build a C++17 library (`libcredit`) with Python bindings (`pycredit`)
that prices:

1. Fixed-coupon corporate bonds (clean, dirty, accrued, YTM, DV01,
   key-rate DV01, OAS, Z-spread, spread convexity).
2. Single-name CDS contracts via the **ISDA CDS Standard Model**
   (hazard bootstrap, survival curve, risky PV, par spread, MTM,
   CS01/CR01).
3. Discount and survival curves with pluggable day-count and
   interpolation policies.

Python bindings accept numpy arrays and deliver **≥ 10,000 bond
pricings/sec** on a single M-series core so Sprint 3 can sweep
portfolios without a dedicated batch queue.

## Economic Hypothesis

**The Sprint 3 RV thesis — "HY/IG dislocations mean-revert net of
duration and rates" — is only defensible if HY and IG are hedged to
equal DV01 and CR01.** Without a correct pricer, the
`rv_hy_ig_residual` column from Sprint 1 mixes three things we want to
keep separate:

- genuine HY/IG credit spread mean-reversion (the alpha),
- duration mismatch between HYG (≈ 4 yr) and LQD (≈ 8 yr) (rates beta),
- credit beta mismatch from different names/ratings (systematic credit).

A correct pricer gives Sprint 3 the weights that zero out the first
two, isolating the residual. The present sprint does not test that the
alpha exists — it only builds the tool.

## Falsification Criteria

Pre-registered. Each threshold is checked by a Catch2 or pytest test
bound to a fixed reference vector. All must pass before Sprint 2 is
closed and Sprint 3 opens.

- **C12 — Discount-curve round-trip.** The discount curve bootstrapped
  from FRED DGS1/2/3/5/7/10/20/30 on any business day must reprice the
  input par yields within **1e-10 (absolute)** at knot points and
  preserve monotone-positive discount factors over `[0, 30y]`.
- **C13 — CDS par spread vs ISDA reference.** For the ISDA CDS
  Standard Model reference vectors (fixed flat-hazard and piecewise-
  hazard test cases, provided in `tests/ref/isda_cds_vectors.csv`), our
  par spread matches within **0.5 bps absolute** at all quoted
  maturities (6m, 1y, 2y, 3y, 5y, 7y, 10y).
- **C14 — Bond YTM accuracy.** For a 10-bond reference set (US
  Treasury 2y/5y/10y + 7 corporates with known dirty price / YTM from
  Bloomberg or textbook), our YTM matches within **1.0 bp absolute**.
- **C15 — DV01 analytic vs FD.** Analytical DV01 and a two-sided
  finite-difference DV01 (±1 bp) agree within **1% relative** for every
  bond in the reference set. Same test for CR01 on the CDS reference
  set.
- **C16 — Throughput.** Batch pricing 10,000 bonds via the pybind11
  numpy entry point completes in **≤ 1.00 s** on a single core
  (M-series, Release build). CDS batch pricing 10,000 contracts in
  **≤ 2.00 s**.
- **C17 — Clean compile + zero warnings.** `cmake --build` under
  `-Wall -Wextra -Wpedantic -Werror` finishes with exit 0. All Catch2
  unit tests pass, all pytest integration tests pass. No sanitizer
  errors under `-fsanitize=address,undefined`.

C12–C15 are **accuracy** criteria (correctness). C16 is a **performance**
criterion (usability). C17 is a **hygiene** criterion (maintainability).
If any criterion fails, that specific engine is not released to Sprint
3 and the dependent bond/CDS path is quarantined.

## Signal Definition

This sprint builds *no* signals. It builds formulas. They are spelled
out here so the Catch2 tests have something unambiguous to assert
against.

### Day-count conventions

Policy-class templates instantiated at compile time:

- `Act360` — `yf = days/360`
- `Act365F` — `yf = days/365`
- `Thirty360` — 30/360 ISDA (used for corporate coupons)

### Interpolation

Policy-class templates over tenor grids:

- `LinearYield` — linear in continuously-compounded zero yield `z(t)`
- `LogLinearDF` — linear in `log(P(0,t))` (equivalent to piecewise
  flat forwards)
- `PiecewiseConstantHazard` — piecewise constant `λ(t)` on quoted
  tenors (ISDA default)

### Discount curve

Inputs: FRED DGS tenors `T = {1, 2, 3, 5, 7, 10, 20, 30}` years with
par yields `y_T` in percent.

1. For each tenor convert par yield to zero rate by bootstrapping
   from shortest to longest; on the first tenor (1y) zero rate = par
   rate; on later tenors, solve for zero such that fixed coupon
   `c = y_T` prices to par:

       par = Σ_{τ∈coupon_dates(T)} c·Δτ·P(0,τ) + P(0,T)

2. Store `P(0, T)` and interpolate with `LogLinearDF` between knots.

Output type: `DiscountCurve<LogLinearDF, Act365F>`.

### Fixed-coupon bond

Given notional `N`, coupon `c` (annual, in percent), frequency `f`
(2 for semi-annual US corporates), schedule of coupon dates
`t_1 < … < t_M = T`, and year fractions `Δ_i` from day-count:

    Dirty(y) = Σ_{i=1}^{M} (c·Δ_i·N) / (1+y/f)^(f·t_i)
              + N / (1+y/f)^(f·T)

    Accrued  = c·N · (days_since_last_coupon) / (days_in_current_period)

    Clean    = Dirty − Accrued

- **YTM** — Newton on `Dirty(y) − P_mkt = 0`, bracketed fallback to
  Brent on `[1e-6, 1.0]`. Max 50 iters, tol 1e-12.
- **DV01 (analytic)** — `−∂Dirty/∂y · 1e-4`. Closed form from the
  derivative of the dirty-price formula.
- **Key-rate DV01** — shift DGS tenor `k` by 1 bp, re-bootstrap
  discount curve, reprice. Vector of length 8.
- **Z-spread** — constant `s` added to zero rates of the discount curve
  such that model dirty price = market dirty price. Newton on `s`.
- **OAS** — identical to Z-spread under zero-optionality assumption
  (our corporates are treated as bullet for this sprint; callable
  schedules are out of scope).
- **Spread convexity** — `(∂²Dirty/∂s²) / Dirty`, computed via central
  finite difference on the Z-spread.

### CDS — ISDA standard model

Contract: buyer pays fixed running coupon `s` quarterly on an IMM
schedule; receives `LGD = (1 − R)·N` on first default within `[0, T]`.

Hazard rate `λ(t)` piecewise constant between tenor knots
`0 < T_1 < … < T_K`. Survival `Q(t) = exp(−∫_0^t λ(u) du)`.

**Premium leg PV** (pay side, risky):

    PV_prem(s) = s·[ Σ_i Δ_i · P(0, t_i) · Q(t_i)
                   + Σ_i ∫_{t_{i−1}}^{t_i} (u − t_{i−1})·P(0, u) · (−dQ(u)) ]

The second term is accrual-on-default, discretized with the ISDA
subdivision (integrate on a daily grid between coupon dates; closed
form available under constant λ and flat `log P`).

**Protection leg PV:**

    PV_prot = LGD · ∫_0^T P(0, u) · (−dQ(u))

Under piecewise-constant `λ` and log-linear DF, this has a closed
form over each segment; we implement it.

**Par spread:**

    s* = PV_prot(1) / PV_prem(s=1)   (numerator uses LGD; denominator is
                                      the annuity RPV01 with s=1)

**MTM** (running coupon `s_c`, recovery `R`):

    MTM_{buyer} = PV_prot − s_c · RPV01

**CS01 / CR01:**

- CS01 — MTM sensitivity to a 1 bp parallel shift in the hazard term
  structure (recalibrated from a 1 bp shift in par spreads).
- CR01 — same semantic but specifically the par-spread shift (Sprint 2
  treats CS01 = CR01 since we only have one credit curve per name).

**Bootstrap:**

Given a quoted par-spread term structure `{(T_k, s_k)}`, solve
sequentially for `λ_k` on `(T_{k−1}, T_k]` such that the CDS contract
at tenor `T_k` prices to zero. Newton on `λ_k` with analytic
derivative; bracket fallback to Brent.

## Data

**Existing inputs** (from `sprint-v1`):

| artifact | purpose |
|---|---|
| `data/raw/credit_market_data.parquet` | DGS term structure (discount curve) + BAML OAS series (CDS sanity) |
| `data/raw/HYG.parquet`, `LQD.parquet` | ETF time series (unused by the pricer itself; Sprint 3 consumes) |

**New reference data** (committed, small, in-repo):

| path | size | purpose |
|---|---|---|
| `cpp/tests/ref/isda_cds_vectors.csv` | ~20 rows | ISDA CDS Standard Model reference vectors for C13 |
| `cpp/tests/ref/bond_ytm_vectors.csv` | 10 rows | Bond reference set (Treasury + corps) for C14/C15 |
| `cpp/tests/ref/discount_curve_knots.csv` | ~8 rows | A snapshot of DGS1…DGS30 on a fixed date for C12 |

No new vendor data. No new network I/O. All reference vectors are
static and versioned with the code so tests are hermetic.

**Known biases / caveats:**

- FRED Treasury CMT is par yields on constant-maturity points; we
  bootstrap to zero under the assumption that DGS reflects a clean
  par bond. This is standard and acceptable.
- ISDA CDS Standard Model nominally uses a LIBOR/swap curve for
  discounting; we substitute our Treasury curve. For the reference-
  vector test (C13) we use the discount curve distributed with the
  vectors so the substitution does not affect C13.
- Callable bonds are priced as bullets. The OAS we compute is thus
  Z-spread; true OAS with option-adjusted tree is out of scope.

## Success Metrics

Passing C12–C17 is sufficient. There is no separate statistical
success metric because there is no strategy.

**Summary table** (printed by the final notebook cell):

| metric | target | source |
|---|---|---|
| Discount-curve knot reprice error | ≤ 1e-10 | C12 |
| CDS par spread vs ISDA | ≤ 0.5 bp | C13 |
| Bond YTM vs reference | ≤ 1.0 bp | C14 |
| DV01 analytic vs FD | ≤ 1% rel | C15 |
| Bond batch throughput | ≥ 10,000 /s | C16 |
| CDS batch throughput | ≥ 5,000 /s | C16 |
| Compile warnings | 0 | C17 |
| Sanitizer errors | 0 | C17 |

## Research Architecture

```
cpp/
  CMakeLists.txt                   # top-level; builds libcredit, tests, pybind module
  include/credit/
    daycount.hpp                   # Act360, Act365F, Thirty360 policy classes
    interp.hpp                     # LinearYield, LogLinearDF, PiecewiseConstantHazard
    curve.hpp                      # template<Interp, DC> Curve<...>
    discount_curve.hpp             # DiscountCurve + bootstrap from par yields
    survival_curve.hpp             # SurvivalCurve + hazard bootstrap
    bond.hpp                       # FixedBond, BondPricer (dirty, clean, YTM, DV01, Z)
    cds.hpp                        # CDSContract, CDSPricer (par, MTM, CS01)
    rootfind.hpp                   # Newton, Brent
    schedule.hpp                   # IMM dates, coupon schedule generation
    common.hpp                     # Date, Period, aliases; uses std::chrono + std::optional
  src/
    discount_curve.cpp
    survival_curve.cpp
    bond.cpp
    cds.cpp
    schedule.cpp
  tests/
    CMakeLists.txt                 # Catch2 v3 via FetchContent
    test_daycount.cpp
    test_discount_curve.cpp        # C12
    test_bond.cpp                  # C14, C15 analytic-vs-FD
    test_cds.cpp                   # C13 ISDA vectors + hazard bootstrap
    test_perf.cpp                  # C16 microbench
    ref/                           # static reference vectors (CSV)
bindings/python/
  CMakeLists.txt
  pycredit.cpp                     # pybind11 module; numpy batch entry points
python/credit/
  __init__.py                      # thin wrapper, docstrings, type hints
tests/
  test_cpp_parity.py               # pycredit vs Catch2 numbers
  test_batch_throughput.py         # C16 on the python side
  test_ref_vectors.py              # C13/C14 via Python entrypoints
notebooks/
  02_pricer_validation.ipynb       # end-to-end demo: curve, bond, CDS, sensitivities
```

**Data flow:**

1. Python reads `data/raw/credit_market_data.parquet`, extracts DGS
   columns on a chosen date.
2. Calls `pycredit.bootstrap_discount(tenors, par_yields)` → C++
   returns a `DiscountCurve` handle.
3. Prices an array of bonds or CDSs in batch via numpy arrays.
4. Returns a numpy recarray with (price, dv01, cr01, ...) columns.

No I/O inside C++ except reading the small reference-vector CSVs in
tests. All production data enters through pybind11.

**Build modes:**

- `Debug` — `-O0 -g -fsanitize=address,undefined`, all tests run under
  sanitizers (C17).
- `Release` — `-O3 -DNDEBUG`, used for throughput benchmarks (C16).

## Risks & Biases

- **Model risk — ISDA approximations.** The ISDA CDS Standard Model
  discretizes the accrual-on-default integral on a daily grid. Our
  closed-form-per-segment alternative is faster but can drift from the
  reference if the hazard curve is sharply kinked. Mitigation: C13
  tests both smooth and kinked reference vectors.
- **Day-count landmines.** Bond coupon conventions (30/360 ISDA vs
  30E/360 vs ACT/ACT) are the single most common source of pricing
  bugs. Mitigation: day-count is a policy class; tests enumerate all
  three and fail if a counted interval drifts by a day.
- **Newton non-convergence on deep-discount or deep-premium bonds.**
  Mitigation: Brent fallback on `[1e-6, 1.0]`; test includes a 10%
  coupon bond at 50 premium and a 0% coupon at 5 to stress both
  directions.
- **pybind11 ownership bugs.** Curve handles returned to Python are
  `shared_ptr`-owned; reckless `.reset()` or capturing references in
  Python can double-free. Mitigation: bindings only expose opaque
  `shared_ptr` handles, never raw pointers; ASan covers lifetime
  errors.
- **Perf regression.** 10K/s is tight for CDS; a naive `std::vector`-
  heavy implementation blows the budget. Mitigation: core inner loops
  take pre-sized arrays, avoid heap in the hot path; benchmark test
  fails below threshold.
- **Reference-vector sourcing.** ISDA publishes canonical test cases
  in their open-source model release; we transcribe a subset and
  document the source in the CSV header. If a test case is
  mis-transcribed, C13 is reporting the wrong thing. Mitigation: cross-
  check with QuantLib's CDS pricer on the same inputs in a one-off
  offline script (result pasted into the CSV header as a comment).
- **Platform drift.** `-O3` behavior can differ between Apple clang
  and GCC; throughput on CI may be lower than on M-series. Mitigation:
  C16 is asserted in Release on local hardware; CI runs the correctness
  suite only (C12–C15, C17).

## Out of Scope

- Any strategy P&L, signal, or portfolio construction.
- Callable / putable / convertible bond pricing.
- Amortizing or floating-rate bonds.
- Counterparty XVA (CVA/DVA/FVA).
- CDS index pricing (CDX.HY, CDX.IG) — single-name only.
- Tranche pricing / CDO math.
- Options on bonds or CDS (no tree, no SABR, no SV).
- Negative hazard rates / negative recovery (guarded against but not
  supported).
- Real-time repricing loop; this sprint is batch only.

## Dependencies

**New:**
- `CMake ≥ 3.20`
- `pybind11 ≥ 2.11` (FetchContent)
- `Catch2 v3` (FetchContent)
- C++17 compiler: Apple clang ≥ 14 or GCC ≥ 11
- `numpy ≥ 1.24` (already in env)
- `pytest ≥ 7` (already in env)

**Existing (unchanged):** `pandas`, `pyarrow` — used only to feed
curve data into the pricer from Python.

**Prior sprint outputs:** `sprint-v1` tag. Specifically
`data/raw/credit_market_data.parquet` for the discount curve at a real
market date, and `data/processed/features.parquet` as a read-only
reference.

**No new vendor subscriptions, no API keys.** The ISDA reference
vectors and bond reference set are committed as small CSVs.
