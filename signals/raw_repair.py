"""Repair holes in freshly fetched raw closes, from Alpaca IEX daily bars.

`load_universe_close` outer-joins the per-ticker raw parquets, so a session one
ticker lacks becomes NaN for that ticker on a date the rest of the universe has.
That silently changes the signal: the NaN return makes the 63 day rolling vol
NaN, the name's position becomes undefined, `apply_rebalance_control` carries its
previous weight forward, and the final joint gross re-cap multiplies the whole row
by one factor. On 2026-09-24 that shipped a book whose EFA, EEM, IEF, HYG and LQD
weights had all moved by exactly 0.765.

`ingest` calls `repair_frames` on what it just fetched, so the live path self-heals
instead of relying on someone noticing. Two deliberate limits:

  - Holes only. A session every ticker is missing is a market closure or a whole
    fetch problem, not a hole, and is left alone.
  - At most `MAX_HOLE_REPAIR_SESSIONS` per ticker. One or two missing sessions is
    a transient vendor gap; a longer run means the history is broken, and papering
    over it would be worse than letting the data-quality gate block the signal.

Nothing here fabricates a price. A real bar goes in or the hole is reported.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

MAX_HOLE_REPAIR_SESSIONS: int = 2
SOURCE_TAG: str = "alpaca_iex"


def close_matrix(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """adj_close per ticker, outer-joined, exactly as load_universe_close sees it."""
    return pd.concat(
        [df[["adj_close"]].rename(columns={"adj_close": t}) for t, df in frames.items()],
        axis=1,
        join="outer",
    ).sort_index()


def select_holes_for_repair(
    close: pd.DataFrame,
    max_sessions: int = MAX_HOLE_REPAIR_SESSIONS,
) -> tuple[list[dict], dict[str, str]]:
    """Split detected holes into repairable ones and per-ticker refusals.

    Returns `(repairable, refused)` where `refused` maps ticker -> reason. A
    ticker with more than `max_sessions` holes is refused as a whole rather than
    partially filled, so a broken history is never half-patched into looking
    healthy while the gate still has something real to block on.
    """
    from signals.data_quality import find_row_gaps

    by_ticker: dict[str, list[dict]] = {}
    for gap in find_row_gaps(close):
        by_ticker.setdefault(gap["ticker"], []).append(gap)

    repairable: list[dict] = []
    refused: dict[str, str] = {}
    for ticker in sorted(by_ticker):
        gaps = by_ticker[ticker]
        if any(g["date"] is None for g in gaps):
            refused[ticker] = "ticker has no observations at all"
            continue
        if len(gaps) > max_sessions:
            refused[ticker] = (
                f"{len(gaps)} missing sessions exceeds the {max_sessions} "
                f"per-ticker repair cap, so none were filled"
            )
            continue
        repairable.extend(gaps)
    return repairable, refused


def adjustment_ratio(frame: pd.DataFrame, before: pd.Timestamp) -> float:
    """That ticker's own cumulative adj_close/close factor, from before `before`.

    Alpaca returns unadjusted OHLC and this pipeline reads `adj_close`, so the
    inserted row is scaled onto the ticker's existing basis rather than introducing
    a second convention.
    """
    prior = frame.loc[frame.index < before]
    if prior.empty:
        return 1.0
    last = prior.iloc[-1]
    close = float(last.get("close", float("nan")))
    adj = float(last.get("adj_close", float("nan")))
    if not (close > 0) or pd.isna(adj):
        logger.warning("adjustment_ratio: unusable prior row, falling back to 1.0")
        return 1.0
    return adj / close


def _default_bar_lookup(tickers: list[str], dates: list[pd.Timestamp]) -> dict:
    """Daily bars from Alpaca IEX. Returns {} on any failure, never raises.

    A repair that cannot run must not break the ingest that called it: the
    data-quality gate downstream is the backstop.
    """
    import os

    if not (os.environ.get("ALPACA_PAPER_API_KEY") and os.environ.get("ALPACA_PAPER_SECRET_KEY")):
        logger.warning("hole repair: ALPACA credentials absent, skipping")
        return {}
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(
            os.environ["ALPACA_PAPER_API_KEY"], os.environ["ALPACA_PAPER_SECRET_KEY"]
        )
        try:
            from execution.alpaca_paper import apply_request_timeout
            apply_request_timeout(client)
        except Exception:  # pragma: no cover - timeout is best effort here
            pass

        start = datetime.combine(min(dates).date(), time.min, tzinfo=timezone.utc)
        end = datetime.combine(
            max(dates).date() + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=sorted(set(tickers)),
            timeframe=TimeFrame.Day,
            feed=DataFeed.IEX,
            start=start,
            end=end,
        ))
        return {
            (sym, bar.timestamp.date()): bar
            for sym, series in bars.data.items()
            for bar in series
        }
    except Exception as exc:
        logger.warning(
            "hole repair: Alpaca IEX lookup failed (%s: %s), skipping repair",
            type(exc).__name__, exc,
        )
        return {}


def repair_frames(
    frames: dict[str, pd.DataFrame],
    max_sessions: int = MAX_HOLE_REPAIR_SESSIONS,
    bar_lookup=None,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Fill repairable holes in `frames`. Returns (frames, report).

    `bar_lookup(tickers, dates) -> {(ticker, date.date()): bar}` is injectable so
    the repair is testable without a broker. Frames are only rebuilt for tickers
    that actually gained a row, and are returned untouched otherwise.
    """
    report: dict = {"filled": [], "refused": {}, "unresolved": [], "holes": 0}
    if not frames:
        return frames, report

    close = close_matrix(frames)
    repairable, refused = select_holes_for_repair(close, max_sessions)
    report["refused"] = refused
    report["holes"] = len(repairable)

    for ticker, reason in sorted(refused.items()):
        logger.error(
            "hole repair REFUSED for %s: %s. Not filling; the data-quality gate "
            "will block the signal until this is fixed.",
            ticker, reason,
        )

    if not repairable:
        if not refused:
            logger.info("hole repair: no holes found, nothing to do")
        return frames, report

    for gap in repairable:
        logger.warning(
            "hole repair: %s missing %s that the rest of the universe has",
            gap["ticker"], gap["date"].date(),
        )

    lookup = _default_bar_lookup if bar_lookup is None else bar_lookup
    try:
        bars = lookup(
            sorted({g["ticker"] for g in repairable}),
            [g["date"] for g in repairable],
        )
    except Exception as exc:
        logger.warning(
            "hole repair: bar lookup raised (%s: %s), skipping",
            type(exc).__name__, exc,
        )
        bars = {}

    out = dict(frames)
    added: dict[str, list[dict]] = {}
    for gap in repairable:
        ticker, date = gap["ticker"], gap["date"]
        bar = bars.get((ticker, date.date()))
        if bar is None:
            logger.error(
                "hole repair: no %s bar for %s %s, leaving the hole in place",
                SOURCE_TAG, ticker, date.date(),
            )
            report["unresolved"].append({"ticker": ticker, "date": date})
            continue
        ratio = adjustment_ratio(frames[ticker], date)
        row = {
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "adj_close": float(bar.close) * ratio,
            "volume": int(bar.volume),
        }
        added.setdefault(ticker, []).append((date, row))
        logger.info(
            "hole repair: filled %s %s source=%s close=%.4f adj_ratio=%.6f",
            ticker, date.date(), SOURCE_TAG, float(bar.close), ratio,
        )
        report["filled"].append({"ticker": ticker, "date": date})

    for ticker, rows in added.items():
        frame = frames[ticker]
        add = pd.DataFrame(
            [r for _, r in rows], index=pd.DatetimeIndex([d for d, _ in rows], name=frame.index.name)
        )
        combined = pd.concat([frame, add[frame.columns]], axis=0).sort_index()
        if combined.index.has_duplicates:
            logger.error("hole repair: %s would gain duplicate dates, skipping", ticker)
            continue
        out[ticker] = combined

    logger.info(
        "hole repair summary: %d filled, %d refused, %d unresolved, source=%s",
        len(report["filled"]), len(report["refused"]),
        len(report["unresolved"]), SOURCE_TAG,
    )
    return out, report
