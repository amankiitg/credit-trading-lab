# Sprint v9.5 -- Render dashboard redeploys and memory

Two problems from the Render dashboard logs: it redeployed on every push, and it
restarted once at 2026-09-25 02:38 right after a page load with no traceback.

## 1. Redeploys on every push

Every push to `main` rebuilt all three services, so a commit that only touched the
cron jobs, the notebooks or the sprint notes restarted the dashboard and dropped
every open session for no reason.

`render.yaml` now gives the dashboard a `buildFilter.paths` list: `dashboard/**`,
`pyproject.toml`, `requirements.txt` and `scripts/write_render_secrets.py`. That last
one is there because the build command runs it, so a change to it does change what
this service builds. Only a change to one of those paths triggers a dashboard build now.

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
