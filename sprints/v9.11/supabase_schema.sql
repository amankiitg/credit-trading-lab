-- Sprint v9.11 -- share_history: the book's end-of-day share count, per session and ticker.
--
-- Why: the signal-history lower panel drew its share line from public.positions, which
-- only started carrying a shares column on 2026-09-28. The line was therefore blank from
-- the first stored snapshot (2026-06-18) until October, while the held-weight line above
-- it was continuous -- the panel said "no position" for a month and a half in which the
-- book was trading.
--
-- The positions table is a snapshot: one row per ticker per run, written after the trade
-- and by the evening refresh. A daily series of share counts is a different object, and
-- it is rebuilt from the broker's own ledger (656 FILL activities, 2026-06-17 ->
-- 2026-11-16). That rebuild reproduces every stored snapshot exactly: 288 overlapping
-- cells across 36 sessions, maximum absolute difference 0.000000.
--
--   session_date -- the trading session this count is the close of.
--   ticker       -- universe symbol.
--   shares       -- signed shares at that session's close (negative = short).
--                   Fractional shares are real; the paper account holds them.
--   source       -- where the row came from; the fill rebuild writes 'alpaca_fills'.
--
-- A session with no row is UNKNOWN, not zero. The chart draws a gap and says so; a zero
-- would assert that the account held nothing, which nothing in the data supports.
--
-- Written by:
--   scripts/backfill_share_history.py   -- one-off rebuild from the fill ledger
--
-- Applied with:
--   npx supabase db query --linked --file sprints/v9.11/supabase_schema.sql

create table if not exists public.share_history (
    session_date date not null,
    ticker       text not null,
    shares       numeric,
    source       text not null,
    primary key (session_date, ticker)
);

comment on table public.share_history is
    'End-of-day signed share count per ticker per session, rebuilt from Alpaca FILL activities. A missing session is unknown, not zero.';

comment on column public.share_history.shares is
    'Signed shares held at the session close; negative for a short. Fractional shares are real.';

comment on column public.share_history.source is
    'Origin of the row; the fill rebuild writes alpaca_fills.';

-- Same posture as every other table in this schema: RLS on, the app's service key
-- bypasses it, and the read-only analyst role gets an explicit select grant and policy.
alter table public.share_history enable row level security;

grant select on table public.share_history to agent_readonly;

drop policy if exists agent_ro_read on public.share_history;
create policy agent_ro_read
    on public.share_history
    for select
    to agent_readonly
    using (true);
