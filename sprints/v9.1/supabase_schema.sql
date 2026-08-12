-- Sprint v9.1: Supabase schema additions
-- Run in Supabase SQL Editor.

-- Live stop-ladder state cache (advisory mode -- for display only, never traded).
-- One row per ticker, upserted each signal run. History lives in cron logs.
CREATE TABLE IF NOT EXISTS stop_states (
    ticker              TEXT PRIMARY KEY,
    state               TEXT NOT NULL DEFAULT 'NORMAL',   -- NORMAL / REDUCED / STOPPED
    episode_entry_date  TEXT,                             -- first date of current episode
    episode_entry_price NUMERIC,
    side                INT,                              -- +1 long, -1 short
    peak_r              NUMERIC,                          -- running favorable peak (return units)
    z                   NUMERIC,                          -- current drawdown z-score
    multiplier          NUMERIC DEFAULT 1.0,              -- current multiplier (always 1.0 in advisory)
    advisory            BOOLEAN DEFAULT TRUE,             -- TRUE = advisory mode (not traded)
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Add advisory column to decisions if not present
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS advisory BOOLEAN DEFAULT FALSE;
