"""Contract test against the installed alpaca-py: every SDK symbol we use exists.

No mocks, no fakes, no stubs of the library. This imports the really-installed
alpaca-py and asks it, symbol by symbol, whether the modules and attributes this
repository references are actually there, and whether the methods we call on a
TradingClient are really methods of TradingClient.

Why it exists. On 2026-11-09 execution/alpaca_paper.py imported
GetActivitiesRequest from alpaca.trading.requests. That name has never existed in
alpaca-py, and TradingClient has no activity method at all. The import sat inside a
try whose except returned [], so four real fills were reported as "0 fills" and the
unread broker looked like a quiet trading day. Every test that exercised our own fake
client passed: the fake had the method the real SDK lacks. A test that asks the
installed library is the one that could not have passed.

On origin/main the import scan finds GetActivitiesRequest and this module fails with
that symbol named. On the fix it passes, because the fills are read by a raw GET
whose receiver method (TradingClient.get) does exist.

The scan is static (ast) so it sees every module, including ones no test imports; the
assertions are live, so a symbol only counts if importlib can resolve it.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata as metadata
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Every first-party package directory that could reach the broker.
SCAN_DIRS = ("execution", "risk", "signals", "dashboard", "scripts", "backtest", "models")

# Receiver names that hold one of these SDK clients. Restricted per file to the class
# the file actually imports: client.table(...) in supabase_client is a Supabase client
# and must not be checked against TradingClient.
CLIENT_CLASSES = {
    "TradingClient": "alpaca.trading.client",
    "StockHistoricalDataClient": "alpaca.data.historical",
}
CLIENT_RECEIVERS = frozenset({"client", "trading_client", "paper_client", "data_client"})

# Our own constructor: execution.alpaca_paper.connect() returns a TradingClient. Named
# here so a variable built from it is checked against that class and not against
# whichever other client the same file happens to import.
CONNECT_BUILDS = {"connect": "TradingClient"}


def _python_files():
    for directory in SCAN_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            yield path


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _alpaca_imports() -> dict[tuple[str, str], set[str]]:
    """{(module, attribute): {files that import it}} for every alpaca import here."""
    found: dict[tuple[str, str], set[str]] = {}
    for path in _python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module.split(".")[0] != "alpaca":
                continue
            for alias in node.names:
                found.setdefault((node.module, alias.name), set()).add(
                    str(path.relative_to(REPO_ROOT))
                )
    return found


def _client_calls() -> list[tuple[str, str, str]]:
    """[(class name, method name, file)] for calls made on an SDK client variable.

    The receiver is typed per file, not by cross-product: a file can hold both a
    TradingClient and a data client (scripts/backfill_execution_run.py does), and
    checking a TradingClient call against the data client would report a method as
    missing that the broker client really has. Receivers are resolved from:

      1. a direct construction -- dc = StockHistoricalDataClient(...);
      2. our own connect(), which builds a TradingClient (see execution.alpaca_paper);
      3. when a file imports exactly one SDK client class, its bare client parameter.
    """
    calls: list[tuple[str, str, str]] = []
    for path in _python_files():
        tree = _parse(path)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in CLIENT_CLASSES.values()
            for alias in node.names
            if alias.name in CLIENT_CLASSES
        }
        if not imported:
            continue

        receivers: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            class_name = None
            if isinstance(func, ast.Name) and func.id in CLIENT_CLASSES:
                class_name = func.id
            elif isinstance(func, ast.Name) and func.id in CONNECT_BUILDS:
                class_name = CONNECT_BUILDS[func.id]
            if class_name is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    receivers[target.id] = class_name

        if len(imported) == 1:
            only = next(iter(imported))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in CLIENT_RECEIVERS
                ):
                    receivers.setdefault(node.func.value.id, only)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in receivers
            ):
                calls.append(
                    (
                        receivers[node.func.value.id],
                        node.func.attr,
                        str(path.relative_to(REPO_ROOT)),
                    )
                )
    return calls


# ------------------------------------------------------------------ the library itself


def test_the_library_under_test_is_the_installed_distribution() -> None:
    """No-mocks has to be checkable: the imported package must be the real one."""
    alpaca = importlib.import_module("alpaca")
    origin = pathlib.Path(alpaca.__file__).resolve()
    assert origin.name == "__init__.py", origin
    assert any(part in {"site-packages", "dist-packages"} for part in origin.parts), origin
    assert metadata.version("alpaca-py")  # raises when the distribution is absent


def test_every_alpaca_import_resolves_against_the_installed_sdk() -> None:
    """Every symbol we import must exist in the version actually installed.

    This is the 2026-11-09 test: on main it reports
    alpaca.trading.requests.GetActivitiesRequest.
    """
    imports = _alpaca_imports()
    assert imports, (
        "the scan found no alpaca imports at all -- it is looking in the wrong place, "
        "and a passing result would mean nothing"
    )

    missing: list[str] = []
    for (module_name, attribute), files in sorted(imports.items()):
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            missing.append(
                f"{module_name}.{attribute} (imported by {', '.join(sorted(files))}): "
                f"module itself does not import: {exc}"
            )
            continue
        if not hasattr(module, attribute):
            missing.append(
                f"{module_name}.{attribute} (imported by {', '.join(sorted(files))})"
            )

    assert not missing, (
        "the code imports alpaca symbols the installed alpaca-py does not provide:\n  "
        + "\n  ".join(missing)
    )


def test_every_method_we_call_on_an_sdk_client_exists() -> None:
    """A fake client cannot hide a missing SDK method; this asks the real class.

    On main the scan picks up client.get_activities(request) and fails here as well
    as on the import above.
    """
    calls = _client_calls()
    assert calls, "the scan found no SDK client calls -- a passing result would mean nothing"

    classes = {name: importlib.import_module(module) for name, module in CLIENT_CLASSES.items()}
    missing: list[str] = []
    for class_name, method, file in sorted(set(calls)):
        klass = getattr(classes[class_name], class_name)
        if not callable(getattr(klass, method, None)):
            missing.append(f"{class_name}.{method} (called in {file})")

    assert not missing, (
        "the code calls SDK methods the installed alpaca-py does not have:\n  "
        + "\n  ".join(missing)
    )


def test_the_sdk_has_no_activity_api_which_is_why_we_use_a_raw_get() -> None:
    """The documented constraint, asserted rather than asserted-in-a-comment.

    fetch_session_fills reads GET /account/activities through the client's raw
    transport on purpose. If a future alpaca-py grows a typed activity API this test
    fails, which is the signal to revisit that choice rather than silently keep two
    ways of doing the same read.
    """
    requests_module = importlib.import_module("alpaca.trading.requests")
    trading_client = importlib.import_module("alpaca.trading.client").TradingClient

    assert not hasattr(requests_module, "GetActivitiesRequest")
    activity_methods = [name for name in dir(trading_client) if "activit" in name.lower()]
    assert activity_methods == [], activity_methods


# ------------------------------------------------------------------ the pin


def _requirement_pin() -> str:
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pins = [
        line.split("==", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith("alpaca-py")
    ]
    assert len(pins) == 1, f"expected one alpaca-py pin in requirements.txt, got {pins}"
    return pins[0]


def _pyproject_pin() -> str:
    import tomli

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomli.load(handle)
    pins = [
        spec.split("==", 1)[1]
        for spec in data["project"]["dependencies"]
        if spec.lower().startswith("alpaca-py")
    ]
    assert len(pins) == 1, f"expected one alpaca-py pin in pyproject.toml, got {pins}"
    return pins[0]


@pytest.mark.parametrize(
    "read_pin", [_requirement_pin, _pyproject_pin], ids=["requirements", "pyproject"]
)
def test_the_alpaca_py_pin_matches_the_installed_version(read_pin) -> None:
    """The version Render installs, the version we pin, and the version tested: one.

    Render builds with pip install -e ., so pyproject is what it actually resolves;
    requirements.txt is pinned to the same value so the two cannot drift apart.
    """
    assert read_pin() == metadata.version("alpaca-py")
