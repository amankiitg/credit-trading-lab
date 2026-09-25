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

import pytest


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
