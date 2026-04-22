# Credit Trading Lab

Research platform for credit relative-value trading strategies. Combines a Python data pipeline with a C++17 pricing engine to build duration-hedged RV signals for HY/IG credit spreads. The central thesis: equity markets incorporate risk faster than credit, creating predictable lag-driven dislocations.

## Architecture

```
credit-trading-lab/
  cpp/                  C++17 pricing library (libcredit)
    include/credit/       headers: bond, cds, discount/survival curves, day-count, interpolation
    src/                  implementations
    tests/                Catch2 test suite (38 tests) + reference vectors
  bindings/python/      pybind11 bridge (pycredit module)
  python/credit/        Python package wrapping pycredit
  signals/              data pipeline + spread signals + RV signal generators
  data/                 raw + processed parquet files
  notebooks/            validation notebooks (01: signals, 02: pricer, 03: RV)
  sprints/              sprint PRDs, tasks, walkthroughs, plots
  tests/                Python test suite (pytest)
```

## Sprint Progress

### Sprint 1 — Data Pipeline + Spread Signals (complete)
ETF-derived credit spread signals (HYG/LQD/SPY/IEF), FRED Treasury + BAML OAS data, 9 rolling z-scores, 12 signal-state flags, random-entry MC baseline. Output: `features.parquet` (4784 x 50), `credit_market_data.parquet` (7639 x 15). 10/11 falsification criteria pass (C3 failed on z-score distribution bands — threshold miscalibrated, not signal). 25/25 tests green. Tagged `sprint-v1`.

### Sprint 2 — C++ Pricing Engine (complete)
C++17 library with pybind11 Python bindings for fixed-income and credit derivative pricing. All 6 falsification criteria pass:

| Criterion | Target | Observed |
|---|---|---|
| C12: Discount curve knot reprice | < 1e-10 | 0.0 (exact) |
| C13: CDS par spread vs ISDA | < 0.5 bps | < 0.01 bps |
| C14: Bond YTM vs reference | < 1.0 bp | < 1e-10 bp |
| C15: DV01 analytic vs FD | < 1% relative | 2e-05% |
| C16a: Bond throughput | > 10k/sec | 42,343/sec |
| C16b: CDS throughput | > 5k/sec | 14,183/sec |

38/38 Catch2 tests, 0 warnings, Python/C++ parity to 12+ significant digits. Tagged `sprint-v2`.

### Sprint 3 — Relative Value Signals (planned)
Three RV signal families (HY/IG, credit/rates, cross-term) with OLS, Kalman, and DV01-based hedging. Regime-conditional quality analysis testing the equity-credit lag thesis. Output: populated RV residuals, regime labels, `regime_signal_quality.parquet`. See `sprints/v3/PRD.md`.

## Building

### Prerequisites
- C++17 compiler (Apple Clang 14+ or GCC 11+)
- CMake 3.20+
- Python 3.10+ with numpy

### Build
```bash
# Create venv and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Build C++ library + pybind11 module
cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release \
  -DPython_EXECUTABLE=$(pwd)/venv/bin/python3 \
  -DPYBIND11_FINDPYTHON=ON
cmake --build build -j
```

### Run Tests
```bash
# C++ tests (38 Catch2)
ctest --test-dir build --output-on-failure

# Python tests (Sprint 1 + Sprint 2 parity/throughput)
venv/bin/python3 -m pytest tests/ -v
```

### Notebooks
```bash
# Register venv kernel for Jupyter
venv/bin/python3 -m ipykernel install --user --name credit-lab

# Run validation notebooks
jupyter nbconvert --to notebook --execute \
  notebooks/01_signal_validation.ipynb \
  --ExecutePreprocessor.kernel_name=credit-lab
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

## Data

| File | Shape | Source | Content |
|---|---|---|---|
| `data/raw/{HYG,LQD,SPY,IEF}.parquet` | ~4784 rows | yfinance | Daily OHLCV + adj close |
| `data/raw/credit_market_data.parquet` | 7639 x 15 | FRED | DGS1-30, BAML OAS, synthetic CDS |
| `data/processed/features.parquet` | 4784 x 50 | Pipeline | Spreads, z-scores, flags, RV stubs |
| `data/benchmarks/random_baseline.parquet` | 3000 x 8 | MC sim | 1000-path random-entry baseline |
| `cpp/tests/ref/*.csv` | small | Static | ISDA/bond reference vectors |

## Test Suite

**C++ (Catch2):** 38 tests covering day-count conventions, interpolation, discount curve bootstrap (C12), bond pricing/YTM (C14), DV01/Z-spread/key-rate DV01 (C15), CDS par spread vs ISDA (C13), hazard bootstrap, CDS MTM/CS01, and throughput benchmarks.

**Python (pytest):** 29 tests covering Sprint 1 data pipeline (schema, NaN, stationarity, flags, FRED coverage, benchmarks) and Sprint 2 parity/throughput (Python/C++ cross-check, 10k batch timing).

## License

Private research project.
