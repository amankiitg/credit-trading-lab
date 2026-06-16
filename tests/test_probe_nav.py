"""Tests for scripts/probe_nav.py's response-validation logic (G0a).

No network calls are made in this test module. The legacy iShares CSV
export was confirmed dead by a live probe documented in
data/processed/nav_audit.md (it returns the ordinary HTML product page
under a text/csv content-type). These tests pin down that the parser
correctly rejects that response shape, and correctly accepts a
genuine-looking CSV, using synthetic fixture text only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.probe_nav import (
    AUDIT_PATH,
    MIN_TRADING_DAYS,
    _looks_like_html,
    _parse_nav_csv,
    g0a_verdict,
)


def test_html_response_detected() -> None:
    html = "<!DOCTYPE html>\n<html><head></head><body>not csv</body></html>"
    assert _looks_like_html(html)


def test_genuine_csv_not_flagged_as_html() -> None:
    csv_text = "Date,NAV\n2020-01-02,100.12\n2020-01-03,100.45\n"
    assert not _looks_like_html(csv_text)


def test_parse_nav_csv_extracts_date_and_nav() -> None:
    csv_text = "Date,NAV,Other\n2020-01-02,100.12,x\n2020-01-03,100.45,y\n"
    df = _parse_nav_csv(csv_text)
    assert list(df.columns) == ["nav"]
    assert df.index.name == "date"
    assert df.loc[pd.Timestamp("2020-01-02"), "nav"] == 100.12


def test_parse_nav_csv_raises_without_nav_column() -> None:
    csv_text = "Date,Price\n2020-01-02,100.12\n"
    with pytest.raises(ValueError):
        _parse_nav_csv(csv_text)


def test_g0a_verdict_requires_min_trading_days() -> None:
    short = {
        "HYG": {"ok": True, "n_days": MIN_TRADING_DAYS - 1},
        "LQD": {"ok": True, "n_days": MIN_TRADING_DAYS},
    }
    assert not g0a_verdict(short)

    enough = {
        "HYG": {"ok": True, "n_days": MIN_TRADING_DAYS},
        "LQD": {"ok": True, "n_days": MIN_TRADING_DAYS + 50},
    }
    assert g0a_verdict(enough)


def test_g0a_verdict_fails_if_fetch_not_ok() -> None:
    results = {
        "HYG": {"ok": False, "n_days": 0},
        "LQD": {"ok": True, "n_days": MIN_TRADING_DAYS + 50},
    }
    assert not g0a_verdict(results)


def test_audit_file_was_written_by_a_real_probe_run() -> None:
    """data/processed/nav_audit.md is produced by running
    `python scripts/probe_nav.py` -- this test only checks it exists
    and records a verdict, not that G0a passed.
    """
    assert AUDIT_PATH.exists(), "run python scripts/probe_nav.py first"
    text = AUDIT_PATH.read_text()
    assert "G0a verdict:" in text
