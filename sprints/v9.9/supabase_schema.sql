-- Sprint v9.9 -- close_equity: the published end-of-day account equity, per session.
--
-- Why: the dashboard's account-value chart reconstructed each evening's NAV from the
-- next morning's execution row:
--
--     evening_nav(t) = live_nav(t+1) - book_pnl(t+1)
--
-- That mixes two clocks. live_nav is the equity the execution job read at 10:32 the
-- next morning, while book_pnl is close-to-close, so every plotted point carried the
-- next day's 10:32-to-close move -- a few dollars on a quiet day, hundreds on a trend
-- day. The close itself is published by the broker's portfolio history, and the
-- evening reconciliation already reads it with the same fetch and the same
-- next-calendar-day stamp rule.
--
-- This table stores that read once, so the dashboard can draw the close without a
-- broker credential of its own and without a second ingest path.
--
--   session_date -- the session the close belongs to. The endpoint labels each close
--                   with the FOLLOWING calendar day; the writer converts the label
--                   back to the session (risk.execution_pnl.published_closes) before
--                   storing it, so this column is a real trading session.
--   equity       -- account equity at that session's close, in USD.
--   source       -- where the number came from. The nightly job and the one-off
--                   backfill both write 'alpaca_portfolio_history'.
--   fetched_at   -- when the row was last written, not when the close happened. A
--                   corrected or re-read close replaces the row and moves this.
--
-- Written by:
--   scripts/run_signal.py step 8b        -- every evening, D-1 and earlier
--   scripts/backfill_close_equity.py     -- 2026-10-01..latest, one-off
--
-- Applied with:
--   npx supabase db query --linked --file sprints/v9.9/supabase_schema.sql

create table if not exists public.close_equity (
    session_date date primary key,
    equity       numeric not null,
    source       text not null,
    fetched_at   timestamp with time zone not null default now()
);

comment on table public.close_equity is
    'Account equity at each session close, read from the Alpaca portfolio history. One row per session, written by the evening signal job.';

comment on column public.close_equity.session_date is
    'Trading session the close belongs to. The portfolio history labels a close with the following calendar day; this column is the session before that label.';

-- The dashboard reads the whole series, newest first.
create index if not exists close_equity_session_date_idx
    on public.close_equity (session_date desc);

-- Same posture as every other table in this schema: RLS on. The app's client uses the
-- service-role key, which bypasses RLS, so the dashboard needs nothing extra here.
alter table public.close_equity enable row level security;

-- The read-only analyst role is the exception: it gets an explicit select grant and a
-- policy, matching the agent_ro_read policy on every other table in this schema.
grant select on table public.close_equity to agent_readonly;

drop policy if exists agent_ro_read on public.close_equity;
create policy agent_ro_read
    on public.close_equity
    for select
    to agent_readonly
    using (true);
