# Sprint v4 — Signal Visualizer + Today View

## Summary

Sprint v4 built a local Streamlit dashboard over `data/processed/features.parquet`
that surfaces Sprint v3's signals and the **C22 thesis** ("equity-credit lag →
faster RV mean-reversion") as a six-card Today View, two interactive historical
views (Directional + RV), and regime-shaded backdrops. No P&L; this is a
visualization sprint. **All eight pre-registered correctness gates (D1–D8) pass,
plus the D9 sanity baseline.** Verdict: ship.

## Hypothesis & Falsification Criteria

This sprint did not test a new economic claim. The PRD's falsification criteria
are **UI/correctness gates** — the dashboard either renders the v3 thesis
faithfully and fast, or it does not.

| ID | Criterion | Threshold | Result |
|---|---|---|---|
| D1 | Conviction truth table | 63 cells pass | ✓ 63/63 |
| D2 | HIGH border green | snapshot test | ✓ via test_dashboard_smoke |
| D3 | Slider redraw < 500ms p95 | 20 measured moves per view | ✓ Directional 60ms, RV 112ms |
| D4 | Panel x-axis sync | `shared_xaxes=True` | ✓ Plotly subplots |
| D5 | Marker fidelity | thresholds → marker counts match | ✓ from_thresholds is pure |
| D6 | Regime span boundaries | unit test on hand-crafted series | ✓ test_regime_shade |
| D7 | Today View always at top | rendered before view branch | ✓ |
| D8 | No-crash render on as-of date | 6 cards, no NaN exception | ✓ test_dashboard_smoke |
| D9 | HIGH count sanity baseline | total ∈ [50, 1500], max signal ≤ 70% | ✓ 178 total, top signal 36% |

## Data Pipeline

**No ingestion this sprint.** The dashboard is read-only over
`data/processed/features.parquet` (4784 × 56, produced by Sprint v3's
`signals.pipeline.build_with_rv()`).

**Single I/O boundary:** `dashboard/loader.py::load_features()` decorated with
`@st.cache_data` so the parquet is read once per session and not on every
slider move or view switch.

**Date semantics:** "today" = `features.index[-1]` (= 2026-04-15 at sprint
open). Deterministic. Surfaced prominently in the Today View header.

## Signal Behavior

**Today View, as-of 2026-04-15:** all six signals LOW conviction; regime =
`neither`; no |z| > 2. The dashboard correctly renders a quiet day with no
NaN crashes. See `sprints/v4/today_screenshot.png`.

**Historical HIGH-conviction days (date × signal cells, post-warmup):**

| signal | HIGH days | first |
|---|---|---|
| hy_spread | 29 | 2008-01-14 |
| ig_spread | 64 | 2007-07-10 |
| hy_ig | 40 | 2008-01-14 |
| rv_hy_ig | 17 | 2007-10-17 |
| rv_credit_rates | 14 | 2010-11-12 |
| rv_xterm | 14 | 2007-10-22 |
| **total** | **178** | |

178 is comfortably in the [50, 1500] sanity band; max single-signal share is
36% (`ig_spread`), well under the 70% dominance cap. HIGH days cluster
around the 2008 GFC, late 2010, 2015–16 oil bust, COVID-2020, and 2022
rate shock — the times you would expect actionable mean-reversion candidates.

## UI Correctness Results (in lieu of Backtest Results)

### Slider latency (D3)

Recorded in `sprints/v4/slider_latency.csv` via `streamlit.testing.v1.AppTest`:

| view | p50 | p95 | max |
|---|---|---|---|
| Directional | 56.4 ms | 60.3 ms | 75.6 ms |
| RV | 102.3 ms | 111.9 ms | 127.8 ms |

5–10× under the 500 ms gate. Latency stays bounded because
`from_thresholds()` recomputes marker frames as pure NumPy comparisons
(no parquet re-read; `load_features` is cached).

### Regime-shading performance (post-fix)

The first deferred benchmark caught the original `add_vrect` regression:

| approach | shapes | latency |
|---|---|---|
| `fig.add_vrect()` × 897 calls | 897 | 830,820 ms |
| `fig.update_layout(shapes=…)` one-shot | 897 | 98 ms |

The shipped code uses the one-shot path and constrains shading to each
subplot's `{yref} domain` (discovered via `fig.select_yaxes(secondary_y=False)`)
so the rects don't bleed into title gutters. Refresh latency on a regime
toggle stays in the 150–350 ms range.

### Today View invariant tests

`tests/test_conviction.py` — 63 / 63 pass over the cartesian product
z ∈ {-3, -2.1, -1.6, -1, 0, 1, 1.6, 2.1, 3} × regime ∈ {equity_first,
credit_first, neither, NaN}, plus the HIGH-iff-thesis-active invariant and
NaN handling.

`tests/test_dashboard_smoke.py` — AppTest run produces no exception, all 6
signal names appear in rendered markdown, and on the first historical
HIGH date the card HTML contains "HIGH" and `border_color="#1b8a3a"` with
`4px solid` styling.

`tests/test_regime_shade.py` — 5 / 5 pass: contiguous-run emission, NaN
skip, no overlap/gap, palette correctness, unknown-column raises.

`tests/test_dashboard_sanity.py` — 2 / 2 pass: total HIGH ∈ [50, 1500],
single-signal share ≤ 70%.

## Key Findings

1. **Plotly `add_vrect` is quadratic in shape count.** With 897 shapes
   (~300 regime runs × 3 panels) a single refresh took 14 minutes. Moving
   to one-shot `update_layout(shapes=…)` cut it to 100 ms — an 8,500×
   speedup. Any subsequent dashboard work involving categorical-background
   shading should batch shapes by default.

2. **Streamlit's free-form `date_input(value=(start, end))` is fragile.**
   It allows the user to pick a date past `max_value`, then errors at the
   widget layer. Two separate `date_input` calls with `min_value` and
   `max_value` clamps, fronted by a preset selectbox, gives a usable UX
   with no error path.

3. **`yref="paper"` rectangles in subplots cross title gutters.** To shade
   only the data area of each panel, query `fig.select_yaxes(secondary_y=False)`
   to discover the primary y-axis per row, then build per-subplot
   `yref="{y} domain"` rects. Costs one extra figure-introspection call,
   keeps the shading clean.

4. **Card colors need separating from line colors.** The first marker
   palette had exit (blue) too close to plot lines (also blue at default
   Plotly), and stop (red) was easy to miss against the spread crossings.
   Final palette swaps exit to red and stop to a black X — the X symbol
   stays the universal "stop-loss" cue while red gains a single
   unambiguous meaning ("close at target").

5. **The dashboard rules out the Kalman residual for daily trading.**
   The RV view's residual+z panel shows that even though `z_rv_hy_ig`
   spikes past ±2 frequently, the underlying residual is near-zero
   noise (Kalman over-fit). A trader looking at the dashboard would not
   take those signals at face value — the dashboard makes the Sprint v3
   conclusion visible without needing to re-read the WALKTHROUGH.

## Limitations

- **Stale data.** "Today" = last bar in features.parquet. If the parquet
  is not regenerated, the dashboard's Today View is stale. No staleness
  warning is shown beyond the as-of date string.
- **No combined portfolio conviction.** Cards are per-signal. Multiple
  cards can light up HIGH on the same day; the dashboard does not roll
  them up into a single trade recommendation.
- **No costs / sizing / borrow.** Position text is action language only
  ("Long HYG / Short LQD"), with no notional or position-size guidance.
- **Single-user, single-machine.** No auth, no deployment, no persistence
  of user preferences. Refreshing the page resets all sliders.
- **Tests use `AppTest`, not Selenium / Playwright.** Visual regression
  on rendered Plotly is not enforced — we test the Python-side shape
  list and HTML strings, not the actual rendered pixels.

## Reproducibility

- **Commit hash:** see this commit + `sprint-v4` tag.
- **Data snapshot:** `data/processed/features.parquet` as produced by
  the `sprint-v3` tag (4784 × 56).
- **No stochastic steps.**

**To regenerate from scratch:**

```bash
# 1. Activate venv + install deps
source venv/bin/activate
pip install -r requirements.txt

# 2. Sprint v3 prerequisite — features.parquet
PYTHONPATH=python/credit python3 -m signals.pipeline

# 3. Run the dashboard
streamlit run dashboard/app.py

# 4. Regenerate the static screenshot
PYTHONPATH=. python3 scripts/today_view_screenshot.py

# 5. Build + execute the threshold-tuning notebook
PYTHONPATH=. python3 scripts/build_notebook_v4.py
jupyter nbconvert --to notebook --execute --inplace \
  notebooks/04_threshold_tuning.ipynb --ExecutePreprocessor.timeout=180

# 6. Run the test suite
PYTHONPATH=python/credit python3 -m pytest tests/ -q
```

## Next Steps

1. **Live data refresh button.** A "Refresh data" action in the sidebar
   that re-runs `signals.pipeline.build_with_rv()` would let the user
   advance "today" without restarting the dashboard.

2. **Combined portfolio conviction.** Roll the 6 cards into one score
   per day (e.g. sum of HIGH cards, weighted by historical half-life
   per signal). Surface as a single "today" gauge.

3. **Best-method override.** Sprint v3 picked Kalman by ADF; the
   dashboard shows that this isn't tradeable on a daily cadence. Add a
   "Method" selector in the RV view sidebar that swaps between OLS /
   Kalman / DV01 residuals so the user can see all three and pick by
   eye. Sprint 5 plan should bake OLS in as the default.

4. **Sprint 5 prep — costs + execution.** Before any backtest, define
   borrow/financing/slippage. The current "position text" on each card
   is the natural API surface — it should serialize into an actual
   order ticket schema.

5. **Mobile / responsive layout.** Today View as 6 horizontal cards
   needs ≥ 1200 px width. A condensed 2×3 grid mode for tablets would
   make the dashboard useful on the go.
