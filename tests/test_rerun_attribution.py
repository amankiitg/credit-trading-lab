"""The attribution-only rerun: one session, four rows, nothing else.

The nightly job writes the session it just closed and never looks back, so a row written
from a wrong input stays wrong. Re-running the whole job to repair it would re-derive the
signal and take the trading decisions with it. This script does the minimum instead, and
these tests pin the "minimum": one date, four rows, the same gate, and no way to reach a
trade.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import risk.sleeve_pnl as sp
import scripts.rerun_attribution as ra
from signals.etf_universe import UNIVERSE

REPO_ROOT = Path(__file__).resolve().parents[1]

TRADE_DATE = "2026-11-02"
PRIOR_DATE = "2026-10-30"

IDX = pd.date_range("2026-01-02", "2026-11-04", freq="B")
CLOSE = pd.DataFrame({t: 100.0 for t in UNIVERSE}, index=IDX)

# The book held into 2026-11-02, short all four monthly payers.
POSITIONS = [
    {"ticker": "TLT", "shares": -330.0},
    {"ticker": "IEF", "shares": -293.0},
    {"ticker": "HYG", "shares": -327.0},
    {"ticker": "LQD", "shares": -251.0},
]

# What the override CSV says for 2026-11-02, which is the whole point of the rerun.
EXPECTED_CARRY = -(330 * 0.3432 + 293 * 0.3115 + 327 * 0.4014 + 251 * 0.4000)


@pytest.fixture
def sb(monkeypatch):
    """Supabase stubbed where the script binds it, and a write spy.

    The script imports these names at module scope, so patching
    `dashboard.supabase_client` would leave the script holding the originals.
    """
    written: list[list[dict]] = []

    monkeypatch.setattr(ra, "fetch_position_snapshot", lambda d: list(POSITIONS))
    monkeypatch.setattr(ra, "fetch_daily_sleeve_pnl", lambda limit=2000: [])
    monkeypatch.setattr(
        ra, "write_daily_sleeve_pnl", lambda rows: written.append(rows) or True
    )
    return written


def test_a_dry_run_writes_nothing(sb) -> None:
    """The mode you reach for first must be safe to reach for."""
    report = ra.recompute(TRADE_DATE, write=False, close=CLOSE)

    assert report["status"] == sp.STATUS_OK
    assert report["written"] is False
    assert sb == [], "a dry run must not touch the table"
    assert len(report["rows"]) == 4


def test_a_real_run_upserts_exactly_four_rows_for_that_date(sb) -> None:
    """Four sleeves, one date. Not five rows, not two dates."""
    report = ra.recompute(TRADE_DATE, write=True, close=CLOSE)

    assert report["written"] is True
    assert len(sb) == 1, "one upsert, not one per row"
    rows = sb[0]

    assert len(rows) == 4
    assert {r["sleeve"] for r in rows} == {"credit", "rates", "equity", "commodity"}
    assert {str(r["trade_date"]) for r in rows} == {TRADE_DATE}, "this date and no other"
    assert report["prior_date"] == PRIOR_DATE


def test_the_override_drives_the_recomputed_carry(sb) -> None:
    """The reason the rerun exists: 2026-11-02 had carry zero because the payout was
    not in the vendor feed, and the override is what supplies it."""
    report = ra.recompute(TRADE_DATE, write=True, close=CLOSE)

    carry = sum(r["carry_pnl"] for r in report["rows"])
    assert carry == pytest.approx(EXPECTED_CARRY, abs=0.01)
    assert carry == pytest.approx(-436.18, abs=0.01)

    by_sleeve = {r["sleeve"]: r["carry_pnl"] for r in report["rows"]}
    assert by_sleeve["rates"] == pytest.approx(-113.256 - 91.2695, abs=0.01)
    assert by_sleeve["credit"] == pytest.approx(-131.2578 - 100.4, abs=0.01)
    assert by_sleeve["equity"] == pytest.approx(0.0)
    assert by_sleeve["commodity"] == pytest.approx(0.0)


def test_a_date_the_close_cache_does_not_hold_is_refused(sb) -> None:
    report = ra.recompute("2026-11-07", write=True, close=CLOSE)  # a Saturday, no bar

    assert report["status"] == "refused"
    assert "no row" in report["reason"]
    assert sb == []


def test_a_missing_snapshot_is_refused(sb, monkeypatch) -> None:
    """No prior book means the session cannot be attributed, not that it was flat."""
    monkeypatch.setattr(ra, "fetch_position_snapshot", lambda d: [])

    report = ra.recompute(TRADE_DATE, write=True, close=CLOSE)

    assert report["status"] == "refused"
    assert PRIOR_DATE in report["reason"]
    assert sb == []


def test_a_session_that_does_not_reconcile_is_refused(sb, monkeypatch) -> None:
    """The same gate as the job: the script must not have a weaker one."""
    mismatch = sp.SleevePnlResult(sp.STATUS_MISMATCH, "the sleeves do not reconcile")
    monkeypatch.setattr(ra, "compute_sleeve_pnl", lambda **kw: mismatch)

    report = ra.recompute(TRADE_DATE, write=True, close=CLOSE)

    assert report["status"] == "refused"
    assert "reconcile" in report["reason"]
    assert sb == []


def test_a_rejected_write_is_reported_as_a_failure(sb, monkeypatch) -> None:
    monkeypatch.setattr(ra, "write_daily_sleeve_pnl", lambda rows: False)

    report = ra.recompute(TRADE_DATE, write=True, close=CLOSE)

    assert report["status"] == sp.STATUS_OK
    assert report["written"] is False


def test_the_cli_exits_non_zero_when_the_date_cannot_be_computed(sb) -> None:
    assert ra.main(["--date", "2026-11-07", "--dry-run"]) == 1
    assert ra.main(["--date", "not-a-date"]) == 2


def test_the_prior_session_is_the_one_before_this_date_not_the_last_in_the_cache() -> None:
    """The nightly job always measures the last session in the cache.

    A rerun of an older date must not inherit that: 2026-11-02's book is the one held at
    the close of 2026-10-30, even though the cache runs to 2026-11-04.
    """
    assert str(CLOSE.index[CLOSE.index.get_loc(pd.Timestamp(TRADE_DATE)) - 1].date()) == PRIOR_DATE


def test_the_script_cannot_reach_a_trade() -> None:
    """Importing it must not pull in the job machinery, and it must never touch runs.

    A separate interpreter on purpose: by the time this test runs, the rest of the suite
    has imported half of execution/ already, so sys.modules here proves nothing.
    """
    code = (
        "import sys; import scripts.rerun_attribution; "
        "bad = sorted(n for n in sys.modules if n == 'execution' or n.startswith('execution.')); "
        "print('|'.join(bad))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr

    # execution.costs is reached through the arithmetic path -- risk.attribution imports
    # backtest.multi_asset, which imports the cost-parameter dataclass. It is a frozen
    # record of cost constants with no client and no order in it, so allowing it costs
    # nothing; everything that could size, submit or record a trade is refused.
    allowed = {"execution", "execution.costs"}
    reached = set(result.stdout.strip().split("|")) - {""}
    assert reached - allowed == set(), f"imported from execution: {sorted(reached - allowed)}"

    source = (REPO_ROOT / "scripts" / "rerun_attribution.py").read_text()
    assert "record_run" not in source, "the rerun must never write a run marker"
    assert "write_cron_run" not in source
    assert "alpaca_paper" not in source, "no broker client of any kind"
    assert "from execution" not in source
    assert "input(" not in source, "no interactive prompt in a cronless tool"
