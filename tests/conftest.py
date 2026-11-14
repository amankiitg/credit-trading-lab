"""Shared test configuration.

The autouse fixture below exists so that no test in this suite can send a real
email. Both cron jobs now send a summary at the end of every run, so any test that
drives `run_execution.main()` or `run_signal.main()` reaches the sender. Without
this, a developer with RESEND_API_KEY exported in their shell would mail
themselves on every test run.

It patches the attribute on `execution.alerts`, which is what `daily_summary`
resolves at call time. `tests/test_alerts.py` is unaffected: it binds
`send_alert_email` at import time and tests the real function through a patched
`requests.post`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def complete_close() -> pd.DataFrame:
    """The real universe, cut to its last session on which every name has a close.

    The checked-in data/raw parquets end with 42 sessions on which SPY, IEF, HYG
    and LQD have no row while EFA, EEM, TLT and GLD do (the matrix runs to
    2026-06-15). `compute_trend` refuses a matrix like that now
    (`MissingSizingInput`): a name that should have a close must not be sized,
    because the gross cap would otherwise renormalise the names that do have one
    -- the 2026-07-28 failure in miniature.

    So construction tests run on the last complete session. That still leaves
    ~4,780 sessions, including every staggered inception and its warmup. The
    refused case is pinned directly on a NaN row instead
    (tests/test_trend_signal.py::test_a_nan_row_for_a_started_name_is_refused).
    """
    from signals.etf_universe import load_universe_close

    close = load_universe_close()
    complete = close.notna().all(axis=1).to_numpy()
    if not complete.any():
        raise AssertionError("the close matrix has no session with every name present")
    return close.loc[: close.index[np.flatnonzero(complete)[-1]]]


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch) -> list:
    """Replace the sender with a spy for every test. Returns the captured sends."""
    import execution.alerts as alerts

    sent: list[dict] = []

    def _spy(subject, body, *args, **kwargs):
        sent.append({"subject": subject, "body": body})
        return True

    monkeypatch.setattr(alerts, "send_alert_email", _spy)
    return sent


@pytest.fixture
def no_dividend_fetch(monkeypatch) -> dict:
    """Keep the signal job's distribution refresh off the network.

    The job refreshes distributions alongside closes, so any test that drives
    `run_signal.main()` to step 3c would otherwise reach Yahoo. The returned dict is
    the report shape `signals.dividends.ingest` produces, so a test can read the
    refresh date it recorded.
    """
    import signals.dividends as div

    from datetime import date

    report = {
        "ok": True,
        "tickers": 8,
        "distributions": 0,
        "as_of": date.today().isoformat(),
    }
    monkeypatch.setattr(div, "ingest", lambda *a, **k: dict(report))
    return report
