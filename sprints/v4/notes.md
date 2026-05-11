# Sprint v4 — Notes

## D1–D2 — Skeleton + conviction logic

- `dashboard/loader.py`: cached `load_features()`, single I/O boundary.
- `dashboard/conviction.py`: pure functions
  `conviction / z_color / arrow / border_color / regime_badge_color`.
- `dashboard/signal_specs.py`: ordered tuple of 6 `SignalSpec` rows
  (3 directional + 3 RV) + `position_text()` mapper.
- `tests/test_conviction.py`: **63 / 63 PASS**, covering the 36-cell
  z × regime cartesian + NaN handling + helper color/arrow mappings.

## D3 — Today View

- `dashboard/views/today.py::render()` emits a `st.columns(6)` row of
  HTML cards (custom inline-styled `<div>` for full color control of
  border / badge / arrow).
- Mounted in `dashboard/app.py` **before** the family branch — so the
  Today View renders on every page (D7 persistence).
- On the as-of date 2026-04-15, every card is `LOW` (no |z|>2,
  regime=neither): correct quiet-day rendering, no NaN crash (D8).

## D4–D5 — Historical Directional + RV

- `dashboard/views/directional.py`: 3 vertically stacked Plotly
  subplots with `shared_xaxes=True`, `hovermode="x unified"`, dual
  y-axes per panel (spread + z63). Markers from
  `components/markers.py::from_thresholds(z, entry, exit_t, stop)`
  — i.e. recomputed from the sliders, not from the cached flag
  columns, so the slider has real effect.
- `dashboard/views/rv.py`: 3 stacked Plotly subplots (legs, hedge
  ratio, residual+z) plus a 4-column `st.metric` strip
  (half-life, β mean/std, last-63d CV, ADF p). RV pair 2 / 3 load
  the rates leg from `credit_market_data.parquet` via a
  `@st.cache_data` shim.

## D6 — Regime shading

- `dashboard/components/regime_shade.py::spans()` walks the regime
  column emitting `(start, end, label, color)` for every contiguous
  run. NaN labels are skipped (intentional gaps).
- Palette: `vol_regime` → red/blue; `equity_credit_lag` →
  orange/purple/gray. Both at low alpha (0.08–0.15) so the line is
  still legible on top.
- `apply_shading(fig, df, regime_col)` adds `add_vrect` shapes
  (`layer="below"`) on every subplot of a Plotly figure.
- `tests/test_regime_shade.py`: **5 / 5 PASS** — contiguous runs,
  NaN skip, no overlap/gap, palette correctness, unknown-column error.

## D7 — Slider latency

`sprints/v4/slider_latency.csv` records 20 entry-slider moves on each
view, measured via `AppTest`:

| view | p50 | p95 | max |
|---|---|---|---|
| Directional | 56.4 ms | 60.3 ms | 75.6 ms |
| RV | 102.3 ms | 111.9 ms | 127.8 ms |

Both are ~5–10× under the 500 ms PRD gate.

## D8 — Smoke / snapshot

`tests/test_dashboard_smoke.py`: AppTest runs `dashboard/app.py`,
asserts no exception, all 6 signal names appear in rendered markdown,
and (independently) a known historical HIGH date renders a card
HTML containing `HIGH` + `4px solid #1b8a3a`.

## D9 — Sanity baseline

`tests/test_dashboard_sanity.py`:

| signal | HIGH days |
|---|---|
| hy_spread | 29 |
| ig_spread | 64 |
| hy_ig | 40 |
| rv_hy_ig | 17 |
| rv_credit_rates | 14 |
| rv_xterm | 14 |
| **total** | **178** |

178 ∈ [50, 1500] ✓. Max single signal contribution = 64 / 178 = 36%
≤ 70% ✓.

## Visual QA (notebook 04_threshold_tuning.ipynb)

3 plots in `sprints/v4/plots/`:
- `01_z_density_by_regime.png` — z-score density per signal × regime;
  the three regime densities overlap heavily, confirming why we
  gate on **both** `|z|>2` AND `equity_first` rather than either alone.
- `02_threshold_sweep.png` — HIGH and MED-or-HIGH counts as `entry`
  sweeps 1.0 → 3.5. Roughly exponential decay; doubling the
  threshold cuts HIGH count ~10×.
- `03_regime_shaded_panels.png` — `hy_spread` shaded by `vol_regime`
  and `rv_hy_ig_residual` shaded by `equity_credit_lag`. The 2008
  GFC spike sits squarely in a `high vol` red span.

## Test sweep (full repo)

- C++ Catch2: 38 / 38 (unchanged).
- pytest: **121 / 124** pass. The 3 failures are pre-registered
  Sprint v3 honest failures (`equity_regime` 74% bull,
  `equity_credit_lag` 85% neither, OLS hedge-ratio CV crossings).
  **Zero new regressions from Sprint v4.**

## Post-PRD UX iterations (during the live preview)

Three issues surfaced when running `streamlit run dashboard/app.py`
and were patched the same session:

1. **Regime shading hung on `equity_credit_lag`.** Deferred benchmark
   showed `fig.add_vrect()` × 897 shapes (299 spans × 3 panels) took
   **830,820 ms**; `fig.update_layout(shapes=...)` with the same 897
   shapes ran in **98 ms** — an 8,500× speedup. Root cause: every
   `add_vrect` call re-serializes the entire figure state, so cost
   grows quadratically with shape count. Patched in
   `dashboard/components/regime_shade.py::apply_shading`.

2. **Date-range picker errored on "Last 1y".** The free-form
   `st.date_input(value=(start, end))` widget allowed users to pick
   beyond `features.index[-1]`, which then exceeded `max_value`.
   Replaced with a `date_preset` selectbox
   (Full / Last 10y / Last 5y / Last 1y / Last 6m / Custom) feeding
   two separate `st.date_input` widgets, each clamped to
   `[features.index[0], features.index[-1]]`. Auto-swaps if user
   inverts start/end. Patched in `dashboard/app.py`.

3. **Shading bled into subplot title gutters.** First fix used
   `yref="paper"` for one full-figure-height rect per span — fast,
   but the rect crossed the title gaps between subplots. Final fix
   discovers each subplot's primary y-axis via
   `fig.select_yaxes(secondary_y=False)` and emits per-subplot
   `yref="{yaxis} domain"` rects, so shading sits inside each
   plotting region only. Latency stayed under 350 ms.

4. **Marker color clash (exit vs stop).** Both were red-ish (exit
   blue, stop red) — user feedback said exit should be more
   distinctive. Final palette: entry_long = green ↑, entry_short =
   amber ↓, exit = red ● (take profit), stop = black ✕ (stop-loss).
   The X symbol stays the universal "kill" cue while red is
   exclusively for closing trades on success.

## D10 — sprint close

- `sprints/v4/today_screenshot.png` — matplotlib-rendered snapshot
  of the 6 Today-View cards on 2026-04-15. All LOW (quiet day,
  regime = neither, no |z|>2). Faithful to the live app palette.
- `scripts/today_view_screenshot.py` — reproducible regen.
- `sprints/v4/WALKTHROUGH.md` — research-report style summary
  per `/quant-walkthrough` (UI-correctness section keyed to D1–D8
  in place of a Backtest Results section).
- Full test suite: 121/124 pytest pass; 3 failures are pre-registered
  Sprint v3 honest failures (no v4 regression).
- C++ Catch2: 38/38 (unchanged).
- Commit + tag `sprint-v4`, push.
