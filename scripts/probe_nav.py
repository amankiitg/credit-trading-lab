"""NAV data-availability probe for sprint v7.1 (gate G0a).

Attempts to retrieve historical daily NAV for HYG (iShares product 239565)
and LQD (iShares product 239566) via the legacy iShares product-page CSV
export, the historically documented way to pull fund NAV history without
an API key or paid vendor. Each response is validated to confirm it is
genuine NAV CSV content, not the ordinary HTML product page served back
under a misleading text/csv content-type.

If retrieval succeeds for a ticker, the NAV series is inner-joined against
the existing trading calendar in data/raw/{ticker}.parquet (close prices),
gaps are counted, and the result is persisted to data/raw/{ticker}_nav.parquet.

Writes the audit trail to data/processed/nav_audit.md every run, regardless
of outcome, per the v7.1 PRD G0a gate (sprints/v7.1/PRD.md). If G0a fails,
that is the documented finding: the signal is not testable on free data.
No proxy or synthetic NAV is substituted.

Run as: python scripts/probe_nav.py
"""

from __future__ import annotations

from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
AUDIT_PATH = ROOT / "data" / "processed" / "nav_audit.md"

MIN_TRADING_DAYS = 1260  # approx 5 years of trading days, per G0a

PRODUCTS = {
    "HYG": {"id": "239565", "slug": "ishares-iboxx-high-yield-corporate-bond-etf"},
    "LQD": {"id": "239566", "slug": "ishares-iboxx-investment-grade-corporate-bond-etf"},
}

LEGACY_CSV_URL = (
    "https://www.ishares.com/us/products/{id}/{slug}/"
    "1467271812596.ajax?fileType=csv&fileName={ticker}_NAV_History&dataType=fund"
)


def _looks_like_html(text: str) -> bool:
    """True if the response body is an HTML page, not CSV data."""
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or "<html" in head


def _parse_nav_csv(text: str) -> pd.DataFrame:
    """Parse CSV text into a [date index, nav column] frame.

    Raises ValueError if no recognizable date/NAV column pair is found.
    """
    df = pd.read_csv(StringIO(text))
    date_col = next((c for c in df.columns if "date" in c.lower()), None)
    nav_col = next((c for c in df.columns if "nav" in c.lower()), None)
    if date_col is None or nav_col is None:
        raise ValueError(f"no date/NAV columns found in CSV: {list(df.columns)}")
    out = df[[date_col, nav_col]].copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.rename(columns={date_col: "date", nav_col: "nav"})
    out = out.set_index("date").sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out[["nav"]]


def fetch_legacy_csv(ticker: str, timeout: int = 20) -> tuple[bool, str, pd.DataFrame | None]:
    """Attempt the legacy iShares NAV CSV export for one ticker.

    Returns (ok, reason, frame). ok is False if the request failed, or
    if the response is not genuine NAV CSV content.
    """
    info = PRODUCTS[ticker]
    url = LEGACY_CSV_URL.format(id=info["id"], slug=info["slug"], ticker=ticker)
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        return False, f"request failed: {exc}", None

    if r.status_code != 200:
        return False, f"http {r.status_code}", None

    if _looks_like_html(r.text):
        return False, "endpoint returned the HTML product page, not CSV (legacy export retired)", None

    try:
        df = _parse_nav_csv(r.text)
    except Exception as exc:
        return False, f"could not parse response as CSV: {exc}", None

    return True, "ok", df


def join_trading_calendar(nav: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, int]:
    """Inner-join NAV against the existing trading calendar for ticker.

    The calendar is the date index of data/raw/{ticker}.parquet (close
    prices). gap_count is the number of calendar trading dates, within
    the NAV's own date range, that have no matching NAV observation.
    """
    calendar = pd.read_parquet(RAW_DIR / f"{ticker}.parquet").index
    calendar_in_range = calendar[(calendar >= nav.index.min()) & (calendar <= nav.index.max())]
    joined = nav.join(pd.DataFrame(index=calendar_in_range), how="inner")
    gap_count = len(calendar_in_range) - len(joined)
    return joined, gap_count


def probe() -> dict[str, dict]:
    """Run the G0a probe for both tickers and return a results dict."""
    results: dict[str, dict] = {}
    for ticker in PRODUCTS:
        ok, reason, df = fetch_legacy_csv(ticker)
        entry = {
            "ok": ok,
            "reason": reason,
            "n_days": 0,
            "first_date": None,
            "last_date": None,
            "gap_count": None,
        }
        if ok and df is not None:
            joined, gap_count = join_trading_calendar(df, ticker)
            entry["n_days"] = len(joined)
            entry["first_date"] = joined.index.min()
            entry["last_date"] = joined.index.max()
            entry["gap_count"] = gap_count
            joined.to_parquet(RAW_DIR / f"{ticker}_nav.parquet")
        results[ticker] = entry
    return results


def g0a_verdict(results: dict[str, dict]) -> bool:
    return all(r["ok"] and r["n_days"] >= MIN_TRADING_DAYS for r in results.values())


def write_audit(results: dict[str, dict], verdict: bool) -> None:
    lines = [
        "# NAV Data Audit -- Sprint v7.1, Task T1 (G0a)",
        "",
        f"Probe date: {date.today().isoformat()}",
        "",
        "## Method",
        "",
        "Attempted the legacy iShares product-page CSV export "
        "(`<product_url>/1467271812596.ajax?fileType=csv&fileName=...`), "
        "the historically documented mechanism for downloading fund NAV "
        "history without an API key. Each response was validated to "
        "confirm it is genuine NAV CSV content, not the ordinary HTML "
        "product page served back under a text/csv content-type.",
        "",
        "Observed behavior on the current site: every fileName/dataType "
        "combination returns HTTP 200 with content-type text/csv, but the "
        "response body is the full HTML page (about 2.73MB, identical to "
        "a plain page fetch) -- the legacy export endpoint has been "
        "retired.",
        "",
        "Additional manual checks (not reproduced by this script): no "
        "inline JSON state was found in the page HTML, and none of the "
        "referenced JS bundles contained an API or CSV endpoint string. "
        "The performance / premium-discount chart is rendered by a "
        "separate custom web component that fetches its data at runtime "
        "in a real browser. Reverse-engineering that call would require "
        "headless-browser JS execution, which is out of scope for a free, "
        "ToS-respecting data probe.",
        "",
        "## Results",
        "",
        "| ticker | product id | ok | reason | trading days | first date | last date | gap count |",
        "|--------|-----------|----|--------|---------------|------------|-----------|-----------|",
    ]
    for ticker, r in results.items():
        pid = PRODUCTS[ticker]["id"]
        lines.append(
            f"| {ticker} | {pid} | {r['ok']} | {r['reason']} | {r['n_days']} | "
            f"{r['first_date']} | {r['last_date']} | {r['gap_count']} |"
        )
    lines += [
        "",
        f"## G0a verdict: {'PASS' if verdict else 'FAIL'}",
        "",
    ]
    if not verdict:
        lines += [
            "Daily NAV for HYG and LQD is not retrievable via a free, "
            "scriptable, ToS-respecting endpoint. Per the sprint v7.1 PRD "
            "(sprints/v7.1/PRD.md), this is the documented outcome: the "
            "signal is not testable on free data. No proxy or synthetic "
            "NAV is substituted. The sprint stops here -- G0b, G0c, and "
            "downstream tasks are not executed.",
            "",
            "A vendor decision (paid NAV history feed) is required before "
            "this hypothesis can be tested. That decision is out of scope "
            "for this sprint and is carried forward as a v7.x dependency.",
        ]
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    results = probe()
    verdict = g0a_verdict(results)
    write_audit(results, verdict)
    for ticker, r in results.items():
        print(f"{ticker}: ok={r['ok']} days={r['n_days']} reason={r['reason']}")
    print(f"G0a verdict: {'PASS' if verdict else 'FAIL'}")


if __name__ == "__main__":
    main()
