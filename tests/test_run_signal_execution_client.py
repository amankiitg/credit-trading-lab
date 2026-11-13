"""Step 8's execution P&L must be given the client step 7 already built.

The 2026-11-06 evening email said:

    Execution P&L: not available (no Alpaca client)

Step 7 read the paper account without trouble -- it asks for a client with
`connect(dry_run=False)`. Step 8's execution component called `connect()` with no
arguments, and `DRY_RUN_DEFAULT` is true on the cron, so `connect()` returned None by
design: the traded part of every session would have been dropped silently, on a read
that never places an order.

These tests drive `run_signal.main()` end to end -- the real steps 1 through 8, the real
sleeve arithmetic, the real email -- with one fake client injected at the single place
step 7 gets its client (`execution.alpaca_paper.connect`). The fake connect keeps the
real dry-run gate, so a bare `connect()` still returns None exactly as production does.
That is what makes the first test the one that would have caught this.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

SESSION = "2026-11-06"
PRIOR_SESSION = "2026-11-05"

# Two fills on the session being measured. The fractional seconds are the shape Alpaca
# actually sends, and the bug fixed on 2026-11-05 was that they were unparseable.
FILLS = [
    {
        "symbol": "TLT",
        "side": "buy",
        "qty": "100",
        "price": "90.00",
        "transaction_time": f"{SESSION}T14:31:31.35178+00:00",
    },
    {
        "symbol": "HYG",
        "side": "sell",
        "qty": "50",
        "price": "80.00",
        "transaction_time": f"{SESSION}T16:02:11.000001+00:00",
    },
]

SHARES = 100.0


class FakeAlpaca:
    """A paper TradingClient reduced to the four reads these two steps make.

    `get_account` and `get_all_positions` are step 7's snapshot refresh;
    `get_activities` and `get_portfolio_history` are step 8's execution P&L. Every call is
    recorded, so a test can prove the same object reached both steps.
    """

    def __init__(self, *, equity: dict[str, float]) -> None:
        self._equity = equity
        self.reads: list[str] = []

    def get_account(self):
        self.reads.append("account")
        return SimpleNamespace(equity="100000.00")

    def get_all_positions(self):
        self.reads.append("positions")
        return []

    def get_activities(self, request):
        self.reads.append("activities")
        return [dict(fill) for fill in FILLS]

    def get_portfolio_history(self, request):
        self.reads.append("portfolio_history")
        history = SimpleNamespace()
        history.timestamp = [
            int(
                (date.fromisoformat(day) - date(1970, 1, 1)).total_seconds()
            )
            for day in sorted(self._equity)
        ]
        history.equity = [self._equity[day] for day in sorted(self._equity)]
        return history


def _close_frame() -> pd.DataFrame:
    """Two hundred sessions of synthetic closes ending on the session under test."""
    import signals.etf_universe as etf

    index = pd.date_range(end=SESSION, periods=200, freq="B")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            ticker: 100 + np.cumsum(rng.normal(0, 0.4, len(index)))
            for ticker in etf.UNIVERSE
        },
        index=index,
    )


def _book_and_execution(close: pd.DataFrame) -> tuple[float, float]:
    """The two numbers the reconciliation adds up, computed independently of the code.

    `book` is the sleeve decomposition of the book held at the prior close; `execution` is
    shares traded x (close - fill). Neither is read back out of the report -- a test that
    asserted the code against itself would have passed on the broken wiring too.
    """
    close_prev = close.loc[PRIOR_SESSION]
    close_now = close.loc[SESSION]
    book = sum(SHARES * (close_now[t] - close_prev[t]) for t in close_prev.index)
    execution = sum(
        (1 if fill["side"] == "buy" else -1)
        * float(fill["qty"])
        * (close_now[fill["symbol"]] - float(fill["price"]))
        for fill in FILLS
    )
    return float(book), float(execution)


def _run_signal_with(
    monkeypatch,
    *,
    gap: float = 0.0,
    client_available: bool = True,
    close_equity_write: str = "ok",
    no_real_email,
) -> dict:
    """Drive the real `run_signal.main()` with one fake client where step 7 gets it.

    Everything that would touch a database, Yahoo or the repository's data files is
    stubbed; the steps themselves, their order and the sleeve arithmetic are real.
    """
    import dashboard.supabase_client as sb
    import execution.alpaca_paper as ap
    import execution.calendar_utils as cal
    import scripts.run_signal as run_signal
    import signals.dividends as div
    import signals.etf_universe as etf

    close = _close_frame()
    book, execution = _book_and_execution(close)
    expected = book + execution  # carry is zero: no distributions in this fixture

    opening = 100_000.0
    closing = opening + expected + gap
    # Alpaca stamps a close with the following calendar day, which is what
    # risk.execution_pnl.close_equity reads.
    equity = {
        (date.fromisoformat(PRIOR_SESSION) + timedelta(days=1)).isoformat(): opening,
        (date.fromisoformat(SESSION) + timedelta(days=1)).isoformat(): closing,
    }

    client = FakeAlpaca(equity=equity)
    connects: list[bool] = []

    def _connect(dry_run=None):
        """The real gate, kept honest: no client at all when dry_run is true.

        `DRY_RUN_DEFAULT` is true on the cron, so `connect()` returns None there. A stub
        that returned the client unconditionally would let the old bare `connect()` pass
        this test, which is the whole bug.
        """
        gate_on = ap.DRY_RUN_DEFAULT if dry_run is None else dry_run
        connects.append(not gate_on)
        if gate_on or not client_available:
            return None
        return client

    monkeypatch.setattr(ap, "DRY_RUN_DEFAULT", True)  # independent of the dev's shell
    monkeypatch.setattr(ap, "connect", _connect)

    # The two broker reads are thin adapters over alpaca-py, which is not installed in
    # this venv and would therefore return nothing. The client hand-off is what is under
    # test, so they are replaced -- and they record which client they were given, so the
    # test can prove it is step 7's.
    handed: dict[str, object] = {}

    def _fetch_session_fills(got_client, session_date):
        handed["fills"] = got_client
        return [dict(fill) for fill in FILLS]

    def _fetch_equity_history(got_client, session_date):
        handed["equity"] = got_client
        return dict(equity)

    monkeypatch.setattr(ap, "fetch_session_fills", _fetch_session_fills)
    monkeypatch.setattr(ap, "fetch_equity_history", _fetch_equity_history)

    written: list[dict] = []
    recorded: list[tuple[str, str]] = []
    close_writes: list[dict] = []

    def _write_close_equity(closes, *, source):
        close_writes.append({"closes": dict(closes), "source": source})
        if close_equity_write == "raises":
            raise RuntimeError("close_equity is not in the schema cache")
        return close_equity_write != "false"

    monkeypatch.setattr(sb, "write_close_equity", _write_close_equity)

    monkeypatch.setattr(cal, "is_trading_day", lambda day: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, day: False)
    monkeypatch.setattr(cal, "record_run", lambda job, day: recorded.append((job, day)))
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(sb, "get_supabase_client", lambda: None)
    monkeypatch.setattr(sb, "get_setting", lambda key: None)
    monkeypatch.setattr(sb, "set_setting", lambda key, value: True)
    monkeypatch.setattr(sb, "write_decision", lambda day, decision: True)
    monkeypatch.setattr(sb, "write_positions", lambda rows: True)
    monkeypatch.setattr(sb, "execution_column_exists", lambda: True)
    monkeypatch.setattr(sb, "fetch_daily_sleeve_pnl", lambda limit=400, **k: [])
    monkeypatch.setattr(
        sb,
        "fetch_position_snapshot",
        lambda day, **k: [{"ticker": t, "shares": SHARES} for t in etf.UNIVERSE],
    )
    monkeypatch.setattr(
        sb, "write_daily_sleeve_pnl", lambda rows: written.extend(rows) or True
    )
    monkeypatch.setattr(
        div,
        "ingest",
        lambda tickers, **k: {
            "ok": True,
            "tickers": 8,
            "distributions": 0,
            "as_of": SESSION,
        },
    )
    monkeypatch.setattr(div, "dividends_as_of", lambda raw_dir=None: SESSION)
    monkeypatch.setattr(
        div,
        "load_dividend_matrix",
        lambda tickers, close_index, **k: pd.DataFrame(
            0.0, index=close_index, columns=list(etf.UNIVERSE)
        ),
    )
    monkeypatch.setattr(run_signal, "RETRY_INTERVAL_SECS", 0)

    code = run_signal.main(max_secs=120)

    assert code == 0, "the evening job must still finish"
    assert len(no_real_email) == 1, "exactly one summary email per run"

    return {
        "code": code,
        "body": no_real_email[0]["body"],
        "subject": no_real_email[0]["subject"],
        "written": written,
        "client": client,
        "handed": handed,
        "connects": connects,
        "book": book,
        "execution": execution,
        "expected": expected,
        "gap": gap,
        "recorded": recorded,
        "close_writes": close_writes,
    }


def test_step_eight_prices_the_session_with_the_client_step_seven_built(
    monkeypatch, no_real_email
) -> None:
    """The catch test. It fails on the old code with "no Alpaca client" in the email.

    Before the fix, step 8 called `connect()` with no arguments, `DRY_RUN_DEFAULT`
    returned None, and the execution component reported itself unavailable -- on every
    evening, not just this one.
    """
    out = _run_signal_with(monkeypatch, no_real_email=no_real_email)

    body = out["body"]
    assert "no Alpaca client" not in body, body
    assert "Execution P&L: not available" not in body, body
    assert "Execution P&L: 2 fills" in body, body
    assert f"{out['execution']:+,.2f} total" in body, body

    # One client, built once, read by both steps: step 7's own snapshot reads came
    # through it, and step 8's two broker reads were handed the very same object.
    assert out["client"].reads == ["account", "positions"]
    assert out["handed"] == {"fills": out["client"], "equity": out["client"]}
    assert out["connects"] == [True], "the client must be requested with dry_run=False"

    # The per-sleeve split is written into the session's rows.
    assert len(out["written"]) == 4
    assert {row["trade_date"] for row in out["written"]} == {SESSION}
    assert all(row["execution_pnl"] is not None for row in out["written"])
    assert sum(row["execution_pnl"] for row in out["written"]) == pytest.approx(
        out["execution"]
    )


def test_the_execution_pnl_is_reconciled_against_the_sessions_equity_change(
    monkeypatch, no_real_email
) -> None:
    """A reconciliation is computed, and a gap is reported as a warning -- not a refusal.

    The equity change is $300 more than the book plus the traded part explains, which is
    exactly the case the email has to show: the sleeve rows are still written.
    """
    out = _run_signal_with(monkeypatch, gap=300.0, no_real_email=no_real_email)

    body = out["body"]
    assert "Execution P&L: 2 fills" in body, body
    assert "WARNING: execution P&L does not reconcile" in body, body
    assert f"a gap of {300.0:+,.2f} dollars" in body, body
    assert f"{out['expected']:+,.2f}" in body, body

    # A warning is not a refusal: the day is still decomposed and written.
    assert len(out["written"]) == 4
    assert out["subject"].startswith("[OK]")


def test_a_matching_equity_change_reconciles_without_a_warning(
    monkeypatch, no_real_email
) -> None:
    out = _run_signal_with(monkeypatch, no_real_email=no_real_email)

    assert "WARNING: execution P&L does not reconcile" not in out["body"], out["body"]
    assert "reconcile" not in out["body"], out["body"]


def test_without_a_client_the_day_is_still_written_and_says_so(
    monkeypatch, no_real_email
) -> None:
    """The graceful path, kept: no client means an honest line in the email, not a crash.

    This is the behaviour the old code produced by accident. It must still be reachable
    when the credentials really are absent, and it must never cost the session its sleeve
    rows.
    """
    out = _run_signal_with(
        monkeypatch, client_available=False, no_real_email=no_real_email
    )

    body = out["body"]
    assert "Execution P&L: not available (no Alpaca client)" in body, body
    assert len(out["written"]) == 4, "carry, price and total are still recorded"
    assert all(row["execution_pnl"] is None for row in out["written"])


# ------------------------------------------------- the close series, step 8b
#
# The dashboard's evening NAV comes from close_equity, which this job fills from the
# portfolio history it already read for step 8's reconciliation. The two reads are the
# same one, so the rows must appear without a second broker call -- and a failed write
# must be a log line, not a failed run.


def test_step_eight_b_stores_the_closes_the_reconciliation_read(monkeypatch, no_real_email) -> None:
    out = _run_signal_with(monkeypatch, no_real_email=no_real_email)

    assert out["close_writes"] == [
        {
            "closes": {PRIOR_SESSION: 100_000.0},
            "source": "alpaca_portfolio_history",
        }
    ], "the prior session's published close, and only that one"


def test_a_raised_close_equity_write_never_fails_the_run(monkeypatch, no_real_email) -> None:
    out = _run_signal_with(
        monkeypatch, close_equity_write="raises", no_real_email=no_real_email
    )

    assert out["code"] == 0
    assert out["subject"].startswith("[OK]")
    assert len(out["written"]) == 4, "the sleeve rows are unaffected"
    assert f"{out['execution']:+,.2f} total" in out["body"], "the signal's numbers stand"


def test_a_rejected_close_equity_write_is_also_non_fatal(monkeypatch, no_real_email) -> None:
    out = _run_signal_with(
        monkeypatch, close_equity_write="false", no_real_email=no_real_email
    )

    assert out["code"] == 0
    assert out["subject"].startswith("[OK]")
    assert len(out["written"]) == 4
