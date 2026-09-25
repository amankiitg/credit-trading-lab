# Sprint v9.5 -- Render dashboard redeploys and memory

Two problems from the Render dashboard logs: it redeployed on every push, and it
restarted once at 2026-09-25 02:38 right after a page load with no traceback.

## 1. Redeploys on every push

Every push to `main` rebuilt all three services, so a commit that only touched the
cron jobs, the notebooks or the sprint notes restarted the dashboard and dropped
every open session for no reason.

`render.yaml` now gives the dashboard a `buildFilter.paths` list: `dashboard/**`,
`signals/**`, `risk/**`, `pyproject.toml`, `requirements.txt` and
`scripts/write_render_secrets.py`. That last one is there because the build command
runs it, so a change to it does change what this service builds. `signals/**` and
`risk/**` are there because the remaining tab imports them at runtime, which was
measured rather than guessed: rendering the page and listing the local modules pulled
in gives `signals.etf_universe` (directly and through
`dashboard.loader.load_close_matrix`) and `risk.live_risk` for the MCTR panel.
`execution/**` is deliberately absent, which is what keeps the Alpaca keys off this
service. Only a change to one of those paths triggers a dashboard build now.

The two cron services were left alone; they were not part of the report.

## 2. Memory, measured first

`scripts/measure_dashboard_memory.py` is new. It measures one page per process,
because `ru_maxrss` is a high-water mark that never falls and measuring several pages
together would report the running maximum rather than each page's own peak. It drives
each view through `AppTest`, so the real render path is exercised, and it reruns the
page a few times because a rerun is what a long-lived session does.

**The peak RSS metric turned out not to be usable for judging this work.** Measuring
the same unchanged code twice, minutes apart, moved the median by up to 87 MiB:

| page | identical code, run 1 | identical code, run 2 | drift |
|---|---|---|---|
| attribution | 305.2 | 341.9 | +36.7 |
| operational | 269.2 | 349.2 | +80.0 |
| research_history | 222.3 | 259.4 | +37.1 |
| everything | 329.0 | 416.0 | +87.0 |

A first single-run comparison appeared to show reductions of 34 to 74 MiB, and a
repeat showed the opposite. Both were noise. So no peak-RSS claim is made here. What
the measurements did establish is that there is **no leak**: RSS across five reruns
oscillates in a band and ends where it started (research_history: 178.5, 126.4, 162.2,
168.8, 176.5 MiB). The baseline is simply high (about 145 MiB before any view runs:
Streamlit ~75 MiB, pandas and matplotlib ~70 MiB), plus render churn.

Where the data is concerned there was nothing to win: every source the dashboard reads
totals about 12 MiB in memory, and `tracemalloc` shows under 20 MiB of Python objects
against 200 MiB of RSS growth, meaning the rest is native buffers.

So the work was targeted with deterministic counters instead, added to the harness:
plotted points, PNG bytes shipped, and Supabase reads per rerun.

## What changed

  - **`dashboard/components/downsample.py`** (new). Min/max bucketing to about 1,200
    points, used by the long-series charts in `attribution.py` and
    `research_history.py`. Min/max rather than striding, because a spike is often the
    point of the chart, and the endpoints are always kept. It affects the plotted
    polyline only: every caption, metric and table still reads the full frame.
  - **Caching.** Four Supabase reads were being made on every rerun from inside
    `render()`; they now go through `st.cache_data(ttl=300)`, matching the TTL the
    proposed-trade panel already used. `operational.py` also called
    `load_universe_close()` directly from the MCTR panel, re-reading eight parquets and
    rebuilding the matrix per rerun, which `dashboard/loader.load_close_matrix` now
    caches.
  - **`plt.close`.** Already correct: 14 figures created, 14 closed, 0 left open on
    every page both before and after. Nothing was fixed here; a test now pins it
    instead, so a new unclosed figure fails the suite.

## Result, in deterministic terms

| page | plotted points before | after | change | PNG before | PNG after | Supabase reads per rerun |
|---|---|---|---|---|---|---|
| attribution | 85,292 | 20,945 | -75% | 544 KiB | 579 KiB | 0 -> 0 |
| research_history | 8,492 | 1,294 | -85% | 60 KiB | 60 KiB | 0 -> 0 |
| operational | 58 | 58 | none | 118 KiB | 119 KiB | 4 -> 0 |
| everything (all three tabs) | 93,842 | 22,297 | -76% | 722 KiB | 759 KiB | 4 -> 0 |

Figures rendered are unchanged (7, 2, 4 and 13), so no panel was dropped, and all four
pages render with zero exceptions.

One honest negative: the PNG payload did not shrink, it grew about 5%. Min/max
bucketing produces a sawtooth that compresses slightly worse than a smooth polyline
even though it draws the same sub-pixel envelope. The saving is render work, not bytes
on the wire, so it should not be described as a payload reduction.

## 3. `st.components.v1.html` -> `st.iframe`

`dashboard/app.py` was the only user, for an inline script that clicks the second tab
after a fresh sign-in. It now calls `st.iframe(...)` with the same HTML.
`st.components.v1.html` is deprecated and Streamlit's own deprecation message says it
will be removed after 2026-06-01.

Two things found while doing it, both verified against streamlit 1.61.1:

  - **`height=0` is rejected.** `st.iframe` requires a positive integer, `stretch` or
    `content`, so the old `height=0` had to become `height=1`. A direct translation
    raises `StreamlitInvalidHeightError`.
  - **`st.tabs` now takes `default=`.** That means the JavaScript is no longer needed
    at all: the tab could simply be opened with `default="Trade Approval"`, removing
    the iframe and the cross-document script entirely. Not done here because it changes
    UI behaviour and was not what was asked for.

## Verification

514 tests pass. The 11 failures and 2 errors are pre-existing and all trace to the
unbuilt `pycredit` C++ extension. No dashboard service was deployed or restarted by
hand.

## Removing the Strategy and Research tabs

The dashboard is now the Trade Approval book alone.

The tab to view mapping was unambiguous, so nothing needed confirming:

| tab label | view module | outcome |
|---|---|---|
| Strategy Analytics | `dashboard/views/attribution.py` | removed |
| Trade Approval | `dashboard/views/operational.py` | kept, the only tab left |
| Research Archive | `dashboard/views/research_history.py` | removed |

`app.py` now declares `st.tabs(["Trade Approval"])`. The tab wrapper was kept around
the single view so the structure is unchanged if a tab is added back, though with one
tab it is now redundant and could be dropped.

**The injected script is gone, not fixed.** It clicked `tabs[1]` to reach Trade
Approval after a fresh sign-in, because that is where the sign-in button lived. With
one tab there is nothing to switch to, so the script has no purpose: there is no
`tabs[1]` to select and a signed-in user is already looking at the only tab. Its
`st.iframe` call and its `_was_logged_in` session marker went with it, which also
removes the last use of the deprecated component namespace from the codebase.

### Deleted

Imports were checked repo-wide before anything was removed.

  - `dashboard/views/attribution.py`, `dashboard/views/research_history.py` - each was
    imported only by the tab that was removed.
  - `dashboard/views/directional.py`, `dashboard/views/rv.py` - nothing imported either
    before this change; they were already unreachable from the app.
  - `dashboard/components/markers.py` - imported only by those two.
  - `dashboard/components/downsample.py` - its only consumers were the two removed
    pages. It is deleted rather than kept as a utility, because nothing uses it now;
    if a chart-heavy tab comes back, this is the thing to bring back with it.

Kept, deliberately, because something outside the app still needs them:

  - `dashboard/loader.py` - `load_close_matrix` for the remaining tab, and
    `load_features` for `tests/test_canonical.py`.
  - `dashboard/components/regime_shade.py` - `tests/test_regime_shade.py` and
    `scripts/build_notebook_v4.py`.
  - `dashboard/views/today.py` with `conviction.py` and `signal_specs.py` - not
    reachable from the app, but referenced by `tests/test_dashboard_smoke.py`,
    `tests/test_dashboard_sanity.py`, `tests/test_conviction.py`,
    `scripts/build_notebook_v4.py` and `scripts/today_view_screenshot.py`. They are
    dead product code kept alive by their tests, which is a separate decision.
  - `dashboard/supabase_client.py` - the remaining tab and both cron jobs.

### Tests

`tests/test_dashboard_memory.py` was removed with the helper it tested. Its
replacement, `tests/test_dashboard_shell.py`, renders the real `dashboard/app.py`
through `AppTest` and asserts it produces no exceptions, asserts the tab bar is
exactly `["Trade Approval"]`, asserts the removed modules are gone and that the shared
ones were kept, and keeps the two hygiene checks (no legacy component, every figure
closed). The tab-label assertion is strict on purpose: before the change the same
call reported all three labels, so it demonstrably bites.

`GOOGLE_CLIENT_ID` is removed in the test before rendering. `app.py` rewrites
`.streamlit/secrets.toml` when that variable is set, and a test must never overwrite a
developer's local secrets file.

### Measurements after the removal

`scripts/measure_dashboard_memory.py` now measures the real `app.py` as well as the
view, so the app's own overhead is included.

| page | figures | line points | PNG | Supabase reads cold | warm | open figures | errors |
|---|---|---|---|---|---|---|---|
| app.py | 4 | 58 | 119 KiB | 6 | 0 | 0 | 0 |
| operational (view) | 4 | 58 | 119 KiB | 6 | 0 | 0 | 0 |

The app and the view are identical, so nothing in the shell adds render work.

Against the three-tab dashboard measured in the previous commit, the same counters go
from 13 figures, 93,842 line points and 722 KiB of PNG per load to 4 figures, 58 points
and 119 KiB: **99.9% fewer plotted points and 84% less PNG**. Removing the two heavy
pages removed essentially all of the plotting, which is a larger saving than the
downsampling it now replaces.

Peak RSS is 316 MiB median and 326 MiB max, essentially unchanged, and that is the
expected result: the peak is dominated by the roughly 145 MiB import baseline, not by
rendering. RSS across reruns rises once and then plateaus (281, 321, 322 MiB), which is
the same no-leak shape as before. This is still not a metric to judge a change by; see
the drift table above.
