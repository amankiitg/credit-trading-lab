"""A paper client may only be asked for one way: with dry_run named, or not at all.

The 2026-11-06 bug was a read that asked for a client the wrong way. Step 8 of
run_signal called connect() with no arguments, DRY_RUN_DEFAULT is true on the cron, so
connect() returned None by design and the traded part of every session was dropped
silently -- on a read that never places an order. Step 7, one line above it, had always
asked for the same account with connect(dry_run=False).

These are source-level guards on that whole class of bug, plus the contract of the one
helper the reads share (execution.snapshot.open_paper_client). They fail on origin/main,
where two read paths still called a bare connect(), and pass only while every call
site names its dry_run.

What they deliberately do NOT do is force every read through open_paper_client. The two
fallbacks that build their own client (execution/snapshot.py and risk/execution_pnl.py)
already name dry_run=False, and routing them through the helper -- which converts every
exception into "no client" -- would launder a real broker fault into "not available",
which tests/test_snapshot_refresh.py::test_a_failed_refresh_is_swallowed_and_reported
and tests/test_execution_pnl.py::test_the_hook_reports_an_error_instead_of_raising both
forbid. One factory for the jobs; explicit dry_run for the fallbacks.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Everything a job can import. tests/ is excluded on purpose: a stub may fake connect()
# however it likes, and several do.
SOURCE_DIRS = ("execution", "risk", "scripts", "signals", "dashboard")

# The audited inventory of every non-test place that asks for a paper client. A new
# key here is a review question -- is that path read-only, and does it name dry_run? --
# and the value is how many call sites that file has, so a second one cannot slip in.
EXPECTED_CALL_SITES: dict[str, int] = {
    "execution/snapshot.py": 2,          # the read-only helper, and its fallback
    "risk/execution_pnl.py": 1,          # step 8 fallback, when no client was handed in
    "scripts/run_execution.py": 1,       # the execution job (order-placing)
    "scripts/backfill_execution_run.py": 1,  # the crash-recovery replay
    "scripts/sync_positions_from_alpaca.py": 1,  # the manual re-sync
}


def _connect_calls():
    """Yield (relative path, lineno, node) for every connect(...) call in source."""
    for folder in SOURCE_DIRS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "connect":
                    yield str(path.relative_to(ROOT)), node.lineno, node


def test_no_module_calls_connect_without_naming_its_dry_run() -> None:
    """A bare connect() is a read switched off, because the default is dry_run=True."""
    offenders = [
        f"{path}:{lineno}"
        for path, lineno, node in _connect_calls()
        if not node.args and not any(kw.arg == "dry_run" for kw in node.keywords)
    ]

    assert offenders == [], (
        "connect() must name dry_run, or not be called at all -- a bare call returns "
        "None whenever DRY_RUN_DEFAULT is on, which silently disables a read: "
        f"{offenders}"
    )


def test_the_set_of_places_that_ask_for_a_client_is_the_audited_one() -> None:
    """Every client factory call is either a known read or the order-placing job."""
    seen: dict[str, int] = {}
    for path, _lineno, _node in _connect_calls():
        seen[path] = seen.get(path, 0) + 1

    assert seen == EXPECTED_CALL_SITES, (
        "a client call site appeared or moved: re-audit it, name its dry_run, then "
        f"update this inventory. got {seen}, expected {EXPECTED_CALL_SITES}"
    )


def test_the_read_only_helper_asks_for_a_read(monkeypatch) -> None:
    """The one place that turns the default off, on purpose."""
    import execution.alpaca_paper as ap
    import execution.snapshot as snap

    asked: list[dict] = []
    monkeypatch.setattr(ap, "DRY_RUN_DEFAULT", True)  # as it is on the cron
    monkeypatch.setattr(ap, "connect", lambda **kwargs: asked.append(kwargs) or "client")

    assert snap.open_paper_client() == "client"
    assert asked == [{"dry_run": False}], "a read must never inherit the dry-run default"


def test_the_read_only_helper_reports_a_missing_credential_as_no_client(monkeypatch) -> None:
    """A missing key is a state, not an incident: the job reports it and carries on."""
    import execution.alpaca_paper as ap
    import execution.snapshot as snap

    def _no_creds(*args, **kwargs):
        raise EnvironmentError(
            "ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY must be set"
        )

    monkeypatch.setattr(ap, "connect", _no_creds)

    assert snap.open_paper_client() is None

