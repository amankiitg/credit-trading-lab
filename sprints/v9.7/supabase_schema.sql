-- Sprint v9.7 -- pnl_log carries the account equity and the book's P&L.
--
-- Why: Panel K used to plot `nav - cumulative(net_pnl)`, and net_pnl is minus the
-- day's turnover cost, so the "NAV" line was reconstructed from costs alone. Across
-- the whole recorded history that series spans about $106 on a $102k account, so it
-- read as flat, and because costs only ever subtract it sloped the wrong way while
-- the real account rose. run_execution already computes both numbers every run for
-- the summary email; these columns are where they are stored so the dashboard can
-- plot the account's actual equity instead of a cost-derived line.
--
--   live_nav  -- Alpaca account equity read at that run (alpaca_paper.get_live_nav)
--   book_pnl  -- live_nav minus the live_nav recorded by the previous run, i.e. the
--                book's result for the day. NULL on the first run, when there is no
--                previous reading to compare against. NULL is deliberate: a missing
--                comparison is not a flat day, and 0.0 would read as one.
--
-- Both are nullable and no default is set, so the rows written before this migration
-- stay NULL and are skipped by the chart rather than plotted as zero.
--
-- Applied with:
--   npx supabase db query --linked --file sprints/v9.7/supabase_schema.sql

alter table pnl_log
    add column if not exists live_nav double precision,
    add column if not exists book_pnl double precision;
