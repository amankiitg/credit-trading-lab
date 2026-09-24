-- Sprint v9.2 -- execution resilience schema.
--
-- Run in the Supabase SQL editor, or with the CLI:
--   npx supabase db query --linked --project-ref <ref> --agent no \
--     --file sprints/v9.2/supabase_schema.sql
--
-- Applied to the live project on 2026-09-24.

-- 1. Rejection audit trail.
--
-- Every leg that never became a submitted order: guard rejections, the
-- shortability pre-check, and any skip or rejection at submission.
--
-- Append-only on purpose. Alpaca does not persist a submit-time rejection as an
-- order record (verified 2026-09-24: the rejected LQD sell_to_open left no trace
-- in the order history), so this table is the only durable evidence that the leg
-- was ever intended. It must not be overwritten by a later run.
create table if not exists order_rejections (
    id                 bigserial primary key,
    created_at         timestamptz default now() not null,
    run_date           date        not null,
    ticker             text        not null,
    leg                int         not null,
    side               text        not null,
    position_intent    text        not null,
    requested_notional float8      not null,
    status             text        not null,
    reason_code        text        not null,
    detail             text,
    order_id           text,
    backfilled         boolean     not null default false
);

create index if not exists order_rejections_run_date_idx
    on order_rejections (run_date);

-- 2. Backfill provenance.
--
-- A reconstructed row (written after the fact because a run crashed before it
-- could record anything) must be distinguishable from a row written live during
-- a normal run.
alter table pnl_log
    add column if not exists backfilled boolean not null default false,
    add column if not exists backfill_note text;

alter table live_attribution
    add column if not exists backfilled boolean not null default false;

-- Enable row-level security (service-role key only, matching the other tables)
alter table order_rejections enable row level security;
