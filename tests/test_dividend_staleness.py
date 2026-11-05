"""Schedule-aware validation of the distribution refresh, and the override feeding it.

The 2026-10 sleeve rows came back with carry identically zero for 24 sessions. The cause
was not the arithmetic: the vendor's history for TLT, IEF, HYG and LQD stopped at
2026-09-01, so the October and November payouts were simply absent. Because the price leg
reads the unadjusted close, each missing payout was booked as a price move instead.

The freshness gate could not see it. It compares the date the cache was refreshed against
the session, and a refresh that succeeds while returning a stale feed still stamps
today's date -- so a marker-only check vouches for a hole it was built to refuse.

This file pins the three things that close it: a schedule check on the payers that
actually have a monthly schedule, a hand-curated override merged in before that check,
and a write order that leaves a refused refresh with the previous cache and marker.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import signals.dividends as div

AS_OF = date(2026, 11, 5)

# A monthly payer's history as the vendor actually returned it in November 2026: the
# last row is the September payout, and the two months after it are missing.
STALE_TLT = [("2026-08-03", 0.3049), ("2026-09-01", 0.3116)]


def _series(pairs: list[tuple[str, float]]) -> pd.Series:
    """A tz-naive, midnight-indexed ex-date series, as the fetch produces."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in pairs]).normalize()
    return pd.Series([a for _, a in pairs], index=idx, name="dividend")


def _freeze_as_of(monkeypatch, day: date) -> None:
    """Pin the validator's clock without changing the validator.

    The month-start rule is relative to today, so a test that leaves the clock alone
    starts failing the moment the real calendar moves past the fixture's last payout.
    """
    real = div.validate_monthly_coverage
    monkeypatch.setattr(
        div,
        "validate_monthly_coverage",
        lambda data, as_of=None, tickers=None: real(
            data, as_of=day, tickers=tickers
        ),
    )


# ------------------------------------------------------------------ the schedule check


def test_a_monthly_payer_that_stopped_paying_is_refused() -> None:
    """The live failure: a monthly payer whose history ends two months ago."""
    data = {"TLT": _series(STALE_TLT)}

    with pytest.raises(div.StaleDividendCacheError) as exc:
        div.validate_monthly_coverage(data, as_of=AS_OF)

    message = str(exc.value)
    assert "TLT" in message
    assert "2026-10-01" in message, "the first month the vendor never delivered"
    assert "2026-11-02" in message, "and the current month, once its grace period passed"
    assert "2026-11-05" in message, "the refusal has to say what it was judging"


def test_a_monthly_payer_is_not_refused_on_its_own_ex_date() -> None:
    """The grace period exists so the ex-date session is not a failure.

    On 2026-11-02 itself the payout is due but not yet late: the vendor posts during the
    day, and refusing the session that measures it would refuse every month start.
    """
    data = {"TLT": _series(STALE_TLT + [("2026-11-02", 0.3432)])}

    div.validate_monthly_coverage(data, as_of=date(2026, 11, 2))

    with pytest.raises(div.StaleDividendCacheError):
        div.validate_monthly_coverage(
            {"TLT": _series(STALE_TLT)}, as_of=date(2026, 11, 4)
        )


def test_a_january_gap_is_not_a_failure() -> None:
    """iShares paid 2025-12-19 and then 2026-02-02: there is no January ex-date.

    Demanding one would refuse January every year, for a month that has no payout by
    construction. The December payment also lands mid-month rather than on the first
    business day, which is why a finished month is judged on the whole of it.
    """
    data = {"TLT": _series([("2025-11-03", 0.2988), ("2025-12-19", 0.4102)])}

    div.validate_monthly_coverage(data, as_of=date(2026, 1, 20))

    # And the same history does refuse once February's payout is overdue, so the
    # exemption is doing the work rather than the check being vacuously lenient.
    with pytest.raises(div.StaleDividendCacheError) as exc:
        div.validate_monthly_coverage(data, as_of=date(2026, 2, 20))
    assert "2026-02-02" in str(exc.value)


def test_quarterly_and_semiannual_payers_are_not_monthly_checked() -> None:
    """SPY pays quarterly, EFA and EEM semiannually.

    A month-start rule applied to them would refuse every month that is not a pay month,
    which is most of them. The check is schedule-aware or it is useless.
    """
    data = {
        "SPY": _series([("2026-03-20", 1.77)]),
        "EFA": _series([("2026-06-15", 0.9123)]),
        "EEM": _series([("2026-06-15", 0.5781)]),
    }

    div.validate_monthly_coverage(data, as_of=AS_OF)


def test_a_fund_that_never_pays_is_not_a_gap() -> None:
    """GLD holds metal. An empty history is its correct value, not a stalled feed."""
    data = {"GLD": pd.Series(dtype="float64")}

    div.validate_monthly_coverage(data, as_of=AS_OF)


def test_an_empty_monthly_payer_is_refused_once_the_month_is_due() -> None:
    """A monthly payer with no rows at all is the extreme case of a stalled feed."""
    with pytest.raises(div.StaleDividendCacheError):
        div.validate_monthly_coverage(
            {"HYG": pd.Series(dtype="float64")}, as_of=AS_OF
        )


# ------------------------------------------------------------------ the override


def test_the_override_csv_is_read_by_ticker_and_ex_date(tmp_path) -> None:
    path = tmp_path / "override.csv"
    path.write_text(
        "ticker,ex_date,amount,source\n"
        "TLT,2026-10-01,0.31158,TO VERIFY vs iShares\n"
        "hyg,2026-11-02,0.40139,TO VERIFY vs iShares\n"
    )

    out = div.load_distributions_override(path)

    assert set(out) == {"TLT", "HYG"}, "tickers are normalized to upper case"
    assert out["TLT"].loc[pd.Timestamp("2026-10-01")] == pytest.approx(0.31158)
    assert out["HYG"].index[0] == pd.Timestamp("2026-11-02")


def test_an_absent_override_is_not_an_error(tmp_path) -> None:
    """The container may legitimately have no override file; that is not a failure."""
    assert div.load_distributions_override(tmp_path / "missing.csv") == {}
    assert div.load_distributions_override(None) == {}


def test_an_override_missing_a_column_is_rejected(tmp_path) -> None:
    path = tmp_path / "override.csv"
    path.write_text("ticker,ex_date\nTLT,2026-10-01\n")

    with pytest.raises(ValueError) as exc:
        div.load_distributions_override(path)

    assert "amount" in str(exc.value)


def test_the_override_fills_a_missing_ex_date(monkeypatch, tmp_path) -> None:
    """End to end through the fetch: without the override this raises, with it it does not."""

    class _FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return _series(STALE_TLT)

    monkeypatch.setattr(div.yf, "Ticker", _FakeTicker)

    path = tmp_path / "override.csv"
    path.write_text(
        "ticker,ex_date,amount,source\n"
        "TLT,2026-10-01,0.31158,TO VERIFY vs iShares\n"
        "TLT,2026-11-02,0.34318,TO VERIFY vs iShares\n"
    )

    with pytest.raises(div.StaleDividendCacheError):
        div.fetch_dividends(["TLT"], override_path=None, as_of=AS_OF)

    out = div.fetch_dividends(["TLT"], override_path=path, as_of=AS_OF)

    assert out["TLT"].loc[pd.Timestamp("2026-10-01")] == pytest.approx(0.31158)
    assert out["TLT"].loc[pd.Timestamp("2026-11-02")] == pytest.approx(0.34318)
    assert out["TLT"].loc[pd.Timestamp("2026-09-01")] == pytest.approx(0.3116)
    assert list(out["TLT"].index) == sorted(out["TLT"].index), "still ordered by ex-date"


def test_the_override_wins_on_a_conflicting_date(monkeypatch, tmp_path) -> None:
    """A wrong vendor row is also worth correcting without waiting for the vendor."""
    base = _series(
        STALE_TLT + [("2026-10-01", 0.10), ("2026-11-02", 0.3432)]
    )

    class _FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return base

    monkeypatch.setattr(div.yf, "Ticker", _FakeTicker)

    path = tmp_path / "override.csv"
    path.write_text(
        "ticker,ex_date,amount,source\n"
        "TLT,2026-10-01,0.31158,TO VERIFY vs iShares\n"
    )

    out = div.fetch_dividends(["TLT"], override_path=path, as_of=AS_OF)

    assert out["TLT"].loc[pd.Timestamp("2026-10-01")] == pytest.approx(0.31158)
    assert len(out["TLT"]) == 4, "a conflicting date is replaced, not duplicated"


def test_an_override_row_for_a_fund_outside_the_run_is_ignored(monkeypatch) -> None:
    """The fetch must return what it was asked for, not everything the file mentions."""

    class _FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return _series([("2026-03-20", 1.77)])

    monkeypatch.setattr(div.yf, "Ticker", _FakeTicker)

    out = div.fetch_dividends(["SPY"], as_of=AS_OF)

    assert set(out) == {"SPY"}


# ------------------------------------------------------------------ the write order


def test_a_refused_refresh_leaves_the_previous_cache_and_marker(monkeypatch, tmp_path) -> None:
    """Validation runs before anything is written, so a refusal costs nothing.

    The order is the whole guarantee: parquets first, then the marker. If validation
    raised after the parquets, a stale feed would replace a good cache and the marker
    would say today. If the marker went first, a refusal would vouch for whatever was
    left behind.
    """
    _freeze_as_of(monkeypatch, AS_OF)
    good = _series([("2026-09-01", 0.3116), ("2026-10-01", 0.31158), ("2026-11-02", 0.34318)])

    class _GoodTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return good

    monkeypatch.setattr(div.yf, "Ticker", _GoodTicker)
    div.ingest(["TLT"], raw_dir=tmp_path, override_path=None)

    files_before = (tmp_path / "TLT_dividends.parquet").read_bytes()
    marker_before = div.dividends_as_of(tmp_path)
    assert marker_before == AS_OF.isoformat()

    class _StaleTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return _series(STALE_TLT)

    monkeypatch.setattr(div.yf, "Ticker", _StaleTicker)

    with pytest.raises(div.StaleDividendCacheError):
        div.ingest(["TLT"], raw_dir=tmp_path, override_path=None)

    assert (tmp_path / "TLT_dividends.parquet").read_bytes() == files_before
    assert div.dividends_as_of(tmp_path) == marker_before, "the marker must not advance"


def test_a_fresh_container_marker_still_reads_as_fresh_after_a_good_refresh(
    monkeypatch, tmp_path
) -> None:
    """The marker is still written on success, and last: the check it feeds is unchanged."""
    _freeze_as_of(monkeypatch, AS_OF)
    good = _series([("2026-09-01", 0.3116), ("2026-10-01", 0.31158), ("2026-11-02", 0.34318)])

    class _GoodTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def dividends(self):
            return good

    monkeypatch.setattr(div.yf, "Ticker", _GoodTicker)

    report = div.ingest(["TLT"], raw_dir=tmp_path, override_path=None)

    assert report["ok"] is True
    assert report["as_of"] == AS_OF.isoformat()
    assert div.dividends_as_of(tmp_path) == AS_OF.isoformat()


# ------------------------------------------------------------------ step 8 isolation


def test_a_job_timeout_inside_step_eight_does_not_fail_the_run(
    monkeypatch, no_real_email
) -> None:
    """JobTimeout derives from BaseException, so `except Exception` never saw it.

    A 120s step budget expiring inside the sleeve decomposition used to unwind through
    \`step()\`, which re-raises, and fail a run whose signal had already been written --
    losing the summary line that would have explained it, on a day the book traded.
    """
    import numpy as np

    import dashboard.supabase_client as sb
    import execution.calendar_utils as cal
    import execution.job_guard as jg
    import execution.snapshot as snap
    import scripts.run_signal as run_signal
    import signals.etf_universe as etf

    idx = pd.date_range(end=date.today(), periods=200, freq="B")
    rng = np.random.default_rng(11)
    close = pd.DataFrame(
        {t: 100 + np.cumsum(rng.normal(0, 0.5, len(idx))) for t in etf.UNIVERSE},
        index=idx,
    )

    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(cal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(cal, "check_already_ran", lambda job, d: False)
    monkeypatch.setattr(cal, "record_run", lambda job, d: recorded.append((job, d)))
    monkeypatch.setattr(etf, "ingest", lambda *a, **k: None)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)
    monkeypatch.setattr(sb, "get_setting", lambda k: None)
    monkeypatch.setattr(sb, "set_setting", lambda k, v: True)
    monkeypatch.setattr(sb, "write_decision", lambda d, dec: True)
    monkeypatch.setattr(
        snap,
        "refresh_snapshot",
        lambda **kw: {"written": True, "nav": 101_000.0, "tickers": 8,
                      "skipped": None, "failed": False},
    )
    monkeypatch.setattr(
        div, "ingest",
        lambda tickers, **kw: {"ok": True, "tickers": 8, "distributions": 1329,
                               "as_of": date.today().isoformat()},
    )
    monkeypatch.setattr(run_signal, "RETRY_INTERVAL_SECS", 0)

    # Step 8's own deadline fires here, deep inside the real record_daily_sleeve_pnl.
    def _timeout(raw_dir=None):
        raise jg.JobTimeout("the 120s sleeve budget expired")

    monkeypatch.setattr(div, "dividends_as_of", _timeout)

    code = run_signal.main(max_secs=120)

    assert code == 0, "a sleeve timeout must not fail a run whose signal was written"
    assert recorded == [("run_signal", date.today().isoformat())], "cron_runs still written"

    body = no_real_email[0]["body"]
    assert "Sleeve P&L FAILED (error): JobTimeout" in body
    assert no_real_email[0]["subject"].startswith("[OK]")


def test_the_sleeve_report_carries_the_timeout_as_a_named_reason(monkeypatch) -> None:
    """The report, not an exception: step 8 returns what it could not do."""
    import execution.job_guard as jg
    import risk.sleeve_pnl as sp
    import signals.etf_universe as etf

    idx = pd.date_range("2026-09-01", "2026-11-04", freq="B")
    close = pd.DataFrame({t: 100.0 for t in etf.UNIVERSE}, index=idx)
    monkeypatch.setattr(etf, "load_universe_close", lambda *a, **k: close)

    def _timeout(raw_dir=None):
        raise jg.JobTimeout("the 120s sleeve budget expired")

    monkeypatch.setattr(div, "dividends_as_of", _timeout)

    report = sp.record_daily_sleeve_pnl(as_of_date="2026-11-04")

    assert report["written"] is False
    assert report["status"] == "error"
    assert report["reason"].startswith("JobTimeout:")
    assert "120s sleeve budget" in report["reason"]
