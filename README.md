# Credit Trading Lab

Research platform for credit relative-value trading strategies. Combines a Python data pipeline with a C++17 pricing engine to produce risk-exact sensitivities (DV01, CS01, Z-spread) for corporate bonds and single-name CDS.

## Architecture

```
credit-trading-lab/
  cpp/                  C++17 pricing library (libcredit)
    include/credit/       headers: bond, cds, discount/survival curves, day-count, interpolation
    src/                  implementations
    tests/                Catch2 test suite + reference vectors
  bindings/python/      pybind11 bridge (pycredit module)
  python/credit/        Python package wrapping pycredit
  signals/              spread signal generators (ETF-based)
  data/                 raw + processed data (parquet)
  notebooks/            validation notebooks
  sprints/              sprint PRDs, tasks, notes, plots
  tests/                Python test suite (pytest)
```

## Sprint Progress

### Sprint 1 — Data Pipeline + Spread Signals
ETF-derived credit spread signals (HYG/LQD/IEF), FRED par yield data, rolling z-scores, and Monte Carlo baselines. 25/25 tests green.

### Sprint 2 — C++ Pricing Engine (in progress)
C++17 library with Python bindings for fixed-income and credit derivative pricing:

| Component | Status | Key Tolerance |
|---|---|---|
| Discount curve bootstrap (C12) | Done | par yield round-trip < 1e-10 |
| CDS survival curve / par spread (C13) | Done | vs ISDA reference < 0.5 bps |
| Fixed bond pricing + YTM (C14) | Done | YTM error < 1.0 bp |
| DV01, key-rate DV01, Z-spread (C15) | Done | analytic vs FD DV01 < 1% |
| CDS MTM, CS01/CR01 | Done | analytic vs FD CS01 < 1% |
| pybind11 batch API | Pending | |
| Throughput benchmark (C16) | Pending | 10k bonds/sec |
| Validation notebook | Pending | |

## Building

### Prerequisites
- C++17 compiler (Clang 14+ or GCC 11+)
- CMake 3.20+
- Python 3.10+ with numpy

### Build
```bash
cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

### Run Tests
```bash
# C++ tests
cd build && ctest --output-on-failure

# Python tests
pytest tests/ -v
```

### Python Bindings
```bash
cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release \
  -DPython_EXECUTABLE=$(which python3)
cmake --build build -j
python -c "import pycredit; pycredit.hello()"
```

## C++ Library Overview

**Curves**
- `DiscountCurve<Interp, DC>` — bootstraps from FRED par yields, supports parallel/key-rate shifts
- `SurvivalCurve` — piecewise-constant hazard rate, bootstraps from CDS par spreads

**Instruments**
- `FixedBond` — coupon schedule, dirty/clean price, accrued interest, YTM (Newton + Brent)
- `CDSContract` — notional, coupon, recovery, quarterly payment frequency

**Pricers**
- `BondPricer` — dirty, clean, accrued, YTM, DV01 (analytic + FD), key-rate DV01, Z-spread, spread convexity
- `CDSPricer` — par spread, RPV01, MTM, CS01, CR01

**Policies** (stateless, zero-overhead)
- Day count: `Act360`, `Act365F`, `Thirty360`
- Interpolation: `LinearYield`, `LogLinearDF`, `PiecewiseConstantHazard`

## Test Suite

37 Catch2 tests covering:
- Day-count conventions (15-row reference table)
- Interpolation round-trips and between-knot behavior
- Discount curve bootstrap + monotonicity (C12)
- Bond pricing, YTM, accrued interest (C14)
- DV01, Z-spread, key-rate DV01, convexity (C15)
- CDS par spread vs ISDA reference vectors (C13)
- Hazard bootstrap round-trip, survival monotonicity
- CDS MTM, CS01 analytic vs FD, sign sanity

25 pytest tests covering Sprint 1 data pipeline and signals.

## License

Private research project.
