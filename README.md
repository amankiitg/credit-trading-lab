# Credit Trading Lab

Research platform for credit relative-value trading strategies. Combines a Python data pipeline with a C++17 pricing engine to build duration-hedged RV signals for HY/IG credit spreads. The central thesis: equity markets incorporate risk faster than credit, creating predictable lag-driven dislocations.

## Tier 1 — complete (5 sprints, tagged `sprint-v1` … `sprint-v5`)

Tier 1 built the full research stack — data pipeline, C++ pricer, RV signals, regime analytics, a visualizer, and a costed backtest — and put the central thesis on trial under pre-registered falsification criteria.

**Bottom line.** The equity-credit lag is a genuine *statistical* effect (Sprint 3, restated in Sprint 5.5: mean-reversion is ~67% faster on `equity_first` regime days on the tradeable OLS residual) but **not a tradeable regime filter** (Sprint 5: gating trades on it gives a negative incremental Sharpe, ΔS = −0.41 with a bootstrap CI entirely below zero, and the rejection holds out-of-sample and across every robustness cut). The genuine deliverable is the **unfiltered RV1 signal** — Strategy A, net Sharpe 0.59, 81% hit rate after costs. The honest negative on the regime overlay is itself a Tier-1 result: a pre-registered hypothesis, falsified cleanly and documented. Tier 2 would test the lag as a position-*sizing* input rather than an entry *gate*, and harden Strategy A toward paper trading.

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
  dashboard/            Streamlit signal visualizer (Today View + historical views)
  execution/            cost model + position state machine
  backtest/             backtest engine, metrics, A/B test, benchmarks, failure analysis
  risk/                 multi-signal portfolio combination
  data/                 raw + processed parquet files
  notebooks/            validation notebooks (01: signals, 02: pricer, 03: RV, 04: thresholds, 05: backtest)
  scripts/              notebook/screenshot generators
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

### Sprint 3 — Relative Value Signals (complete)
Three RV signal families (HY/IG, credit/rates, cross-term) with OLS, Kalman, and DV01-based hedging. Regime-conditional quality analysis testing the equity-credit lag thesis. Output: populated RV residuals, regime labels, `regime_signal_quality.parquet`. See `sprints/v3/PRD.md`.

Status (V1–V10 complete, tagged `sprint-v3`):
- V1 ✓ regime classifiers (`signals/regimes.py`): vol, equity, equity-credit lag — all trailing-only
- V2 ✓ regime tests (`tests/test_regimes.py`): 8/10 pass; 2 C18(b) failures pre-registered honest failures
- V3 ✓ rolling OLS hedge: residual variance reduced 11–47× vs raw spread on all 3 pairs
- V4 ✓ 2-state Kalman hedge: α/β random-walk states, residual variance ~20× smaller than OLS
- V5 ✓ DV01 hedge via C++ pricer (`dv01_hedge`): full 4784-day sweep in 0.45s; pair-1 ratio mean 0.46 matches theoretical 4y/9y duration ratio
- V6 ✓ best-method selection + features.parquet 56-col enrichment (`enrich_with_rv`); Kalman wins ADF on all 3 pairs; 25/25 sprint-1 tests still green — **⚠ superseded by Sprint 5.5: ADF selection rewards whitening; the tradeability selector picks OLS for all 3 pairs**
- V7 ✓ stationarity, cointegration, half-life tests: 9/9 pass (C19/C20/C21)
- V8 ✓ regime quality table (63 rows) + **C22 thesis PASSES** (RV1 equity_first half-life is 43–67% shorter than neither across methods); C23 fails on 4/9 (OLS β crosses zero); C24 passes — **⚠ Sprint 5.5 errata: "across methods" retired (43% leg was whitened Kalman); honest claim is 67% on OLS only; table now 56 rows**
- V9 ✓ ELI-10 validation notebook (`notebooks/03_rv_signals.ipynb`, 25 cells) with 6 plots in `sprints/v3/plots/`
- V10 ✓ sprint close, full test suite (38 Catch2 + 48/51 pytest, 3 documented option-3 honest failures), `sprints/v3/WALKTHROUGH.md`

| Falsification | Result |
|---|---|
| C18 — regime coverage + non-degeneracy | partial: 1/3 (vol_regime) |
| C19 — RV residual stationarity | ✓ all 3 |
| C20 — cointegration via best method | ✓ all 3 |
| C21 — half-life ∈ [1, 126] | ✓ all 3 |
| C22 — equity-credit lag thesis | ✓ 67% shorter (OLS; v5.5 restated, was "43–67% across methods") |
| C23 — hedge ratio CV < 1.0 | partial: DV01 ✓, OLS/Kalman fail on regime shifts |
| C24 — quality parquet schema | ✓ |

### Sprint 4 — Signal Visualizer + Today View (complete)
Streamlit dashboard over `features.parquet` with three views: (1) **Today View** — 6 horizontal cards (3 directional + 3 RV) with conviction tier HIGH/MED/LOW (HIGH iff `equity_credit_lag == 'equity_first' AND |z| > 2`); (2) Historical Directional — 3 synced panels with entry/exit/stop markers; (3) Historical RV — legs + hedge ratio + residual+z + stats strip. Regime shading (vol or equity-credit-lag) on both historical views. Threshold sliders redraw in 60–112 ms p95 (8× under the 500 ms gate). No P&L. Run with `streamlit run dashboard/app.py`. See `sprints/v4/PRD.md` + `WALKTHROUGH.md`. Tagged `sprint-v4`.

| Falsification | Result |
|---|---|
| D1 — Conviction truth table (63 cells) | ✓ |
| D2 — HIGH border = green | ✓ |
| D3 — Slider redraw < 500ms p95 | ✓ 60ms / 112ms |
| D4 — Panel x-axis sync | ✓ |
| D5 — Marker fidelity | ✓ |
| D6 — Regime span boundaries | ✓ |
| D7 — Today View persistence | ✓ |
| D8 — No-crash render | ✓ |
| D9 — HIGH count sanity baseline | ✓ 178 ∈ [50,1500] |

### Sprint 5 — Backtest + Thesis Test + Portfolio (complete, final Tier-1 sprint)
A backtest engine (cost model, position state machine, mark-to-market P&L, metrics) runs the equity-credit-lag thesis as an A/B test: Strategy A trades the RV1 residual unconditionally, Strategy B only on `equity_first` days. Pre-registered costs (1.5bp half-spread + 0.5bp slippage + 0.40%/yr borrow), fixed $1M DV01-hedged notional. **The thesis is rejected at the trading level.** See `sprints/v5/PRD.md` + `WALKTHROUGH.md`. Tagged `sprint-v5`.

| Falsification | Result |
|---|---|
| C25 — engine correct, no leakage | ✓ PASS |
| C26 — control strategy real (≥30 trades) | ✓ PASS |
| C27 — **thesis**: incremental Sharpe ΔS>0, CI excludes 0 | ✗ FAIL (ΔS −0.41, CI [−0.82,−0.01]) |
| C28 — out-of-sample ΔS>0 | ✗ FAIL (OOS ΔS −0.61) |
| C29 — Strategy B beats random p95 | ✗ FAIL |
| C30 — robust across grid + subperiods | ✗ FAIL (0/27 cells) |
| C31 — no single trade >25% of P&L | ✗ FAIL (B: 49%) |

**Headline finding.** The equity-credit lag is a real *statistical* effect (Sprint 3 C22, restated in Sprint 5.5: ~67% faster mean-reversion on `equity_first` days on the tradeable OLS residual) but **not a tradeable regime filter** — gating discards ~85% of trades and the lost diversification outweighs the faster reversion. The unfiltered Strategy A (net Sharpe 0.59, 81% hit rate) is the better strategy and the genuine Tier-1 deliverable. Engine gates pass, so this is a correctly-measured, pre-registered rejection.

### Sprint 5.5 — Foundation Repair (pre-Tier-2 gate, complete)

A **corrective** sprint, not new research. Auditing the signal-generation layer before Tier 2 surfaced that **three different residuals travelled under the name `rv_hy_ig`**: the pipeline's `select_best_method` ranked hedge methods by *lowest ADF p-value* — which rewards whitening — so `features.parquet` and the dashboard published the **Kalman posterior** residual (std 0.005, half-life ~1.5 days = whitened noise), while the v5 backtest correctly traded the **OLS** residual it deliberately chose. v5.5 collapses these to one honest, consistently-consumed residual and restates the affected claims. **Strategy A is unchanged — net Sharpe 0.591, bit-identical to v5** (the winner always traded the clean OLS residual; the rot was in the layer around it). See `sprints/v5.5/{PRD,TASKS,WALKTHROUGH,notes}.md` and `notebooks/55_foundation_repair.ipynb`.

| Falsification (errata namespace) | Result |
|---|---|
| E1 — features / dashboard / backtest read **bit-identical** residual | ✓ PASS |
| E2 — no stored residual has half-life < 5d (no whitened residual survives) | ✓ PASS |
| E3 — Kalman residual is the one-step-ahead innovation, not the posterior | ✓ PASS |
| E4 — `rv_xterm` DV01 (a copy of pair-1's ratio) removed | ✓ PASS |
| E5 — C22 restated on the canonical OLS residual only | ✓ PASS |
| E6 — Strategy A re-validated through the unchanged v5 engine | ✓ PASS (Sharpe 0.591, Δ +0.001) |

**Corrections to the Sprint 3 record:** (1) the "best method = Kalman" claim was an artifact of the ADF selector — the tradeability selector (stationary **and** half-life ∈ [5, 63] days) picks **OLS** for all three pairs; (2) the C22 "43–67% shorter *across methods*" framing is retired — the 43% end was the whitened Kalman residual (0.85d vs 1.46d = noise vs noise) and the DV01 leg is non-stationary; the honest number is **67% shorter on OLS only**, and C22 still passes. The `regime_signal_quality.parquet` schema is 56 rows (was 63; the 7 `rv_xterm/dv01` rows removed).

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

# Python tests (all sprints; pycredit must be importable)
PYTHONPATH=python/credit venv/bin/python3 -m pytest tests/ -q
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

### Dashboard (Sprint 4)
```bash
# Launch the Streamlit signal visualizer → http://localhost:8501
streamlit run dashboard/app.py
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
| `data/processed/features.parquet` | 4784 x 56 | Pipeline | Spreads, z-scores, flags, RV residuals, regime labels, z_rv |
| `data/results/regime_signal_quality.parquet` | 63 x 9 | Sprint 3 | Per-regime half-life / z-magnitude / signal-freq |
| `data/benchmarks/random_baseline.parquet` | 3000 x 8 | MC sim | 1000-path random-entry baseline |
| `cpp/tests/ref/*.csv` | small | Static | ISDA/bond reference vectors |

## Test Suite

**C++ (Catch2):** 38 tests covering day-count conventions, interpolation, discount curve bootstrap (C12), bond pricing/YTM (C14), DV01/Z-spread/key-rate DV01 (C15), CDS par spread vs ISDA (C13), hazard bootstrap, CDS MTM/CS01, and throughput benchmarks.

**Python (pytest):** 190 tests across all sprints — Sprint 1 data pipeline, Sprint 2 parity/throughput, Sprint 3 regimes / RV signals / regime-quality (C18–C24), Sprint 4 dashboard (conviction, regime shading, AppTest smoke, sanity), and Sprint 5 backtest (cost model, position state machine, engine + leakage, metrics, A/B bootstrap, benchmarks, failure analysis, regime table, portfolio). 187/190 pass; the 3 failures are pre-registered Sprint 3 honest failures (C18 regime non-degeneracy ×2, C23 hedge-ratio CV ×1) documented in `sprints/v3/notes.md`.

## License

Private research project.
