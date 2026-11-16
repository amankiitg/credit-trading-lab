-- Sprint v9.10 -- daily_sleeve_pnl.distribution_pnl: the pay-date half of the
-- short-distribution add-back.
--
-- Why: the bridge and the reconciliation both carried the short leg's distribution as a
-- permanent add-back. Measured on the 2026-11-05 pay date, the broker booked four CDIV
-- debits -- IEF -91.26, TLT -113.25, LQD -100.39, HYG -131.25, -436.15 total -- against
-- a -413.50 residual on the same session. Paper does debit a short its distributions; it
-- debits them on the PAY date, about three sessions after the ex-date the sleeve row
-- accrues the carry on.
--
-- So the item has two legs and the table only stored one. carry_pnl holds the ex-date
-- accrual (negative for a short). This column holds what the broker actually took out on
-- the session:
--
--     add_back(session) = -carry_short(session) + distribution_pnl(session)
--
-- On an ex-date distribution_pnl is 0 and the accrual is added back, because paper has
-- not paid it yet. On a pay date carry_short is 0 and this column is the debit, so the
-- same money is reversed and the pay date stops inheriting the accrual as residual. Over
-- a period containing both dates the bridge's add-back term is zero.
--
--   distribution_pnl -- cash the broker moved for distributions on this session, split
--                       by the sleeve of the symbol that paid it. Negative when the book
--                       was short and owed. NULL, not 0, when the broker's activity read
--                       failed: a zero there would be an assertion that the session had
--                       no distributions, which is the assumption this change removes.
--
-- Written by:
--   scripts/run_signal.py step 8 (risk/sleeve_pnl.py), when the column exists.
--
-- Consumers degrade gracefully without it (dashboard.supabase_client has a probe, and
-- the bridge keeps the accrual-only behaviour it had before), but the pay date is only
-- explicable once this column is present and populated.
--
-- Applied with:
--   npx supabase db query --linked --file sprints/v9.10/supabase_schema.sql

alter table public.daily_sleeve_pnl
    add column if not exists distribution_pnl double precision;

comment on column public.daily_sleeve_pnl.distribution_pnl is
    'Cash the broker moved for distributions on this session, split by sleeve. Negative when the book was short and owed. The pay-date leg of the short-distribution add-back; carry_pnl holds the ex-date leg. NULL when the broker read failed, which is not the same as a session with no distribution.';
