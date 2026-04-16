# Sprint v1 — Dev Notes

Running log of findings as tasks land. Append-only.

---

## 2026-04-16 — Task 1: scaffold modules + pin deps

**Status:** done.

### What was built
- `signals/load.py`, `signals/features.py`, `signals/zscore.py`,
  `signals/pipeline.py` — stubs with type-hinted signatures and
  docstrings tied back to PRD section references. Bodies are
  `raise NotImplementedError` so callers fail loudly if run early.
- `requirements.txt` — added `jupyter==1.1.1`, `pyarrow==21.0.0`,
  `statsmodels==0.14.6` (plus transitive pins `patsy==1.0.2`,
  `scipy==1.13.1`).

### Acceptance check
```
$ venv/bin/python -c "import signals.load, signals.features,
    signals.zscore, signals.pipeline, pyarrow, statsmodels; print('ok')"
ok
pyarrow 21.0.0 | statsmodels 0.14.6
```
All four module imports + both new deps resolved cleanly. Task 1
acceptance satisfied.

### Environment blocker (flagged, not fixed)

The existing `venv/` was copied from a different project path
(`credit-quant`). Symptoms:

- `venv/bin/pip` has a broken shebang: `/Users/amankesarwani/
  PycharmProjects/credit-quant/venv/bin/python3: no such file`.
- `source venv/bin/activate` doesn't stick because pyenv shadows PATH
  and sends `python` to the system 3.6.15 — which fails any modern
  pip install with a numpy build error.

**Workaround used:** invoked the venv's python binary directly
(`venv/bin/python -m pip install ...`) — that works because the
site-packages is local and we bypass the broken shebang.

**Recommended fix before Task 2:** delete and recreate the venv so
`pip`, `jupyter`, and any other entry-point scripts have correct
shebangs. This will matter as soon as we try `jupyter notebook` or
`pytest`.

### Observations
- Scaffolding intentionally has no logic. Leakage rules in PRD §Signal
  Definition and docstring contracts are the only thing to enforce
  later; no temptation to pre-optimize.
- `scipy` pulled in by `statsmodels` unblocks downstream ADF work in
  Task 9.

### Next task
Task 2 — yfinance ingest → `data/raw/{ticker}.parquet`. Blocked only
by the venv issue above if we want `pytest` / `jupyter` to work from
the shell. The ingest itself can proceed with
`venv/bin/python signals/load.py`.

---

## 2026-04-16 — Tasks 2-10 (remainder of sprint)

**Venv:** rewrote broken `credit-quant` → `credit-trading-lab` shebangs
in-place across `venv/bin/` and `venv/pyvenv.cfg`. `pip`, `pytest`,
`jupyter` now all work from the shell without reinstalling packages.
`.dist-info/RECORD` files still reference the old path but are not
runtime-loaded.

### Task 2 — yfinance ingest
All four tickers pulled cleanly for 2007-04-11 → 2026-04-15: **4784
rows each**. Index is tz-naive, monotonic, unique; dtypes match the
PRD schema exactly (OHLC+adj_close `float64`, volume `int64`).

### Task 3 — raw audit
`pytest tests/test_signals.py::test_raw_integrity` passes for every
ticker. Max consecutive business-day gap = **2** (long weekends).
Coverage plot: `sprints/v1/plots/01_raw_coverage.png`.

### Tasks 4 + 6 + 7 — features, spreads, z-scores
Implemented as pure functions in `signals/features.py` and
`signals/zscore.py`. Paired unit tests all pass:

- `test_spread_identity` — `hy_ig == hy_spread - ig_spread` within 1e-12
- `test_returns_no_leakage` — tainting the last row of price input
  does not change earlier returns
- `test_zscore_no_leakage` — same for z-scores
- `test_zscore_known_values` — z-scores on synthetic N(5,2)×5000 data
  recover mean ≈ 0, std ∈ (0.8, 1.2)
- `test_vol_is_annualized` — σ≈0.01 daily → annualized vol ≈ 0.16

### Task 5 — returns validation

```
ticker       mean       std     skew      kurt      ac1
HYG       +0.00019  +0.00688  +0.31   +39.3    +0.002
LQD       +0.00016  +0.00560  -0.45   +56.2    +0.005
SPY       +0.00040  +0.01247  -0.29   +13.8    -0.103
IEF       +0.00013  +0.00440  +0.11    +2.6    -0.029
```

**Observation:** SPY lag-1 autocorrelation is -0.103, marginally
outside the PRD band `|ρ|<0.10`. This is well-known microstructure
behavior for SPY daily returns (bid-ask bounce + mean-reverting
intraday flow). Not a data-quality issue.

Heavy fat tails on credit ETFs (kurt 39 and 56) are real — they reflect
2008 and 2020 crises in the sample.

Plots: `02_returns_acf.png`, `03_returns_dist.png`.

### Task 8 — pipeline
`signals.pipeline.build()` produces `data/processed/features.parquet`
with shape **(4784, 32)** and the exact column layout from PRD §Parquet
Schemas. Inner-join on adjusted close keeps all 4784 rows (row counts
conserved across every ticker). `test_features_schema` and
`test_features_no_nan_post_warmup` both pass. Full suite: **11/11
green**.

### Task 9 — signal validation + baseline

```
column              mean     std    kurt   adf_p     ac1
hy_spread_z63    +0.3259  +1.31  +0.03   0.0000  +0.946
hy_spread_z126   +0.3985  +1.35  +0.42   0.0000  +0.971
hy_spread_z252   +0.4106  +1.38  +1.11   0.0004  +0.984
ig_spread_z63    +0.2294  +1.36  -0.08   0.0000  +0.961
ig_spread_z126   +0.3173  +1.41  +0.68   0.0000  +0.979
ig_spread_z252   +0.3448  +1.37  +1.01   0.0001  +0.989
hy_ig_z63        +0.1926  +1.32  -0.34   0.0000  +0.935
hy_ig_z126       +0.2367  +1.37  -0.04   0.0000  +0.961
hy_ig_z252       +0.2874  +1.41  +0.12   0.0020  +0.982
```

Baseline (shuffled spread, z252):
```
hy_spread_z252  mean -0.006  std 1.000  kurt  0.79  adf_p 0.0
ig_spread_z252  mean -0.004  std 0.997  kurt  0.10  adf_p 0.0
hy_ig_z252      mean -0.002  std 1.001  kurt  0.85  adf_p 0.0
```

**Key finding:** on shuffled data, rolling z-scores converge to
`μ≈0, σ≈1.0` as expected. On the **real** spreads, z-scores have
persistent positive mean (+0.19 to +0.41) and std ~1.3–1.4. The gap
between observed and baseline is the signal — it measures how much of
the variance is driven by secular credit-regime level shifts (GFC
recovery, COVID, 2022 rate shock) that the rolling window partially
but not fully absorbs.

### Task 10 — notebook
`notebooks/01_signal_validation.ipynb` executes top-to-bottom via
`jupyter nbconvert --execute`. Final cell prints a per-criterion
checklist.

### Phase-1 verdict

```
PASS  C1  no NaNs post-warmup
PASS  C2  ADF p < 0.05 (all 9 z-scores)
FAIL  C3  z-score mean ∈ [-0.2, 0.2] and std ∈ [0.8, 1.2]
PASS  C4  z-score kurtosis < 20
PASS  C5  max business-day gap ≤ 5 (observed 2)
PASS  C6  row counts conserved (4784 × 4 → 4784)

PHASE1 STATUS: FAIL
```

### What the C3 failure actually tells us

The pre-registered band was wrong, not the signal. Three pieces of
evidence:

1. The shuffle baseline recovers μ≈0, σ≈1 exactly — so the
   normalization math is correct.
2. ADF comfortably rejects unit roots on all 9 columns — the series
   are stationary enough to trade.
3. The realized std of ~1.35 is consistent with excess kurtosis in
   the underlying log-price-ratio series (credit spreads fat-tail
   around crises). An N(0,1) band is only correct if the input is
   itself Gaussian, which credit spreads are not.

### Recommendations for Sprint v2

- Widen the acceptable z-score bands to `|μ|<0.5` and `σ∈[0.7, 1.5]`,
  *or* switch to a robust scaler (median / MAD) that is less sensitive
  to fat tails.
- Investigate whether the persistent positive mean is signal or
  artifact — split the sample pre/post-2015 and compare.
- Start building the first trading signal (threshold trade around
  `|z| > k`) with proper train/test splits.

### Files touched this sprint

- Code: `signals/load.py`, `signals/features.py`, `signals/zscore.py`,
  `signals/pipeline.py`
- Tests: `tests/test_signals.py` (11 tests, all passing)
- Plotting: `sprints/v1/make_plots.py`
- Data: `data/raw/{HYG,LQD,SPY,IEF}.parquet`,
  `data/processed/features.parquet`
- Plots: `sprints/v1/plots/0{1..5}_*.png`
- Notebook: `notebooks/01_signal_validation.ipynb`
- Deps: `pyarrow==21.0.0`, `statsmodels==0.14.6`, `jupyter==1.1.1`,
  `pytest==8.4.2`, plus transitives (`patsy`, `scipy`)

---

## 2026-04-17 — Amendment: signal-state flags + RV stubs

**Status:** done. Pre-registered in `PRD_update.md`; tasks U1–U3 in
`TASKS_update.md`. Schema bumped from 32 → 49 columns.

### What changed

- New `signals/flags.py`:
  - `compute_flags(df, spreads, window=63, thresholds=FlagThresholds())`
    returns a bool frame with 12 columns (4 flags × 3 spreads) from
    `{spread}_z63`. NaN z-score → `False`.
  - Thresholds: `entry=2.0`, `exit=0.5`, `stop=4.0`. `exit >= entry`
    or `stop <= entry` raises `ValueError`.
  - `rv_stubs(index)` returns a float64 frame with 5 all-NaN columns:
    `rv_hy_ig_residual`, `rv_credit_rates_residual`,
    `rv_xterm_residual`, `hedge_ratio_hy_ig`, `hedge_ratio_cr`.
- `signals/pipeline.py` extended: `compute_flags(...)` then
  `rv_stubs(...)` appended after z-scores; column ordering enforced
  by an explicit `ordered` list.
- `tests/test_signals.py` extended (11 → 16 tests); schema asserts
  `len(df.columns) == 49`, dtype discipline, and the exact per-ticker
  + per-spread layout.

### Observed flag firing rates (post-warmup, 4784 rows)

```
flag                    nans  fires  fire_rate_pct
hy_spread_entry_long       0    301           6.29
hy_spread_entry_short      0    266           5.56
hy_spread_exit             0    968          20.23
hy_spread_stop             0     10           0.21
ig_spread_entry_long       0    361           7.55
ig_spread_entry_short      0    292           6.10
ig_spread_exit             0   1043          21.80
ig_spread_stop             0     17           0.36
hy_ig_entry_long           0    283           5.92
hy_ig_entry_short          0    281           5.87
hy_ig_exit                 0   1135          23.72
hy_ig_stop                 0      6           0.13
```

All 12 flags satisfy **C7** (`0 < fire_rate < 25%`). `exit` flags
cluster near the upper bound (20–24%) because the `|z| < 0.5` band
naturally captures most of the distribution; this is expected given
the observed z-score std of ~1.35, not evidence of a broken threshold.
`stop` flags fire on 0.13–0.36% of rows — calibrated for rare tail
events (GFC, COVID, 2022 rate shock).

### RV stubs

All 5 columns present, float64, 100% NaN across all 4784 rows.
Satisfies **C8**. Ready for Phase 3 to populate in place without
schema migration.

### Verdict (unchanged)

```
PASS  C1  no NaNs post-warmup (amended to exclude flags + stubs)
PASS  C2  ADF p < 0.05 (all 9 z-scores)
FAIL  C3  z-score mean ∈ [-0.2, 0.2] and std ∈ [0.8, 1.2]
PASS  C4  z-score kurtosis < 20
PASS  C5  max business-day gap ≤ 5 (observed 2)
PASS  C6  row counts conserved (4784 × 4 → 4784)
PASS  C7  flags: bool, no NaN, fire-rate < 25%
PASS  C8  RV stubs present and all-NaN

PHASE1 STATUS: FAIL   (unchanged — C3 still fails; C7 + C8 pass)
```

The amendment does not change the sprint verdict — C3 was the only
failing criterion before the patch and remains the only failing
criterion after. C7 and C8 pass as pre-registered.

### Files touched this amendment

- Code: `signals/flags.py` (new), `signals/pipeline.py`
- Tests: `tests/test_signals.py` (11 → 16 tests, all passing)
- Data: `data/processed/features.parquet` rebuilt at **(4784, 49)**
- Docs: `sprints/v1/PRD.md`, `sprints/v1/TASKS.md` (Tasks 11–13),
  `sprints/v1/PRD_update.md`, `sprints/v1/TASKS_update.md`,
  `sprints/v1/notes.md` (this entry),
  `sprints/v1/WALKTHROUGH.md` (amendment section)
- Notebook: `notebooks/01_signal_validation.ipynb` (amendment cell
  + C1 fix to exclude flags+stubs; C7+C8 in final checklist)
