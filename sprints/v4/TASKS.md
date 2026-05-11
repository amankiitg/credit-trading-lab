# Sprint v4 — Tasks

Ten atomic tasks, each 10–20 minutes of focused work. Every UI task
is paired with either a unit test or a smoke-test acceptance.
Status starts at `[ ]`; flip to `[x]` as each lands.

Pre-req: `sprint-v3` tag checked out; `features.parquet` (4784 × 56)
present at `data/processed/`.

See `PRD.md` §Falsification Criteria for D1–D8 definitions.

---

- [x] **Task D1: Streamlit skeleton + deps + cached loader**
  - Acceptance: `dashboard/app.py` launches via
    `streamlit run dashboard/app.py` and shows a placeholder title
    + the as-of date from `features.parquet`. `dashboard/loader.py`
    exposes `load_features()` decorated with `@st.cache_data`.
    `streamlit` + `plotly` added to `requirements.txt`.
  - Files: `dashboard/app.py`, `dashboard/loader.py`,
    `requirements.txt`.
  - Validation: fails if the app errors on launch, if the as-of
    date shown doesn't equal `features.index[-1].date()`, or if
    `load_features` is not cached (uncached load shows visible
    re-read on every interaction).

- [x] **Task D2: Conviction logic + unit tests (D1 criterion)**
  - Acceptance: `dashboard/conviction.py` exposes pure functions
    `conviction(z, regime) -> Literal["HIGH","MED","LOW"]`,
    `z_color(z) -> str`, `arrow(z) -> str`, `border_color(tier) -> str`.
    `tests/test_conviction.py` covers the cartesian product
    z ∈ {-3, -2.1, -1.6, -1.0, 0, 1.0, 1.6, 2.1, 3} × regime ∈
    {equity_first, credit_first, neither, NaN} = 36 cases plus
    explicit "HIGH iff equity_first ∧ |z|>2" invariant test.
  - Files: `dashboard/conviction.py`, `tests/test_conviction.py`.
  - Validation: fails D1 if any cell mismatches the truth table;
    fails if `conviction` is not pure (has I/O, accesses globals,
    raises on NaN regime — should return "LOW").

- [x] **Task D3: Today View — 6 horizontal cards (D2, D7, D8)**
  - Acceptance: `dashboard/views/today.py::render(df)` produces a
    horizontal row of 6 cards (one per signal in
    PRD §Signal Definition table) using `st.columns(6)`. Each card
    shows: signal name, z-score with z-color, arrow, regime badge,
    conviction tier, position text. Card border thickness/color
    matches `border_color(conviction)`. Row renders at the top of
    every page (sticky / always visible). On `features.index[-1]`
    no card raises; missing values render as "—".
  - Files: `dashboard/views/today.py`, `dashboard/app.py` (wires
    Today View as a top section visible on all pages).
  - Validation: fails D2 if a HIGH card lacks a green border;
    fails D7 if Today View is hidden on any view; fails D8 if any
    of the 6 cards raises on the last available date.

- [x] **Task D4: Historical Directional view — 3 synced panels (D4, D5)**
  - Acceptance: `dashboard/views/directional.py::render(df, controls)`
    returns a Plotly figure with 3 vertically stacked subplots
    sharing the x-axis, one per spread in {hy_spread, ig_spread,
    hy_ig}. Each subplot shows the spread line + its `_z63` overlay
    on a secondary y-axis + 4 scatter overlays from
    `components/markers.py::from_flags` mapped to entry_long /
    entry_short / exit / stop (one shape/color per flag). Hover
    crosshair shared across all three panels.
  - Files: `dashboard/views/directional.py`,
    `dashboard/components/markers.py`.
  - Validation: fails D4 if x-axes are not shared (panel ranges
    drift independently); fails D5 if marker counts differ from
    `df[f"{spread}_{flag}"].sum()` for any (spread, flag).

- [x] **Task D5: Historical RV view — 4-panel pair detail**
  - Acceptance: `dashboard/views/rv.py::render(df, pair, controls)`
    renders four stacked panels for the selected pair:
    (1) leg-1 vs leg-2 dual-axis, (2) hedge-ratio time-series from
    the appropriate `hedge_ratio_*` column (or recomputed for the
    selected method if a method selector is exposed), (3) residual
    + z-score with |z|>1.5 highlight markers, (4) static text strip
    showing OU half-life, β mean/std, last-63d CV, ADF p-value.
    The pair selector is wired to the sidebar.
  - Files: `dashboard/views/rv.py`.
  - Validation: fails if any of the 4 panels is missing; fails if
    the static stats strip does not match values recomputed inline
    via `signals.halflife.ou_halflife` and `statsmodels.adfuller`
    (within 1e-6 of the values surfaced in the dashboard).

- [x] **Task D6: Regime shading overlay (D6)**
  - Acceptance: `dashboard/components/regime_shade.py::spans(df,
    regime_col)` returns a list of `(start_date, end_date, label,
    color)` tuples covering every contiguous run of the chosen
    regime label. Color map: vol_regime → {high: light-red, low:
    light-blue}; equity_credit_lag → {equity_first: light-yellow,
    credit_first: light-purple, neither: light-gray}. The sidebar
    exposes a `regime_shading` selector
    {none, vol_regime, equity_credit_lag} that adds `add_vrect`
    shapes to both Historical Directional and Historical RV figures.
  - Files: `dashboard/components/regime_shade.py`,
    sidebar updates in `dashboard/app.py`.
  - Validation: fails D6 if `spans` returns overlapping or gapped
    ranges (unit test on a hand-crafted regime series); fails if
    the selector "none" still draws spans; fails if changing the
    selector does not update both historical views.

- [x] **Task D7: Threshold sliders + < 500ms redraw (D3)**
  - Acceptance: Sidebar exposes three `st.slider`s for entry, exit,
    stop (defaults 2.0, 0.5, 4.0). Moving any slider recomputes
    the entry/exit/stop marker overlays on Historical Directional
    in < 500ms p95 across 20 measured moves (timed in code via
    `time.perf_counter` around the figure rebuild; logged to
    `sprints/v4/slider_latency.csv`). Marker recomputation uses a
    pure function in `dashboard/components/markers.py` (no
    re-reading the parquet).
  - Files: `dashboard/components/markers.py` (extended),
    `dashboard/app.py` (slider plumbing),
    `sprints/v4/slider_latency.csv`.
  - Validation: fails D3 if p95 of the 20 recorded redraws is ≥
    500ms; fails if `load_features` is called on slider change
    (only the marker frame should recompute).

- [x] **Task D8: Smoke + snapshot test (D2, D7, D8)**
  - Acceptance: `tests/test_dashboard_smoke.py` uses Streamlit's
    `AppTest` framework (built-in headless test harness, no
    selenium needed) to: (a) import and run `dashboard/app.py`,
    (b) assert the Today View row renders 6 cards on the last
    date, (c) on a known historical HIGH-conviction date (selected
    by querying `features.parquet` for rows where
    `equity_credit_lag == 'equity_first'` AND `|z_rv_hy_ig| > 2`,
    then picking the first such date), assert the corresponding
    card shows tier=HIGH and `border_color="#1b8a3a"`.
  - Files: `tests/test_dashboard_smoke.py`.
  - Validation: fails D2/D7/D8 if any of the three assertions fail;
    fails if the test depends on a manual fixture not produced from
    `features.parquet`.

- [x] **Task D9: Sanity baseline — HIGH-card-count sanity check**
  - Acceptance: `tests/test_dashboard_sanity.py` computes, for the
    full features.parquet, the count of (date × signal) cells
    where conviction would be HIGH; asserts it falls in
    [50, 1500]. Rationale: too few = thesis-active threshold is
    set so tight nothing fires (broken or miscalibrated); too many
    = thesis-active fires on a majority of days (no discrimination,
    or regime-label bug). Also prints a small table of counts per
    signal so the reader can spot a single misbehaving column.
  - Files: `tests/test_dashboard_sanity.py`.
  - Validation: fails if total HIGH count is outside [50, 1500];
    fails if any single signal contributes >70% of total HIGH
    (regime/z calculation likely wrong on that column).

- [x] **Task D10: Sprint close — walkthrough + commit + tag**
  - Acceptance: `sprints/v4/notes.md` records per-task findings,
    measured slider latency p50/p95, the HIGH-cell count from D9,
    and a screenshot of the Today View on `today_date`.
    `sprints/v4/WALKTHROUGH.md` written per `/quant-walkthrough`,
    framed as a dashboarding sprint (replace Backtest Results
    section with a UI-correctness section keyed to D1–D8). Full
    test suite green: prior sprints unchanged; v4 new tests pass
    (D1 conviction, D8 smoke, D9 sanity, and `regime_shade.spans`
    unit test from D6). Commit, tag `sprint-v4`, push.
  - Files: `sprints/v4/notes.md`, `sprints/v4/WALKTHROUGH.md`,
    `sprints/v4/today_screenshot.png`.
  - Validation: fails sprint if any of D1–D8 fail, if any prior
    sprint test regresses, if the walkthrough omits any required
    section, or if the slider latency CSV shows p95 ≥ 500ms.
    Promote to tag `sprint-v4` only when all green.
