-- Sprint v8.6 supplemental schema
-- Run in the Supabase SQL editor at:
--   https://supabase.com/dashboard/project/omnsjnosbaiqkrmnknqw/sql/new
--
-- These tables complement the v8.5 schema (decisions, positions, pnl_log, settings).
-- Safe to re-run (IF NOT EXISTS guards).

-- Fix decisions check constraint: v8.5 only allowed APPROVE/REJECT (uppercase).
-- v8.6 run_signal.py writes 'proposed'; run_execution.py reads 'approve'/'reject'.
ALTER TABLE decisions DROP CONSTRAINT IF EXISTS decisions_decision_check;
ALTER TABLE decisions ADD CONSTRAINT decisions_decision_check
    CHECK (decision IN ('proposed', 'approve', 'reject'));

-- Idempotency log for cron jobs.
-- Written at the END of a successful run so partial runs retry.
CREATE TABLE IF NOT EXISTS cron_runs (
    run_date     TEXT        NOT NULL,
    job_name     TEXT        NOT NULL,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_date, job_name)
);

-- Live attribution rows written by run_execution.py after each cycle.
-- The historical parquet (data/processed/attribution.parquet) is a
-- read-only artifact; this table captures live paper fill data from v8.6 on.
CREATE TABLE IF NOT EXISTS live_attribution (
    run_date      TEXT    NOT NULL,
    ticker        TEXT    NOT NULL,
    asset_class   TEXT,
    weight        NUMERIC,
    pnl           NUMERIC,
    carry         NUMERIC,
    price_change  NUMERIC,
    gross_pnl     NUMERIC,
    net_pnl       NUMERIC,
    turnover_cost NUMERIC,
    borrow_cost   NUMERIC,
    PRIMARY KEY (run_date, ticker)
);

-- Ensure settings table exists (may have been created in v8.5).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Ensure pnl_log table exists with the columns run_execution.py writes.
CREATE TABLE IF NOT EXISTS pnl_log (
    trade_date    TEXT PRIMARY KEY,
    gross_pnl     NUMERIC,
    net_pnl       NUMERIC,
    turnover_cost NUMERIC,
    borrow_cost   NUMERIC,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure positions table exists.
CREATE TABLE IF NOT EXISTS positions (
    trade_date      TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    signed_notional NUMERIC,
    weight          NUMERIC,
    side            TEXT,
    PRIMARY KEY (trade_date, ticker)
);
