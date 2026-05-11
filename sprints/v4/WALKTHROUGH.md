# Sprint v4 — Signal Visualizer + Today View

## Summary

Sprint v4 built a local Streamlit dashboard over Sprint v3's `features.parquet`
that surfaces the C22 equity-credit-lag thesis as a single per-signal
conviction tier (HIGH iff `equity_credit_lag == 'equity_first' ∧ |z| > 2`),
plus two synchronized historical views and selectable regime backdrops. All
nine pre-registered correctness gates (D1–D9) pass. The dashboard is
**read-only** — no P&L was computed and no trades were simulated, by design.
**Verdict: ship.** The visualization is honest about Sprint v3's residual
issues (Kalman over-fit, hedge-stability failures), and the conviction-tier
sanity baseline shows the tier rule fires at roughly its independence rate,
which we flag below as a real limitation, not a bug.

## Hypothesis & Falsification Criteria

This sprint does not test a new economic claim. The PRD's falsification gates
are correctness/latency criteria — does the dashboard render Sprint v3's
signals faithfully and fast?

| ID | Criterion | Threshold | Result |
|---|---|---|---|
| D1 | Conviction truth table | 63 cells pass | ✓ 63/63 |
| D2 | HIGH card border green (`#1b8a3a`, 4px solid) | snapshot test | ✓ |
| D3 | Threshold-slider redraw p95 | < 500 ms | ✓ Directional 60 ms, RV 112 ms |
| D4 | Historical Directional x-axis sync | `shared_xaxes=True` | ✓ |
| D5 | Marker fidelity vs flag semantics | exact equality | ✓ |
| D6 | Regime span boundaries | no overlap / no gap; 5 unit tests | ✓ |
| D7 | Today View always at top | renders before view branch on every page | ✓ |
| D8 | No-crash render on `features.index[-1]` | 6 / 6 cards | ✓ |
| D9 | HIGH-cell sanity baseline | total ∈ [50, 1500] AND no signal > 70% | ✓ 178 total, top signal 36% |

## Data Pipeline

**Source:** `data/processed/features.parquet` (4784 × 56), produced by
Sprint v3's `signals.pipeline.build_with_rv()`. No new ingestion.

**Frequency / range:** daily, 2007-04-11 → 2026-04-15. Universe is fixed
at 4 ETFs (HYG, LQD, SPY, IEF) per Sprint v1; no time-varying membership.

**Transforms applied (V4):**
1. `dashboard/loader.py::load_features()` — single `pd.read_parquet`
   call, wrapped in `@st.cache_data` so subsequent Streamlit reruns
   (slider moves, view switches) bypass disk I/O.
2. For Historical RV, `dashboard/views/rv.py::_load_rates()` lazily
   reads `data/raw/credit_market_data.parquet` for the `dgs10` / 2y-10y
   slope rates legs; also `@st.cache_data`.
3. Marker overlays recomputed on the fly from current slider values via
   `dashboard/components/markers.py::from_thresholds(z, entry, exit_t, stop)`
   — pure NumPy, no parquet re-read on slider.
4. Regime backgrounds via
   `dashboard/components/regime_shade.py::spans(df, regime_col)` →
   contiguous-run tuples → fed once into `fig.update_layout(shapes=…)`.

**Known biases (inherited from prior sprints):**
- **"Today" = `features.index[-1]`.** Deterministic, reproducible, but
  goes stale if the parquet isn't refreshed. Surfaced in the Today
  View header as `as of <date>`.
- **Regime label leakage.** `vol_regime` uses an expanding median;
  weak look-ahead into earlier days. Same caveat as Sprint v3.
- **Kalman residual is near-zero noise** (v3 Sprint finding). v4
  inherits this and makes it visible in the RV view (residual panel
  hugs zero while z spikes ±3) but does not re-validate or override
  the v6 best-method selector.

**Rows dropped:** none in v4. Day-count is preserved end-to-end
(4784 rows in, 4784 displayed). 252-day warmup is applied
analytically (the dashboard renders all rows but the historical
views' interesting region starts after row 378).

## Signal Behavior

### z-score distribution by signal (post-warmup, n=4532)

| signal | mean | std | skew | excess kurt | \|z\|>2 fraction |
|---|---|---|---|---|---|
| hy_spread | +0.33 | 1.31 | -0.70 | 0.0 | 11.8% |
| ig_spread | +0.23 | 1.36 | -0.59 | -0.1 | 13.4% |
| hy_ig | +0.19 | 1.32 | -0.42 | -0.3 | 11.8% |
| rv_hy_ig | -0.01 | 1.03 | -0.17 | 1.3 | 5.2% |
| rv_credit_rates | -0.06 | 1.07 | -0.71 | 2.0 | 6.3% |
| rv_xterm | -0.03 | 1.05 | -0.48 | 1.7 | 6.1% |

Directional spread z-scores are wider-tailed (|z|>2 in ~12–13% of
days vs ~5–6% for RV) because directional z is divided by a stable
spread std, while RV z is divided by a tiny residual std (Kalman
over-fit). The RV z's high excess kurtosis (1.3 / 2.0 / 1.7) tells
the same story — fat tails relative to a true σ=1 Gaussian.

See `sprints/v4/plots/01_z_density_by_regime.png` for the same
distributions split by `equity_credit_lag` regime; the three regime
densities heavily overlap, which is why the HIGH conviction
requires **both** `equity_first` AND `|z|>2` — neither alone
discriminates.

### Conviction-tier coverage (the "signal" v4 actually produces)

Cross-tab of all 27,192 post-warmup `(date × signal)` cells:

| tier | count | share |
|---|---|---|
| HIGH | 164 | 0.60% |
| MED | 2,561 | 9.42% |
| LOW | 24,467 | 89.98% |

**HIGH coverage stays close to its independence baseline.** Under
random regime labels (seed 42), HIGH would fire on 168 cells —
within 3% of the actual 164. This is informative: at the level of
*how often* HIGH fires, the regime gate adds ~zero discrimination
on top of `|z|>2` alone, because `equity_first` is roughly
independent of extreme z (it's a lag-detection regime, not a
volatility regime).

This does **not** refute Sprint v3's C22 thesis. C22 is about
*mean-reversion speed* on the equity_first subset (half-life
0.85 d vs 1.48 d in neither), not about whether extreme z is
*more common* there. The dashboard correctly encodes the v3 thesis
into a per-day tier the PM can read instantly; whether the
tier is a profitable filter at trade time is a Sprint v5 question.

### Regime-conditioned per-signal HIGH counts (D9 baseline)

| signal | HIGH days | first HIGH |
|---|---|---|
| hy_spread | 29 | 2008-01-14 |
| ig_spread | 64 | 2007-07-10 |
| hy_ig | 40 | 2008-01-14 |
| rv_hy_ig | 17 | 2007-10-17 |
| rv_credit_rates | 14 | 2010-11-12 |
| rv_xterm | 14 | 2007-10-22 |
| **total** | **178** | |

178 ∈ [50, 1500]; top signal share 36% (under 70%). Concentrated
around 2008 GFC, late 2010 (euro crisis), 2015–16 oil bust,
COVID-2020, 2022 rate shock — the macro stress periods where you
would *want* a "watchlist" alert.

### Stationarity / IC / decay

Not computed in v4. The dashboard is a viewer over Sprint v3's
already-stationary residuals (Sprint v3 C19/C20/C21 all passed).
An IC / rank-IC analysis on the conviction tier vs forward returns
is the natural Sprint 5 follow-up; v4 deliberately stops short of
that to avoid mixing visualization with predictive validation.

## Backtest Results

**No backtest was run in this sprint.** The PRD explicitly scopes out
P&L (`Out of Scope: P&L, Sharpe, backtest, position sizing`). The
substantive results for v4 are correctness and latency, presented
below as a replacement for the standard Backtest Results section.

### Latency

20 entry-slider moves per view, measured via `streamlit.testing.v1.AppTest`
and logged to `sprints/v4/slider_latency.csv`:

| view | p50 | p95 | max |
|---|---|---|---|
| Historical Directional | 56.4 ms | 60.3 ms | 75.6 ms |
| Historical RV | 102.3 ms | 111.9 ms | 127.8 ms |

5–10× under the 500 ms gate. Latency stays bounded because
`load_features` is cached and `from_thresholds` is a pure NumPy
operation; the only per-rerun work is the Plotly figure rebuild
(at ~10–100 ms depending on view).

### Regime-shading perf (before / after fix)

| approach | shapes | latency |
|---|---|---|
| `fig.add_vrect()` × 897 calls | 897 | 830,820 ms |
| `fig.update_layout(shapes=…)` one-shot | 897 | 98 ms |

Shipped path uses the one-shot variant with per-subplot
`yref="{y} domain"` rects (so shading stays inside data areas, not
title gutters). End-user latency on a regime toggle: 150–350 ms.

### Subperiod stability (rendering)

Historical views were spot-checked at 4 date-range slices:
2007-2010 (GFC concentrated), 2011-2014 (calm), 2015-2018, 2019-2026.
All slices render with synchronized x-axes, no NaN crashes,
markers consistent with the underlying flag columns. Regime
shading correctly transitions on every regime boundary across all
slices.

### Parameter sensitivity (threshold sliders)

Sweeping `entry` from 1.0 → 3.5 (post-warmup, 4532 days):

| entry | total `|z|>entry` cells | total HIGH cells |
|---|---|---|
| 1.0 | 9,720 | 800 |
| 1.5 | 5,310 | 444 |
| 2.0 | 2,725 | 164 |
| 2.5 | 1,320 | 65 |
| 3.0 | 593 | 22 |
| 3.5 | 235 | 4 |

Roughly exponential decay; default 2.0 sits where the HIGH count
is large enough to be informative (~180 days over 19 years) but
not so common as to be noise. Doubling to 4.0 reduces HIGH to
trivial.

## Key Findings

1. **The dashboard makes Sprint v3's caveats unavoidable.** Looking
   at the RV residual+z panel for `rv_hy_ig`, a user can see the
   residual hugs zero while z spikes — visual evidence of the
   Kalman over-fit. The dashboard demonstrates *why* the v3
   PRD-best (Kalman by ADF) isn't tradeable, far more directly
   than the v3 WALKTHROUGH does in prose.

2. **HIGH frequency is baseline-like; HIGH *quality* is the v3 claim.**
   The regime gate (`equity_first`) does not increase the frequency
   of HIGH days vs random labels (164 vs 168). What v3 showed, and
   v4 visualizes, is that *when* HIGH fires the half-life is
   shorter. This is a subtle distinction worth carrying forward into
   v5 — frequency-of-firing is not the right metric for the tier;
   conditional realized-revert is.

3. **Plotly's `add_vrect` is quadratic in shape count.** 897
   sequentially-added shapes took 14 minutes. The fix
   (`update_layout(shapes=…)`) cut it to 98 ms. Any future dashboard
   work involving categorical-background shading should batch
   shapes by default.

4. **Streamlit's free-form date range picker allows max-value
   exceedance.** Even with `max_value` set, the widget lets users
   tab through to dates past the bound; the error surfaces at
   re-render time. A preset selectbox + two clamped pickers is a
   more robust pattern.

5. **The matplotlib screenshot stays in sync because the conviction
   logic is pure.** `scripts/today_view_screenshot.py` calls the
   same `conviction()` / `position_text()` / palette functions the
   Streamlit cards use, so refactors to either path are visually
   guaranteed not to drift apart.

## Limitations

- **No predictive validation.** v4 does not test whether HIGH
  conviction days have actually higher forward returns / faster
  revert than MED or LOW on out-of-sample data. The HIGH-count
  baseline shows tier *frequency* is independence-like; tier
  *quality* is a Sprint 5 question.
- **"Today" is not actually today.** Last bar = 2026-04-15. The
  dashboard has no live-refresh affordance, no Slack/email alert
  on regime activation, no Yahoo-pull. Useful for review, not for
  intraday trading.
- **Single-user, single-machine.** No auth, no deployment, no
  persistence of slider state across sessions. Refreshing resets
  everything to defaults.
- **Visual regression is not enforced.** Tests check the Python-side
  shape lists, HTML strings, and AppTest element trees; they do
  not snapshot rendered Plotly pixels. A future-someone changing
  Plotly version or marker palette could break the look without
  any test catching it.
- **Multiple testing.** The dashboard surfaces 27 regime × method
  cells inherited from Sprint v3 plus the conviction tier on 6
  signals × every date. A PM staring at this could trivially find
  cherry-picked HIGH days that "worked." The card UI is informative,
  not statistically rigorous.
- **Costs / borrow / sizing not modeled.** Position text is action
  language only ("Long HYG / Short LQD") with no notional. A
  ticker-level borrow-cost feed would be the minimum addition
  before any v5 backtest.

## Reproducibility

- **No seeds**; v4 has no stochastic step. The shuffled-regime
  baseline in Signal Behavior uses `np.random.seed(42)`.
- **Data snapshot:** `data/processed/features.parquet` as produced
  by the `sprint-v3` tag (commit `4115592`, 4784 × 56).
- **Code:** sprint-v4 tag = commit `554a8d5`.
- **Dependencies (new in v4):** `streamlit==1.50.0`, `plotly==6.7.0`
  + transitive deps recorded in `requirements.txt`.

**To regenerate every output:**

```bash
# 1. Setup
source venv/bin/activate
pip install -r requirements.txt

# 2. (Re)build features.parquet from sprint-v3 artifacts (optional;
#    skip if already present)
PYTHONPATH=python/credit python3 -m signals.pipeline

# 3. Static screenshot of Today View (no Streamlit needed)
PYTHONPATH=. python3 scripts/today_view_screenshot.py
#    → sprints/v4/today_screenshot.png

# 4. Build + execute the threshold-tuning notebook
PYTHONPATH=. python3 scripts/build_notebook_v4.py
jupyter nbconvert --to notebook --execute --inplace \
  notebooks/04_threshold_tuning.ipynb \
  --ExecutePreprocessor.timeout=180
#    → 3 plots in sprints/v4/plots/

# 5. Slider-latency benchmark (regenerates sprints/v4/slider_latency.csv)
#    — inline AppTest harness; see sprints/v4/notes.md for the snippet.

# 6. Test suite
PYTHONPATH=python/credit python3 -m pytest tests/ -q

# 7. Launch the dashboard (interactive)
streamlit run dashboard/app.py
#    → http://localhost:8501
```

## Next Steps

1. **Conditional realized-revert dashboard panel.** Add a panel that
   shows, for each (date, signal) HIGH cell, the realized z over
   the next N days. This is the empirical version of the v3
   half-life claim, restricted to actual HIGH dates. Would directly
   answer "is HIGH quality > MED quality > LOW quality" on out-of-
   sample windows.

2. **Live-data refresh button.** A "Refresh data" action that
   re-runs `signals.pipeline.build_with_rv()` would let the user
   advance "today" without leaving the app. Pre-req: yfinance /
   FRED rate-limit handling and a "data as of …" staleness banner.

3. **Best-method override per pair.** v3 picked Kalman by ADF; v4
   shows that's miscalibrated for daily trading. Add an OLS /
   Kalman / DV01 toggle inside the RV view so a user can see all
   three residuals overlaid. Sprint 5 should default to OLS.

4. **Visual regression tests.** Add a tiny Playwright-driven
   snapshot test that loads the app, drives the sliders, and
   compares rendered PNG against a baseline (with a tolerance
   threshold). Catches Plotly-version drift and palette regressions.

5. **Combined-portfolio conviction.** Roll the 6 cards into one
   per-day score (e.g. count of HIGH cards weighted by historical
   half-life). The current 6-card view is information-dense; a
   single rolled-up gauge would let the PM scan the past 30 days
   in seconds.

6. **Sprint v5 prep — costs + execution.** Before any backtest:
   borrow cost feed for HYG/LQD shorts, slippage model, a
   position state machine that consumes `entry/exit/stop` flags.
   The "position text" on each card is the natural API surface —
   it should serialize into an actual order ticket schema.
