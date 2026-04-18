# Sprint v2 — Notes

## 2026-04-17 — Task V1: Scaffold CMake project + pybind11/Catch2 deps

**Status:** Done

### Build targets
- `credit` — static library (`libcredit.a`)
- `credit_tests` — Catch2 v3.5.4 runner (2/2 smoke tests pass)
- `pycredit` — pybind11 v2.11.1 module (`pycredit.hello()` → `"ok"`)

### Compiler flags
- Release: `-O3 -DNDEBUG -Wall -Wextra -Wpedantic -Werror` — 0 warnings
- Debug: ASan/UBSan flags (`-fsanitize=address,undefined`) wired via
  `credit_sanitizers` INTERFACE target, auto-enabled when
  `CMAKE_BUILD_TYPE=Debug`

### Platform workarounds
1. **CMake** installed via `pip install cmake` (not on system PATH).
2. **pybind11 FindPython**: v2.11.1 defaults to legacy `FindPythonInterp`
   which picked up system pyenv 3.6. Fixed with `PYBIND11_FINDPYTHON=ON`
   + `-DPython_EXECUTABLE=<venv>/bin/python3`.
3. **Broken Apple CLT libc++**: `/Library/Developer/CommandLineTools/usr/include/c++/v1/`
   contains only a 3-file legacy stub that shadows the real SDK libc++.
   Fixed by detecting the stub and injecting
   `-nostdinc++ -isystem <sdk>/usr/include/c++/v1` globally.
4. **ASan runtime**: compiles + links at exit 0, but runtime hangs on
   Apple Silicon with CommandLineTools-only install. Requires full Xcode
   for runtime validation. CMake plumbing is correct.

### Key numbers
| metric | value |
|---|---|
| Release build time (cold, incl. Catch2) | ~45s |
| ctest pass rate | 2/2 (100%) |
| Python smoke | `pycredit.hello()` → `"ok"` |
| Compile warnings | 0 |

## 2026-04-18 — Task V2: Day-count + interpolation policy classes

**Status:** Done

### Files created
- `cpp/include/credit/date.hpp` — minimal `Date{year, month, day}` struct with Julian-day serial for arithmetic
- `cpp/include/credit/daycount.hpp` — `Act360`, `Act365F`, `Thirty360` stateless structs
- `cpp/include/credit/interp.hpp` — `LinearYield`, `LogLinearDF`, `PiecewiseConstantHazard` stateless structs
- `cpp/tests/test_daycount.cpp` — 15-row reference table (exact to 1e-15)
- `cpp/tests/test_interp.cpp` — knot round-trip, between-knot behavior, extrapolation

### Design decisions
- **Custom `Date` over `std::chrono::year_month_day`**: the chrono calendar types are C++20; we target C++17.
  Julian Day Number formula handles all the calendar math in 3 lines.
- **All policy classes are stateless structs with static methods**: no constructors, no members, no allocations.
  The compiler can inline everything — zero overhead.
- **Flat extrapolation outside the grid**: conservative choice. If you ask for a rate at t=50y when your curve only goes to 30y, you get the 30y rate — not a wild extrapolation.

### Bug caught during testing
- Rows 4–5 of the day-count table had wrong 30/360 reference values for Jan 31→Mar 1.
  The ISDA 30/360 rule clamps d1=31→30 but does NOT set d2=1→30, so the result is 31/360, not 30/360.
  Fixed in the reference table.

### Key numbers
| metric | value |
|---|---|
| Incremental build time | ~3s (2 new .cpp files) |
| ctest pass rate | 11/11 (100%) |
| Day-count tolerance | exact to 1e-15 |
| Interp knot round-trip tolerance | exact to 1e-12 |
| Compile warnings | 0 |

## 2026-04-18 — Task V3: Discount curve bootstrap (C12)

**Status:** Done

### Files created/modified
- `cpp/include/credit/discount_curve.hpp` — `DiscountCurve<Interp, DC>` template class with Newton bootstrap
- `cpp/tests/test_discount_curve.cpp` — C12 knot reprice + monotonicity + zero rate tests
- `cpp/tests/ref/discount_curve_knots.csv` — FRED DGS snapshot from 2025-01-02

### Design decisions
- **Newton's method for bootstrap** — naive "solve directly" only works when
  tenors are consecutive integers (1y, 2y, 3y). For gaps (3y→5y), intermediate
  coupon dates (4y) have DFs that depend on the unknown DF(5y) through
  LogLinearDF interpolation. Newton solves `f(x) = price(x) - 1 = 0` with
  analytic derivatives. Converges in 2–3 iterations.
- **Template on Interp + DC policies** — same class works with different
  interpolation/day-count combos without code duplication.
- **CREDIT_TEST_REF_DIR compile definition** — absolute path to test reference
  data, so tests work regardless of working directory.

### Bugs caught during testing
- **First attempt**: direct bootstrap gave exact results for consecutive tenors
  (1, 2, 3) but failed for gaps (5, 7, 10, 20, 30). Root cause: DF at
  intermediate coupon dates was extrapolated from last known knot instead of
  interpolated to the current unknown. Newton's method fixed this.
- **ctest escaping**: test names with `(0, 1]` brackets broke ctest's filter.
  Renamed to avoid special chars.

### Key numbers
| metric | value |
|---|---|
| Par yield round-trip error | 0.0 (exact to machine precision) |
| Max DF at 30y | 0.2563 |
| Monotonicity | strictly decreasing over [0, 30y] at 0.5y steps |
| Newton iterations per tenor | 2–3 |
| ctest pass rate | 14/14 (100%) |
| Compile warnings | 0 |

## 2026-04-18 — Task V4: Fixed-coupon bond pricing (C14)

**Status:** Done

### Files created/modified
- `cpp/include/credit/date.hpp` — added `<=`, `>`, `>=` operators + `add_months()` for schedule generation
- `cpp/include/credit/rootfind.hpp` — template Newton + Brent root-finders (zero-overhead, no `std::function`)
- `cpp/include/credit/bond.hpp` — `FixedBond` struct, `DayCountType` enum, `coupon_schedule()`, `BondPricer`
- `cpp/src/bond.cpp` — `dirty_at_yield`, `dirty_deriv`, `accrued`, `ytm` implementations
- `cpp/tests/test_bond.cpp` — 6 test cases: dirty-price cross-check, YTM round-trip, accrued sanity, zero-coupon spot-check, at-par identity, clean=dirty-accrued
- `cpp/tests/ref/bond_ytm_vectors.csv` — 10 bonds (3 Treasuries + 7 corporates incl. zero-coupon, deep discount, high premium)

### Design decisions
- **`DayCountType` enum + runtime dispatch** rather than templating `BondPricer` on day count. The bond is a plain data struct; the pricer dispatches via `year_fraction(DayCountType, d1, d2)` which calls the static policy-class methods. This keeps the bond serializable and avoids template explosion.
- **Template only `dirty(bond, curve)` on the curve's Interp/DC policies** — the rest of BondPricer lives in bond.cpp.
- **Newton's method initial guess = coupon rate** — converges in ~3 iterations for most bonds. Brent fallback on [1e-6, 1.0] catches edge cases (zero-coupon where Newton's initial guess is far from truth).
- **Reference vectors generated independently in Python** — dirty prices computed from reference YTMs using an independent Python implementation of 30/360 and the dirty-price formula. C++ matches Python to within 1e-8. YTM round-trip error < 1e-10 bp for all 10 bonds (far below the 1.0 bp C14 threshold).

### Analytical spot-checks
1. **Zero-coupon bond**: `dirty = 100 / (1+y)^T` verified exact to 1e-10.
2. **At-par bond on coupon date**: `dirty = 100.0` verified exact to 1e-8.
3. **Accrued interest**: verified non-negative and ≤ one coupon for all 10 bonds.

### Key numbers
| metric | value |
|---|---|
| Dirty price vs Python reference | all 10 within 1e-8 |
| YTM round-trip error | all 10 < 1e-10 bp (threshold: 1.0 bp) |
| Accrued interest | non-negative, ≤ one coupon for all |
| Newton convergence | ~3 iters typical, Brent fallback not triggered |
| ctest pass rate | 20/20 (100%) |
| Compile warnings | 0 |
