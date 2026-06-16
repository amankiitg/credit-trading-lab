# Sprint v7.1 -- Notes
**Signal: NAV wedge (close/NAV - 1) on HYG/LQD | Programme v7, Sprint 1**

---

## T1 -- NAV Data-Availability Probe (G0a)

Implemented `scripts/probe_nav.py`. Method: the legacy iShares product-page CSV
export (`<product_url>/1467271812596.ajax?fileType=csv&fileName=...`), historically
the documented free way to pull fund NAV history without an API key.

Ran live against both products:

| ticker | product id | ok | reason | trading days |
|--------|-----------|----|--------|---------------|
| HYG | 239565 | False | endpoint returned the HTML product page, not CSV (legacy export retired) | 0 |
| LQD | 239566 | False | endpoint returned the HTML product page, not CSV (legacy export retired) | 0 |

Every `fileName`/`dataType` combination tried returns HTTP 200 with content-type
`text/csv`, but the response body is the full HTML page (about 2.73MB, identical to
a plain page fetch). Additional manual checks (not reproduced by the script): no
inline JSON state in the page HTML, and none of the referenced JS bundles contain an
API or CSV endpoint string. The performance / premium-discount chart is rendered by
a separate custom web component that fetches its data at runtime in a real browser.
Reverse-engineering that call would require headless-browser JS execution, which is
out of scope for a free, ToS-respecting data probe.

Full audit written to `data/processed/nav_audit.md`.

**G0a verdict: FAIL.** Daily NAV for HYG and LQD is not retrievable via a free,
scriptable, ToS-respecting endpoint.

### Gate decision

Per the pre-registered protocol in `sprints/v7.1/PRD.md`: **the documented finding
is that the signal is not testable on free data. The programme stops here.** No
proxy or synthetic NAV is substituted (House Rule 4). T2 and T3 (G0b, G0c) cannot
run without a real NAV series and are superseded.

---

## T5/T6 -- Signal construction and leakage check (code-level, ahead of data)

Even though G0a failed, the wedge/z_wedge construction and its gate-check
helper functions were implemented and unit-tested against synthetic fixtures, so
the code is ready the moment a NAV source is identified (e.g. a paid vendor
decision is made):

- `signals/nav_wedge.py`:
  - `compute_wedge(close, nav)` = `close/nav - 1`, no fitted parameters.
  - `compute_z_wedge(wedge, window=63)` -- strictly trailing, right-aligned rolling
    z-score. 63d is pre-registered in the PRD and is the literal `DEFAULT_WINDOW`
    constant; not tuned.
  - `check_date_alignment` / `g0b_passes` -- the G0b lag-correlation logic.
  - `check_eod_striking` / `g0c_passes` -- the G0c spot-check logic.
  - `S1B_STATEMENT` -- the guardrail text (see T8 below).
- `tests/test_nav_wedge.py` (12 tests) -- verifies the wedge formula, confirms
  `compute_z_wedge` has no look-ahead (perturbing future wedge values leaves past
  z_wedge values bit-for-bit unchanged), and exercises G0b/G0c against both clean
  and deliberately broken synthetic series (one-day NAV offset, mismatched
  reference value, missing reference date).
- `tests/test_probe_nav.py` (6 tests) -- verifies the HTML-vs-CSV response
  classifier and the G0a verdict logic, including the case that surfaced live
  (HTML page returned under a `text/csv` content-type).

All 18 tests pass (`pytest tests/test_nav_wedge.py tests/test_probe_nav.py`).
No regression, OLS, Kalman filter, or fitted intercept appears anywhere in this
construction (House Rule 2).

**These tests validate the code is correct. They are not a claim that the NAV
wedge signal has been validated on real market data** -- there is no real data
yet.

---

## T7 -- S1a stress-episode sanity check

Not executed. There is no real wedge series to plot through 2020-03 or 2022
without a NAV source. Superseded along with T2-T4.

---

## T8 -- S1b guardrail statement + sprint close

**S1b (verbatim):** "z_wedge stationarity, if observed, is not evidence of
tradeability. No IC test, no backtest, and no Sharpe/hit-rate claim is in scope for
v7.1." This statement is also codified as `signals.nav_wedge.S1B_STATEMENT` and
checked by `test_s1b_statement_present`.

### Gate-status table

| ID | Status | Note |
|----|--------|------|
| G0a | **FAIL** | Daily NAV not retrievable via free endpoint; legacy CSV export retired |
| G0b | not run | superseded -- no NAV series to test |
| G0c | not run | superseded -- no NAV series to test |
| S1a | not run | superseded -- no wedge series to plot |
| S1b | stated | guardrail recorded verbatim above, codified in code |

### What this sprint actually delivered

1. A real, reproducible answer to "can we get NAV history for free": no, not on
   the current iShares site, via the previously-known mechanism.
2. Construction code (`signals/nav_wedge.py`) and its gate-check logic, fully
   unit-tested, ready to run the moment NAV data exists.
3. A clean stop, per the v7.1 house rules, instead of substituting a proxy.

### v7.2 scope (contingent)

v7.2 cannot proceed as originally scoped (G0b/G0c/IC test on a real wedge) until a
NAV data source is resolved. Options to evaluate before opening v7.2:

1. **Paid vendor decision** -- identify and budget a vendor that provides daily
   historical fund NAV (the actual blocking dependency).
2. **Different proxy entirely** -- abandon the NAV-wedge mechanism and look for a
   genuinely different hypothesis that uses already-available data
   (`data/raw/credit_market_data.parquet`, `data/raw/{HYG,LQD,SPY,IEF}.parquet`).
3. Do not retry the iShares scrape with a headless browser to bypass the
   JS-rendered chart -- this would cross from "free public CSV export" into
   automated browser scraping of a financial site's internal API, which was
   intentionally ruled out in T1 as outside what this programme does.

Sprint v7.1 is closed at T1. T2-T4 and T7 skipped; T5/T6/T8 delivered at the code
level only, ahead of data.
