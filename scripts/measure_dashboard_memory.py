"""Peak-memory harness for the dashboard -- one measurement per fresh process.

Why a separate process per page: `resource.getrusage(RUSAGE_SELF).ru_maxrss` is a
high-water mark that never falls, so measuring several pages in one process would
report the running maximum rather than each page's own peak. This script therefore
measures exactly one page per invocation and the caller loops over pages.

Why AppTest: a view is a Streamlit script by contract, and `render()` calls st.*
functions that need a script run context. AppTest is the sanctioned way to execute
a Streamlit script in-process, so the measurement exercises the real render path
including the widget/DeltaGenerator allocations, not just the pandas reads.

Usage:
    python scripts/measure_dashboard_memory.py --page research_history
    python scripts/measure_dashboard_memory.py --all     # loops via subprocess

ru_maxrss units differ by platform: bytes on macOS, kilobytes on Linux. Reporting
the wrong one silently inflates every number by 1024x, so it is handled explicitly.
"""

from __future__ import annotations

import argparse
import os
import resource
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_env() -> None:
    """Same .env convention the other scripts use, so Supabase reads behave as on Render."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def peak_mb() -> float:
    """Process peak RSS in MiB."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / (1024 * 1024) if sys.platform == "darwin" else ru / 1024


def _attribution_script() -> str:
    return (
        "from dashboard.views import attribution as v\n"
        "v.render()\n"
    )


def _operational_script() -> str:
    return (
        "from dashboard.views import operational as v\n"
        "v.render(user_email='local', is_authenticated=True, secrets_configured=False)\n"
    )


def _research_history_script() -> str:
    return (
        "from dashboard.views import research_history as v\n"
        "v.render()\n"
    )


def _everything_script() -> str:
    """What app.py actually does: every tab renders on every script run."""
    return (
        "from dashboard.views import attribution as a, operational as o, research_history as r\n"
        "a.render()\n"
        "o.render(user_email='local', is_authenticated=True, secrets_configured=False)\n"
        "r.render()\n"
    )


PAGES = {
    "attribution": _attribution_script,
    "operational": _operational_script,
    "research_history": _research_history_script,
    "everything": _everything_script,
}

# Deterministic companion to the RSS number.
#
# Peak RSS turned out to be useless for judging these changes: measuring the SAME
# code twice, minutes apart, moved the median by up to 87 MiB, which is larger than
# any plausible effect of the changes. The measurements that are not noisy are the
# ones that count work actually done, so this preamble also records:
#
#   fig_points   -- data points handed to matplotlib's line renderer
#   png_bytes    -- bytes of PNG Streamlit encodes and ships to the browser
#   sb_calls     -- Supabase reads per rerun, which is what the caching change is for
#
# It must run BEFORE the view module is imported: operational.py binds the
# supabase_client functions into its own namespace at import time, so patching the
# library afterwards would not be seen.
_PROBE = r'''
import io as _io
import streamlit as _st

# ONE dict for the whole session, reset in place at the start of each run. Each
# rerun re-executes this script in a fresh namespace, so a per-run dict would be
# captured by the previous run's wrappers and their calls would be counted into a
# dict nobody reads. That bug reported 0 Supabase calls on every rerun after the
# first. Session state survives reruns, so the wrappers always write to the dict
# that is actually reported.
try:
    _m = _st.session_state["_metrics"]
except Exception:
    _m = {}
    _st.session_state["_metrics"] = _m
for _k in ("fig_points", "png_bytes", "figures", "sb_calls"):
    _m[_k] = 0

_real_pyplot = _st.pyplot

def _counting_pyplot(fig=None, **kw):
    try:
        buf = _io.BytesIO()
        fig.savefig(buf, format="png")
        _m["png_bytes"] += buf.tell()
    except Exception:
        pass
    try:
        _m["fig_points"] += sum(
            sum(len(ln.get_xdata()) for ln in ax.get_lines()) for ax in fig.axes
        )
    except Exception:
        pass
    _m["figures"] += 1
    return _real_pyplot(fig, **kw)

if not getattr(_st, "_dashmem_wrapped", False):
    _st.pyplot = _counting_pyplot

    import dashboard.supabase_client as _sb

    def _counting(fn):
        def _wrap(*a, **k):
            _m["sb_calls"] += 1
            return fn(*a, **k)
        return _wrap

    for _name in ("fetch_pnl_log", "fetch_live_attribution", "fetch_positions",
                  "fetch_stop_states"):
        _fn = getattr(_sb, _name, None)
        if _fn is not None:
            setattr(_sb, _name, _counting(_fn))

    _st._dashmem_wrapped = True
'''


def current_mb() -> float:
    """Current resident set size in MiB.

    ru_maxrss is a high-water mark and cannot show growth across reruns, which is
    the whole question here, so current RSS is read separately. psutil is used when
    present and `ps` otherwise, so this works without adding a dependency.
    """
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        return float(out) / 1024 if out else 0.0


def measure(page: str, tracemalloc_top: int = 0, runs: int = 1) -> dict:
    """Run one page through AppTest and report the peak RSS for this process."""
    load_env()

    base = peak_mb()  # interpreter + stdlib only

    import streamlit  # noqa: F401
    from streamlit.testing.v1 import AppTest

    after_streamlit = peak_mb()

    # Warm the heavy third-party imports the way the app does, separately from the
    # view itself, so the page number is the page's own cost and not pandas+matplotlib
    # arriving for the first time.
    import matplotlib.pyplot as plt
    import pandas as pd  # noqa: F401

    after_libs = peak_mb()

    at = AppTest.from_string(_PROBE + PAGES[page](), default_timeout=600)

    # Reruns are what a long-lived dashboard session does (every widget change
    # re-executes the script), so growth across them is the OOM hypothesis.
    rss_series: list[float] = []
    for _ in range(runs):
        at.run()
        rss_series.append(round(current_mb(), 1))

    peak = peak_mb()
    errors = [str(e.value) for e in (at.exception or [])]
    figures = len(plt.get_fignums())

    # Metrics for the last rerun only, so they describe one page load. SafeSessionState
    # has no .get(), so the key is read directly.
    try:
        metrics = dict(at.session_state["_metrics"])
    except Exception:
        metrics = {}

    return {
        "page": page,
        "base_mb": round(base, 1),
        "after_streamlit_mb": round(after_streamlit, 1),
        "after_libs_mb": round(after_libs, 1),
        "peak_mb": round(peak, 1),
        "view_cost_mb": round(peak - after_libs, 1),
        "open_figures": figures,
        "errors": errors,
        "runs": len(rss_series),
        "rss_after_each_run": rss_series,
        "growth_over_runs_mb": round(rss_series[-1] - rss_series[0], 1) if len(rss_series) > 1 else 0.0,
        "fig_points": metrics.get("fig_points", 0),
        "png_bytes": metrics.get("png_bytes", 0),
        "sb_calls": metrics.get("sb_calls", 0),
        "figures_rendered": metrics.get("figures", 0),
        "hotspots": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", choices=sorted(PAGES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--runs", type=int, default=1,
        help="rerun the page N times in one process and report RSS after each",
    )
    args = ap.parse_args()

    if args.all:
        # One subprocess per page, so each peak is that page's own high-water mark.
        for page in ["attribution", "operational", "research_history", "everything"]:
            subprocess.run(
                [sys.executable, __file__, "--page", page, "--runs", str(args.runs)],
                check=False, cwd=ROOT,
            )
        return 0

    if not args.page:
        ap.error("pass --page or --all")

    import json

    result = measure(args.page, runs=args.runs)
    result.pop("hotspots", None)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
