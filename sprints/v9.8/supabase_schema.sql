-- Sprint v9.8 -- daily_sleeve_pnl: the book's P&L by sleeve, carry vs price.
--
-- Why: the daily email reports the book's result as the move in live account equity,
-- which is the right headline and says nothing about where the money came from. This
-- table is the decomposition of one session's move into the four sleeves and, inside
-- each sleeve, into the distributions the book received (carry) and the price move on
-- the book it held (price).
--
-- Written by the signal cron (risk/sleeve_pnl.py), which is the only job that runs
-- after the US close and so the only one that can see the session's close.
--
--   trade_date -- the session the P&L belongs to, measured on the book held at the
--                 PREVIOUS close. Not necessarily the run date: when the close cache
--                 lags a session, the row is written for the session the closes
--                 describe, which is one behind the run.
--   sleeve     -- equity | rates | credit | commodity
--   price_pnl  -- shares(t-1) * (unadjusted close(t) - unadjusted close(t-1))
--   carry_pnl  -- shares(t-1) * per-share distribution on t
--   total      -- price_pnl + carry_pnl, that sleeve's contribution to the session
--   book_total -- the session's whole-book gross P&L, repeated on all four rows so the
--                 reconciliation is checkable from the table alone:
--                 sum(total) over one trade_date must equal book_total.
--
-- Rows are written only when the gate in risk/sleeve_pnl.py passes. A session that
-- does not reconcile writes NOTHING, so a missing session means refused, not zero.
--
-- Prices are the unadjusted close on purpose. Adj_close is back-adjusted, so a
-- difference between two adjusted closes already contains the distributions paid in
-- between; adding carry to that would count the same distribution twice.
--
-- Applied with:
--   npx supabase db query --linked --file sprints/v9.8/supabase_schema.sql

create table if not exists daily_sleeve_pnl (
    trade_date text not null,
    sleeve     text not null,
    price_pnl  double precision,
    carry_pnl  double precision,
    total      double precision,
    book_total double precision,
    created_at timestamp with time zone not null default now(),
    primary key (trade_date, sleeve)
);

-- The dashboard reads the whole history, newest first.
create index if not exists daily_sleeve_pnl_trade_date_idx
    on daily_sleeve_pnl (trade_date desc);

-- Service-role key only, matching every other table in this schema: the dashboard is
-- publicly deployed and the anon key is not a reader of this data.
alter table daily_sleeve_pnl enable row level security;
