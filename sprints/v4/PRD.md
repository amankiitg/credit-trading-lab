# Sprint v4 — Signal Visualizer + Today View

Weeks 8-9. Sprint 1 delivered directional spreads + flags + z-scores;
Sprint 2 delivered a C++ pricer; Sprint 3 delivered RV residuals,
regime classifiers, and confirmed the equity-credit lag thesis (C22).
Sprint 4 builds the **read-only dashboard** that lets a discretionary
PM (or future-us) act on those signals at a glance, with the v3
thesis baked into a single "conviction" badge.

## Overview

Build a local Streamlit dashboard over `data/processed/features.parquet`.
Three views — Today (always visible at top), Historical Directional,
Historical RV — surface the Sprint-1 spread signals and the Sprint-3
RV residuals with their regime context. The Today View encodes
Sprint v3's central thesis as a single boolean: **HIGH conviction
iff `equity_credit_lag == 'equity_first'` AND `|z| > 2`**. No P&L, no
backtest, no live data feeds — features.parquet is the only data
source and "today" = the last bar in the file.

## Economic Hypothesis

This sprint does not test a new economic claim; it operationalizes
the one Sprint v3 already confirmed. Sprint v3 §C22 showed that for
RV1 (HY/IG residual) the half-life on `equity_first` regime days is
**42.8% shorter** than on `neither` days (Kalman, the PRD-best
method), and 67% shorter on OLS. The Today View asks: *is today an
`equity_first` day, and is the residual extended (|z| > 2)?* If yes,
the thesis predicts a faster-than-baseline snap back → trade with
higher conviction.

The conviction tiers are not new edge — they are a labeling rule
that maps z-scores and regime labels onto a UI primitive a PM can
read in one second.

## Falsification Criteria

Pre-registered. These are **correctness gates**, not statistical
edges (Sprint v3 already ran the statistics).

- **D1 — Conviction logic invariant.** `conviction(z, regime)` returns
  `HIGH` if and only if `regime == "equity_first"` AND `|z| > 2`,
  `MED` if `|z| > 2` OR (`|z| > 1.5` AND `regime == "equity_first"`)
  but not both HIGH conditions, `LOW` otherwise. Unit-tested over
  the cartesian product of z ∈ {-3, -2.1, -1.6, -1.0, 0, 1.0, 1.6,
  2.1, 3} × regime ∈ {equity_first, credit_first, neither}.
- **D2 — Thesis-active border.** Card border color is `green` iff
  conviction == HIGH on the selected as-of date. Tested in a
  snapshot test on a known HIGH date from Sprint v3.
- **D3 — Slider latency.** After moving any threshold slider, the
  affected chart re-renders in < 500ms on `features.parquet`
  (4784 × 56) measured via Streamlit's session-state timing.
- **D4 — Panel synchronization.** In Historical Directional, the
  three panels (hy_spread, ig_spread, hy_ig) share an x-axis; a
  mouseover hover-line shows the same date across all three.
- **D5 — Marker fidelity.** Entry/exit/stop markers plotted on
  Historical Directional and Historical RV charts match the
  underlying flag columns (`*_entry_long`, `*_entry_short`,
  `*_exit`, `*_stop`) exactly — same count, same dates, no
  off-by-one.
- **D6 — Regime shading correctness.** Regime background spans
  begin and end exactly at the boundaries of the regime label
  series; no shifted spans, no gaps. Spot-check 3 transitions.
- **D7 — Today View persistence.** Today View renders at the top
  on all three pages and never scrolls out of immediate view.
- **D8 — No-crash invariant.** All 6 cards render on the last
  available date with no `NaN`/`None` crashes; any genuinely
  missing data shows an explicit `—` placeholder.

## Signal Definition

### "Today" semantics

`today_date = features.index[-1]` (= 2026-04-15 at sprint open).
Deterministic; no live data pulls.

### Six Today-View cards

Three directional spread signals + three RV residual signals. Per
card, all values are looked up at `today_date`:

| signal | z-source | regime-source | position direction |
|---|---|---|---|
| `hy_spread` | `hy_spread_z63` | `equity_credit_lag` | long HY if z < -2, short HY if z > +2 |
| `ig_spread` | `ig_spread_z63` | `equity_credit_lag` | long IG if z < -2, short IG if z > +2 |
| `hy_ig` | `hy_ig_z63` | `equity_credit_lag` | long HY / short LQD if z < -2, opposite if z > +2 |
| `rv_hy_ig` | `z_rv_hy_ig` | `equity_credit_lag` | sell residual (short HY, long IG·β) if z > +2; opposite if z < -2 |
| `rv_credit_rates` | `z_rv_credit_rates` | `equity_credit_lag` | sell residual (short HY, long DGS10·β) if z > +2 |
| `rv_xterm` | `z_rv_xterm` | `equity_credit_lag` | sell residual (short HY-IG, long slope·β) if z > +2 |

### Conviction tiers (pure function)

```
def conviction(z: float, regime: str) -> Literal["HIGH","MED","LOW"]:
    if abs(z) > 2 and regime == "equity_first":
        return "HIGH"        # thesis active
    if abs(z) > 2 or (abs(z) > 1.5 and regime == "equity_first"):
        return "MED"
    return "LOW"
```

### z-score cell color

| |z| range | color |
|---|---|---|
| |z| ≥ 2.0 | green (`#1b8a3a`) |
| 1.0 ≤ |z| < 2.0 | yellow (`#d9a116`) |
| |z| < 1.0 | red (`#b04040`) |

(Red here means "no actionable signal," not "warning." Yellow is
"watch list." Green is "extreme enough to trade.")

### Arrow

- z > 0 (residual / spread is rich vs hedge) → ↓ (expect snap down)
- z < 0 (cheap vs hedge) → ↑ (expect snap up)
- |z| < 1 → · (flat)

### Position text

Free-form per signal, e.g.:

- `hy_ig` z = +2.3 → "Short HYG, Long LQD (mean-revert)"
- `rv_hy_ig` z = -2.5, β = 0.46 → "Long HY, Short 0.46×IG (β-hedged)"
- z within band → "No trade"

### Card border

- HIGH conviction → solid green border (4px)
- MED → yellow border (2px)
- LOW → gray border (1px)

### Regime badge

Single-line chip next to the signal name displaying the current
`equity_credit_lag` label with a fixed color map:

- `equity_first` → orange chip
- `credit_first` → purple chip
- `neither` → gray chip
- NaN → "—" chip

### Three views

**Today View** — always visible at the top of every page (sticky
row of 6 cards, horizontal).

**Historical Directional** — three vertically stacked panels for
`hy_spread`, `ig_spread`, `hy_ig`. Each panel shows the spread
series + its `_z63` overlay on a secondary axis + entry/exit/stop
markers from the four boolean flag columns. Shared x-axis with
synchronized hover crosshair. Optional regime background shading
(see §controls).

**Historical RV** — pick a pair from {rv_hy_ig, rv_credit_rates,
rv_xterm}. Four stacked panels:
1. The two legs on the same axes (y left = leg-1, y right = leg-2)
2. Hedge-ratio time-series (best method)
3. Residual + z-score, with |z| > 1.5 highlighted
4. Static text strip: half-life, β mean/std, last-63d CV, ADF p-value

Optional regime shading.

### Controls (right sidebar)

- **Date range** — start/end date pickers; applies to historical
  views only (Today View always shows latest).
- **Family** — directional / RV (selects which historical view).
- **Spread / pair** — driven by Family (hy_spread/ig_spread/hy_ig
  for directional; rv_hy_ig/rv_credit_rates/rv_xterm for RV).
- **Threshold sliders** — `entry`, `exit`, `stop` (z-score units);
  defaults from Sprint 1 (2.0, 0.5, 4.0). Drives the marker
  placement on historical charts. Must redraw < 500ms.
- **Regime shading** — `none` / `vol_regime` / `equity_credit_lag`.
  Updates background spans on both historical views.

## Data

| artifact | source | role |
|---|---|---|
| `data/processed/features.parquet` | sprint-v3 | only data source — 4784 × 56 |

No ingestion this sprint. The dashboard is **read-only**; if the
file is regenerated by `signals.pipeline.build_with_rv()`, the
dashboard automatically picks up the new bars on next launch.

**Known biases (inherited from v1–v3):**

- "Today" = last bar in features.parquet; if the parquet is stale,
  the dashboard is stale. Surface the as-of date prominently.
- Regime labels are computed from trailing windows (vol_regime
  uses an expanding median that weakly leaks past-history). Same
  caveat as Sprint 3.
- Sprint-3 best-method selector picked Kalman, whose residuals are
  near-zero noise. Z-scores can therefore spike past ±2 frequently
  even when there's nothing meaningful happening. This dashboard
  surfaces what the parquet contains; it does not re-validate.
- No corporate-action handling beyond what ETF adjusted-close
  series already encode.

## Success Metrics

Pass D1–D8.

| metric | target | source |
|---|---|---|
| Conviction unit-test coverage | 27 cartesian cells, 100% pass | D1 |
| Thesis-active border correctness | snapshot test on known HIGH date | D2 |
| Slider redraw | < 500ms p95 over 20 slider moves | D3 |
| Panel x-axis sync | shared range object | D4 |
| Marker count match | exact equality vs flag columns | D5 |
| Regime span boundaries | 3 spot-checks pass | D6 |
| Today View visible on all pages | manual + screenshot | D7 |
| Card render on `today_date` | no exceptions, 6/6 cards | D8 |

**Operational:**

- `streamlit run dashboard/app.py` launches in < 5s cold.
- All UI runs locally; no auth, no network requests except the
  initial parquet read.

## Research Architecture

```
dashboard/
  app.py              -- Streamlit entrypoint, sidebar, page router
  conviction.py       -- pure conviction() function + tiers + color map
  loader.py           -- single cached features.parquet reader
  views/
    today.py          -- 6-card horizontal row (the sticky header)
    directional.py    -- 3-panel historical directional chart
    rv.py             -- 4-panel historical RV chart
  components/
    regime_shade.py   -- Plotly shape generator for regime backgrounds
    markers.py        -- Plotly scatter overlays from flag columns

tests/
  test_conviction.py        -- D1 unit tests (27 cells)
  test_dashboard_smoke.py   -- app imports, fixture-driven render
  test_slider_latency.py    -- D3 timing under simulated reruns
```

**Data flow:**

1. `loader.load_features()` → cached `pd.DataFrame` of features.parquet.
2. `views.today.render(df)` → 6 cards. Pulls last-row values for
   each (signal, z-source, regime-source); calls `conviction()`.
3. `views.directional.render(df, controls)` → Plotly figure with
   3 synced panels.
4. `views.rv.render(df, controls)` → Plotly figure with 4 panels
   for the selected pair.
5. `components.regime_shade.spans(df, regime_col)` → list of
   `(start, end, label, color)` for `add_vrect` calls.
6. `components.markers.from_flags(df, signal)` → scatter traces
   for entry_long / entry_short / exit / stop.

## Risks & Biases

- **"Today" is yesterday-of-yesterday.** features.parquet is
  updated by hand. Surface as-of date and stale-warning banner if
  > 5 trading days old.
- **Streamlit rerun model.** Streamlit re-executes the script on
  every input change. Cache the parquet load and figure generation
  via `@st.cache_data` keyed by (date_range, family, pair,
  thresholds, regime_shading) to hit the < 500ms gate.
- **Plotly redraw on large series.** 4784 rows × 2-3 traces per
  panel = 10k+ points. Use Plotly's `scattergl` for the line
  layers if `scatter` lags.
- **Visual regression.** No snapshot baseline yet; the snapshot
  test (D2) seeds the baseline on first run.
- **Thresholds are global.** Moving the slider re-classifies
  markers across the whole 19-year sample. Document that this is
  exploratory, not predictive.
- **Conviction is per-signal, not portfolio.** Multiple signals
  may be HIGH on the same day; the dashboard does not combine
  them.

## Out of Scope

- P&L, Sharpe, backtest, position sizing. (Sprint 5.)
- Live data ingestion. (We read the parquet, full stop.)
- Multi-user auth, deployment, hosting.
- Mobile / responsive design — desktop browser only.
- Persisting user preferences across sessions.
- Walk-forward / as-of-date scrubbing (one of the options surveyed
  but rejected for scope).
- Combining signals into a single portfolio conviction.
- Sound / push notifications when thesis activates.

## Dependencies

**New (added to `requirements.txt`):**
- `streamlit` (≥ 1.31)
- `plotly` (≥ 5.18)
- `streamlit-extras` (optional; only if needed for sticky header)

**Existing:** `pandas`, `numpy`, `pyarrow`, `pytest`.

**Prior sprint outputs:**
- `sprint-v3` tag: `features.parquet` (4784 × 56) with all RV
  residuals, regime labels, z_rv columns populated.
