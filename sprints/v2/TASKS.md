# Sprint v2 — Tasks

Ten atomic tasks, each 5–15 minutes of focused work. Every build /
pricer task is paired with a test. Randomness is pinned via fixed
reference vectors committed under `cpp/tests/ref/`. Status starts at
`[ ]`; mark `[x]` as each lands. All tests cumulative: the final task
requires the full C12–C17 checklist green.

Pre-req: `sprint-v1` tag checked out (features.parquet 50 cols,
`credit_market_data.parquet` 15 cols, 25/25 tests green).

See `PRD.md` §Falsification Criteria for C12–C17 definitions.

---

- [x] **Task V1: Scaffold CMake project + pybind11/Catch2 deps**
  - Acceptance: `cpp/CMakeLists.txt` builds three targets (`credit`
    static lib, `credit_tests` Catch2 runner, `pycredit` pybind11
    module) via `FetchContent` for Catch2 v3 and pybind11 2.11.
    `cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release && cmake --build
    build -j` exits 0 with `-Wall -Wextra -Wpedantic -Werror`. A smoke
    test `test_smoke.cpp` asserts `1 + 1 == 2` and runs via `ctest`.
    `python -c "import pycredit; pycredit.hello()"` prints "ok".
  - Files: `cpp/CMakeLists.txt`, `cpp/tests/CMakeLists.txt`,
    `cpp/tests/test_smoke.cpp`, `bindings/python/CMakeLists.txt`,
    `bindings/python/pycredit.cpp`, `python/credit/__init__.py`,
    `.gitignore` (add `build/`).
  - Validation: fails if build warns, if ASan/UBSan cannot be enabled
    in Debug, or if the pybind11 module cannot be imported from the
    project venv.

- [x] **Task V2: Day-count + interpolation policy classes**
  - Acceptance: `cpp/include/credit/daycount.hpp` exposes `Act360`,
    `Act365F`, `Thirty360` as stateless structs with static
    `year_fraction(Date, Date)`. `cpp/include/credit/interp.hpp`
    exposes `LinearYield`, `LogLinearDF`, `PiecewiseConstantHazard`
    with `interp(xs, ys, x)`. `test_daycount.cpp` checks a 15-row
    table of (d1, d2, Act360, Act365F, Thirty360) exact to the
    nanosecond; `test_interp.cpp` round-trips every interpolator on
    its own knot points to within 1e-12.
  - Files: `cpp/include/credit/daycount.hpp`,
    `cpp/include/credit/interp.hpp`,
    `cpp/tests/test_daycount.cpp`, `cpp/tests/test_interp.cpp`.
  - Validation: fails if any policy class has non-static state, if a
    day-count result drifts by more than 0 days on the reference
    table, or if an interpolator fails to be exact on its own knots.

- [x] **Task V3: Discount curve bootstrap from FRED par yields (C12)**
  - Acceptance: `DiscountCurve<LogLinearDF, Act365F>` bootstraps from
    `{(T, y_T)}` pairs where `T ∈ {1,2,3,5,7,10,20,30}y` and `y_T` is
    in decimal (e.g. 0.0425). Bootstrap proceeds shortest→longest,
    solving for the zero rate that makes a par-coupon bond of maturity
    `T` and annual coupon `y_T` price to par. `test_discount_curve.cpp`
    loads `cpp/tests/ref/discount_curve_knots.csv` (one real FRED
    snapshot date), bootstraps, and asserts the model par yield at
    every input tenor matches input to within **1e-10**. Discount
    factors monotone ∈ (0, 1] over `[0, 30y]`.
  - Files: `cpp/include/credit/discount_curve.hpp`,
    `cpp/src/discount_curve.cpp`, `cpp/tests/test_discount_curve.cpp`,
    `cpp/tests/ref/discount_curve_knots.csv`.
  - Validation: fails C12 if any knot reprice exceeds 1e-10, if any
    discount factor is non-positive or > 1, or if the bootstrap
    diverges for the supplied snapshot.

- [x] **Task V4: Fixed-coupon bond — dirty, clean, accrued, YTM (C14)**
  - Acceptance: `FixedBond` holds `{notional, coupon, frequency,
    issue_date, maturity_date, day_count}` (30/360 ISDA default).
    `BondPricer::dirty(bond, curve)` returns model dirty price;
    `::accrued(bond, settle)` returns accrued interest;
    `::clean = dirty − accrued`. `::ytm(bond, dirty_price)` uses
    Newton with Brent fallback on `[1e-6, 1.0]`, tol 1e-12, max 50
    iters. `test_bond.cpp` loads `cpp/tests/ref/bond_ytm_vectors.csv`
    (10 bonds: Treasury 2y/5y/10y + 7 corporates with known dirty
    price + reference YTM) and asserts YTM error < **1.0 bp** per bond.
  - Files: `cpp/include/credit/bond.hpp`, `cpp/src/bond.cpp`,
    `cpp/include/credit/rootfind.hpp`, `cpp/tests/test_bond.cpp`,
    `cpp/tests/ref/bond_ytm_vectors.csv`.
  - Validation: fails C14 if any reference YTM drifts > 1 bp; fails if
    Newton diverges on any supplied bond (Brent must catch it); fails
    if accrued is negative or > one coupon period.

- [x] **Task V5: DV01, key-rate DV01, Z-spread, convexity (C15)**
  - Acceptance: `BondPricer::dv01(bond, curve)` analytic; compare to
    two-sided FD (±1 bp parallel yield shift). `::krdv01(bond, curve)`
    returns an 8-vector, one per DGS tenor, via curve re-bootstrap
    with that tenor shifted. `::zspread(bond, curve, mkt_dirty)`
    Newton-solves the constant zero-rate shift. `::spread_convexity`
    via central FD on Z-spread. `test_bond.cpp` adds assertions:
    analytic DV01 vs FD agree within **1% relative** for every
    reference bond; Z-spread of a bond priced from the curve round-
    trips to 0 ± 1e-8; key-rate DV01 summed across tenors ≈ parallel
    DV01 within 2% (caveat: not exact because the curve re-bootstrap
    is non-linear).
  - Files: `cpp/include/credit/bond.hpp` (extended),
    `cpp/src/bond.cpp` (extended), `cpp/tests/test_bond.cpp`
    (extended).
  - Validation: fails C15 if analytic/FD DV01 diverges > 1%, if
    Z-spread round-trip fails, or if key-rate DV01 sum drifts more
    than 2% from parallel.

- [x] **Task V6: CDS contract + hazard-bootstrap survival curve (C13)**
  - Acceptance: `CDSContract` holds `{notional, coupon, recovery,
    effective_date, maturity_date, pay_freq=quarterly}`. `SurvivalCurve`
    bootstraps piecewise-constant `λ_k` from a par-spread term
    structure `{(T_k, s_k)}` by Newton-solving each segment sequentially
    to price the contract at `T_k` to zero. `CDSPricer::par_spread`
    returns `PV_prot / RPV01`. `test_cds.cpp` loads
    `cpp/tests/ref/isda_cds_vectors.csv` (both flat-hazard and
    piecewise test cases at 6m/1y/2y/3y/5y/7y/10y) and asserts every
    par spread matches ISDA reference within **0.5 bps absolute**.
  - Files: `cpp/include/credit/survival_curve.hpp`,
    `cpp/src/survival_curve.cpp`, `cpp/include/credit/cds.hpp`,
    `cpp/src/cds.cpp`, `cpp/include/credit/schedule.hpp`,
    `cpp/src/schedule.cpp`, `cpp/tests/test_cds.cpp`,
    `cpp/tests/ref/isda_cds_vectors.csv`.
  - Validation: fails C13 if any reference par spread drifts > 0.5
    bps; fails if hazard bootstrap produces a negative λ on any
    segment (must throw); fails if accrual-on-default closed form
    diverges from a 10-bucket-per-day numerical integral by > 0.1
    bps on the reference set.

- [ ] **Task V7: CDS MTM + CS01/CR01**
  - Acceptance: `CDSPricer::mtm(contract, survival, discount)` returns
    buyer-side MTM = `PV_prot − s_c · RPV01`. `::cs01` is MTM change
    per 1 bp parallel shift in par-spread term structure (recalibrates
    survival curve and reprices). `::cr01` identical (single credit
    curve per name in this sprint). `test_cds.cpp` extends: analytic
    CS01 (partial derivative wrt s) vs FD CS01 agree within **1%
    relative** on the reference set. Consistency: par-spread contract
    MTM ≈ 0 within 1e-8 on its own curve.
  - Files: `cpp/include/credit/cds.hpp` (extended),
    `cpp/src/cds.cpp` (extended), `cpp/tests/test_cds.cpp`
    (extended).
  - Validation: fails if analytic / FD CS01 disagree > 1%; fails if
    par-spread contract MTM is not zero on its own curve; fails if
    MTM sign flips when LGD sign flips (sanity).

- [ ] **Task V8: pybind11 batch API + numpy zero-copy**
  - Acceptance: `pycredit` exposes:
    `bootstrap_discount(tenors: np.ndarray, par_yields: np.ndarray) →
    DiscountCurve`, `price_bonds(curve, bond_frame: np.recarray) →
    np.recarray`, `bootstrap_survival(tenors, spreads, recovery,
    discount) → SurvivalCurve`, `price_cds(survival, discount,
    contracts: np.recarray) → np.recarray`. Batch functions take
    contiguous `float64` arrays, release the GIL (`py::gil_scoped_release`),
    and return numpy recarrays with columns (`price`, `dv01`, `dv01_fd`,
    `accrued`, `ytm`) for bonds and (`mtm`, `par_spread`, `cs01`,
    `rpv01`) for CDS. Curve objects are opaque `shared_ptr` handles.
  - Files: `bindings/python/pycredit.cpp`,
    `python/credit/__init__.py`, `python/credit/_types.py` (recarray
    dtype aliases).
  - Validation: fails if `pycredit.price_bonds` allocates the output
    inside a Python loop (must be one C++ loop with GIL released);
    fails if numpy dtypes are not `float64`; fails if curve handles
    leak under repeated Python GC cycles (ASan in Debug).

- [ ] **Task V9: Throughput benchmark + parity tests (C16)**
  - Acceptance: `tests/test_batch_throughput.py::test_bond_10k_per_sec`
    builds a 10,000-row bond frame (random coupons, frequencies,
    maturities 1y–30y) and asserts batch pricing completes in **≤ 1.00
    s** in a pytest-level timer (Release build).
    `::test_cds_10k` asserts ≤ 2.00 s for 10,000 CDS. Python-side
    parity: `tests/test_cpp_parity.py` prices 20 bonds and 20 CDS via
    `pycredit.price_bonds` and asserts numbers match the Catch2 output
    dumped to `cpp/tests/ref/parity_dump.csv` within 1e-10.
  - Files: `tests/test_batch_throughput.py`, `tests/test_cpp_parity.py`,
    `cpp/tests/ref/parity_dump.csv`, `cpp/tests/test_perf.cpp`.
  - Validation: fails C16 if throughput misses either target on the
    developer machine; fails parity if any Python/C++ number drifts
    > 1e-10 on identical inputs.

- [ ] **Task V10: Sprint validation — notebook + ISDA reference check + close**
  - Acceptance: `notebooks/02_pricer_validation.ipynb` runs top-to-
    bottom without errors and demonstrates: (a) bootstrap discount
    curve on the latest FRED date, plot yield + discount factor; (b)
    price a 10y 5% corporate, show clean/dirty/DV01/krDV01; (c)
    bootstrap a synthetic HY CDS curve from `synth_cds_hy`, plot
    hazard + survival; (d) compute CS01 for a 5y contract; (e) print
    a final C12–C17 checklist mirroring the Sprint 1 convention.
    `sprints/v2/plots/{01_discount_curve,02_hazard_survival,03_bond_sensitivities}.png`
    saved. `sprints/v2/notes.md` records wall-clock throughput,
    compile flags used, and the git SHA of the commit. Full test
    suite green: Catch2 `ctest --output-on-failure` + pytest (Sprint 1
    regression 25/25 still green + Sprint 2 new ≥ 15 passing).
  - Files: `notebooks/02_pricer_validation.ipynb`, `sprints/v2/notes.md`,
    `sprints/v2/plots/01_discount_curve.png`,
    `sprints/v2/plots/02_hazard_survival.png`,
    `sprints/v2/plots/03_bond_sensitivities.png`.
  - Validation: fails sprint if any of C12–C17 fail, if the notebook
    errors on re-run, if any Sprint 1 test regresses, or if the
    checklist cell omits any of C12–C17. Promote to tag `sprint-v2`
    only when all green.
