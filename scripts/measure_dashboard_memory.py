"""Peak-memory and render-work harness for the dashboard -- sprint v9.5.

One measurement per fresh process: `resource.getrusage(RUSAGE_SELF).ru_maxrss` is a
high-water mark that never falls, so measuring several pages in one process would
report the running maximum rather than each page's own peak. The caller loops.

**The peak RSS number is not good enough to judge a change with.** Measuring the same
unchanged code twice moved the median by up to 87 MiB, which is larger than any
plausible effect of an optimisation. It is still reported, because the shape of the
baseline is useful (roughly 145 MiB before any view runs) and because a rising trend
across reruns would indicate a leak. To judge work, read the counters:

    fig_points  -- data points handed to matplotlib's line renderer
    png_bytes   -- bytes of PNG Streamlit encodes and ships to the browser
    sb_calls    -- Supabase reads per rerun

Those are deterministic: the same page gives the same numbers every time.

Instrumentation is installed in THIS process rather than inside the page script, so
the same counters apply to a view driven from a string and to the real app entry point
read from disk. It must be installed before the page imports its view, because
operational.py binds the supabase_client functions into its own namespace at import
time, so patching the library afterwards would not be seen.

Usage:
    python scripts/measure_dashboard_memory.py --page app --runs 3
    python scripts/measure_dashboard_memory.py --all
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

PAGE_NAMES = ("app", "operational")

# `app` is the real entry point read from disk. `operational` is the one remaining
# tab's view driven directly, which isolates the view from the app's own overhead.
PAGE_FILES = {"app": "dashboard/app.py"}
PAGE_SCRIPTS = {
    "operational": (
        "from dashboard.views import operational as v\n"
        "v.render(user_email='local', is_authenticated=True, secrets_configured=False)\n"
    ),
}

_COUNTERS = {"fig_points": 0, "png_bytes": 0, "figures": 0, "sb_calls": 0}
_INSTALLED = False


def load_env() -> None:
    """Same .env convention the other scripts use, so Supabase reads behave as on Render."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    # app.py rewrites .streamlit/secrets.toml when GOOGLE_CLIENT_ID is set, which would
    # clobber a developer's local secrets file during a measurement. Unset, the app
    # takes its local-dev branch, which is the path worth measuring anyway.
    os.environ.pop("GOOGLE_CLIENT_ID", None)


def peak_mb() -> float:
    """Process peak RSS in MiB."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / (1024 * 1024) if sys.platform == "darwin" else ru / 1024


def current_mb() -> float:
    """Current RSS in MiB. The peak cannot show growth across reruns, which is the question."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        return float(out) / 1024 if out else 0.0


def reset_counters() -> None:
    for key in _COUNTERS:
        _COUNTERS[key] = 0


def install_counters() -> None:
    """Patch st.pyplot and the Supabase readers, once per process.

    Once only: wrapping on every rerun stacks wrappers and counts each event two,
    three, ... times across reruns. That bug reported three times the real figures.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    import io

    import streamlit as st

    real_pyplot = st.pyplot

    def counting_pyplot(fig=None, **kw):
        try:
            buf = io.BytesIO()
            fig.savefig(buf, format="png")
            _COUNTERS["png_bytes"] += buf.tell()
        except Exception:
            pass
        try:
            _COUNTERS["fig_points"] += sum(
                sum(len(ln.get_xdata()) for ln in ax.get_lines()) for ax in fig.axes
            )
        except Exception:
            pass
        _COUNTERS["figures"] += 1
        return real_pyplot(fig, **kw)

    st.pyplot = counting_pyplot

    import dashboard.supabase_client as sb

    def counting(fn):
        def _wrap(*a, **k):
            _COUNTERS["sb_calls"] += 1
            return fn(*a, **k)
        return _wrap

    for name in ("fetch_pnl_log", "fetch_live_attribution", "fetch_positions",
                 "fetch_stop_states"):
        fn = getattr(sb, name, None)
        if fn is not None:
            setattr(sb, name, counting(fn))

    _INSTALLED = True


def build_app(page: str):
    from streamlit.testing.v1 import AppTest

    if page in PAGE_FILES:
        # Absolute: AppTest.from_file resolves a relative path against the file that
        # calls it, which here is this script in scripts/, not the repo root.
        return AppTest.from_file(
            str((ROOT / PAGE_FILES[page]).resolve()), default_timeout=600
        )
    return AppTest.from_string(PAGE_SCRIPTS[page], default_timeout=600)


def measure(page: str, runs: int = 1) -> dict:
    load_env()

    base = peak_mb()  # interpreter + stdlib only

    import streamlit  # noqa: F401
    from streamlit.testing.v1 import AppTest  # noqa: F401

    after_streamlit = peak_mb()

    # Warm the heavy third-party imports separately from the page, so the page number
    # is the page's own cost and not pandas+matplotlib arriving for the first time.
    import matplotlib.pyplot as plt
    import pandas as pd  # noqa: F401

    after_libs = peak_mb()

    install_counters()

    at = build_app(page)

    # Reruns are what a long-lived dashboard session does: every widget change
    # re-executes the whole script. Growth across them is the OOM hypothesis.
    # Counters are snapshotted per run, not accumulated, so the first (cold cache)
    # load and the later (warm cache) loads can be told apart.
    rss_series: list[float] = []
    counters_per_run: list[dict] = []
    for _ in range(runs):
        reset_counters()
        at.run()
        rss_series.append(round(current_mb(), 1))
        counters_per_run.append(dict(_COUNTERS))

    last = counters_per_run[-1] if counters_per_run else dict(_COUNTERS)

    return {
        "page": page,
        "base_mb": round(base, 1),
        "after_streamlit_mb": round(after_streamlit, 1),
        "after_libs_mb": round(after_libs, 1),
        "peak_mb": round(peak_mb(), 1),
        "view_cost_mb": round(peak_mb() - after_libs, 1),
        "open_figures": len(plt.get_fignums()),
        "errors": [str(e.value) for e in (at.exception or [])],
        "runs": len(rss_series),
        "rss_after_each_run": rss_series,
        "growth_over_runs_mb": (
            round(rss_series[-1] - rss_series[0], 1) if len(rss_series) > 1 else 0.0
        ),
        "counters_per_run": counters_per_run,
        "fig_points": last["fig_points"],
        "png_bytes": last["png_bytes"],
        "figures_rendered": last["figures"],
        "sb_calls": last["sb_calls"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", choices=sorted(PAGE_NAMES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--runs", type=int, default=1,
        help="rerun the page N times in one process and report RSS after each",
    )
    args = ap.parse_args()

    if args.all:
        # One subprocess per page, so each peak is that page's own high-water mark.
        for page in PAGE_NAMES:
            subprocess.run(
                [sys.executable, __file__, "--page", page, "--runs", str(args.runs)],
                check=False, cwd=ROOT,
            )
        return 0

    if not args.page:
        ap.error("pass --page or --all")

    import json

    print(json.dumps(measure(args.page, runs=args.runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
