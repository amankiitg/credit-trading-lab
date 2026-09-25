"""Tests for the dashboard shell after the Strategy and Research tabs were removed.

The dashboard is now the Trade Approval book alone. These tests pin the shape that
removal produced: exactly one tab, the removed view modules gone, no legacy HTML
component, and every matplotlib figure closed on the one page that still draws them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD = Path("dashboard")
APP = DASHBOARD / "app.py"

REMOVED_MODULES = [
    "dashboard/views/attribution.py",
    "dashboard/views/research_history.py",
    "dashboard/views/directional.py",
    "dashboard/views/rv.py",
    "dashboard/components/markers.py",
]


def _render_app(monkeypatch):
    """Render dashboard/app.py the way Streamlit does, with no side effects.

    GOOGLE_CLIENT_ID is removed first: app.py rewrites .streamlit/secrets.toml when
    it is set, and a test must never overwrite a developer's local secrets file.
    With it unset the app takes its local-dev branch.

    The path is absolute because AppTest.from_file resolves a relative path against
    the file that calls it, which here is this test file, not the repo root.
    """
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP.resolve()), default_timeout=600)
    at.run()
    return at


# ------------------------------------------------------------------ the shell

def test_the_remaining_tab_renders_with_no_exceptions(monkeypatch) -> None:
    """The one acceptance criterion that matters after the removal.

    AppTest executes the real app.py, so this covers the tab wiring, the auth
    branch, the view import and the whole render path of the remaining panel.
    """
    at = _render_app(monkeypatch)

    assert not at.exception, [str(e.value) for e in at.exception]


def test_only_the_trade_approval_tab_is_declared(monkeypatch) -> None:
    """Strategy Analytics and Research Archive must be gone from the tab bar."""
    at = _render_app(monkeypatch)

    labels = [tab.label for tab in at.tabs]
    assert labels == ["Trade Approval"], f"unexpected tabs: {labels}"


def test_the_removed_pages_are_not_referenced_by_the_app() -> None:
    """The app must not import a page that no longer exists."""
    source = APP.read_text()

    for module in ("attribution", "research_history"):
        assert f"import {module}" not in source
        assert f"{module}_view" not in source


@pytest.mark.parametrize("path", REMOVED_MODULES)
def test_the_removed_modules_are_actually_gone(path: str) -> None:
    assert not Path(path).exists(), f"{path} should have been deleted"


def test_the_remaining_tabs_dependencies_were_kept() -> None:
    """Deleting the two pages must not have taken a shared module with them."""
    for required in (
        "dashboard/views/operational.py",
        "dashboard/loader.py",
        "dashboard/supabase_client.py",
        "dashboard/components/regime_shade.py",
    ):
        assert Path(required).exists(), f"{required} is still needed"


# ------------------------------------------------------------------ hygiene

def test_the_dashboard_no_longer_uses_the_legacy_html_component() -> None:
    """st.components.v1.html is deprecated, and its last use was the tab script.

    This looks for an import of the legacy namespace or a call into it, not for the
    bare string, so explanation in comments does not trip it.
    """
    legacy_import = re.compile(r"^\s*(?:import|from)\s+streamlit\.components", re.M)
    legacy_call = re.compile(r"\bcomponents\.v1\.\w+\s*\(")

    offenders = [
        str(path.relative_to(DASHBOARD))
        for path in DASHBOARD.rglob("*.py")
        if legacy_import.search(path.read_text()) or legacy_call.search(path.read_text())
    ]
    assert not offenders, f"legacy components.v1 still used in: {offenders}"


def test_the_tab_switching_script_is_gone() -> None:
    """With one tab there is nothing to switch to, so the injected script went too."""
    source = APP.read_text()

    assert "st.iframe" not in source
    assert "querySelectorAll" not in source
    assert "_was_logged_in" not in source


def test_every_matplotlib_figure_is_closed() -> None:
    """The remaining page creates 4 figures per load and must close all 4.

    A figure left open is never collected while matplotlib's global state holds it,
    and on a long-lived session that is a leak. Per file, so a failure names the file.
    """
    offenders: list[str] = []
    for path in sorted(DASHBOARD.rglob("views/*.py")):
        source = path.read_text()
        created = len(re.findall(r"plt\.subplots?\(", source)) + len(
            re.findall(r"plt\.figure\(", source)
        )
        closed = len(re.findall(r"plt\.close\(", source))
        if created and closed < created:
            offenders.append(f"{path}: {created} created, {closed} closed")

    assert not offenders, "unclosed matplotlib figures:\n" + "\n".join(offenders)
