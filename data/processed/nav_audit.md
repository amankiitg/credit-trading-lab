# NAV Data Audit -- Sprint v7.1, Task T1 (G0a)

Probe date: 2026-06-16

## Method

Attempted the legacy iShares product-page CSV export (`<product_url>/1467271812596.ajax?fileType=csv&fileName=...`), the historically documented mechanism for downloading fund NAV history without an API key. Each response was validated to confirm it is genuine NAV CSV content, not the ordinary HTML product page served back under a text/csv content-type.

Observed behavior on the current site: every fileName/dataType combination returns HTTP 200 with content-type text/csv, but the response body is the full HTML page (about 2.73MB, identical to a plain page fetch) -- the legacy export endpoint has been retired.

Additional manual checks (not reproduced by this script): no inline JSON state was found in the page HTML, and none of the referenced JS bundles contained an API or CSV endpoint string. The performance / premium-discount chart is rendered by a separate custom web component that fetches its data at runtime in a real browser. Reverse-engineering that call would require headless-browser JS execution, which is out of scope for a free, ToS-respecting data probe.

## Results

| ticker | product id | ok | reason | trading days | first date | last date | gap count |
|--------|-----------|----|--------|---------------|------------|-----------|-----------|
| HYG | 239565 | False | endpoint returned the HTML product page, not CSV (legacy export retired) | 0 | None | None | None |
| LQD | 239566 | False | endpoint returned the HTML product page, not CSV (legacy export retired) | 0 | None | None | None |

## G0a verdict: FAIL

Daily NAV for HYG and LQD is not retrievable via a free, scriptable, ToS-respecting endpoint. Per the sprint v7.1 PRD (sprints/v7.1/PRD.md), this is the documented outcome: the signal is not testable on free data. No proxy or synthetic NAV is substituted. The sprint stops here -- G0b, G0c, and downstream tasks are not executed.

A vendor decision (paid NAV history feed) is required before this hypothesis can be tested. That decision is out of scope for this sprint and is carried forward as a v7.x dependency.
