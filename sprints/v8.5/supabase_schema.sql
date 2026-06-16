-- Run this in the Supabase SQL editor to provision the v8.5 tables.
-- Table names match dashboard/supabase_client.py and dashboard/views/operational.py.
-- Enable row-level security on each table after creation.

create table if not exists decisions (
    id                        bigserial primary key,
    created_at                timestamptz default now() not null,
    signal_date               date        not null,
    decision                  text        not null check (decision in ('APPROVE', 'REJECT')),
    ticker                    text        not null,
    proposed_target_weight    float8      not null,
    proposed_delta_notional   float8      not null,
    approved_by               text        not null
);

create table if not exists positions (
    id                bigserial primary key,
    updated_at        timestamptz default now() not null,
    ticker            text        not null unique,
    signed_notional   float8      not null,
    weight            float8      not null,
    entry_date        date,
    avg_entry_price   float8
);

create table if not exists pnl_log (
    id                bigserial primary key,
    fill_at           timestamptz default now() not null,
    signal_date       date        not null,
    ticker            text        not null,
    side              text        not null,
    position_intent   text        not null,
    filled_notional   float8      not null,
    fill_price        float8      not null,
    simulated_cost    float8      not null,
    gross_pnl         float8,
    net_pnl           float8,
    status            text        not null
);

-- Enable row-level security (run after table creation)
alter table decisions  enable row level security;
alter table positions  enable row level security;
alter table pnl_log    enable row level security;

-- Policy: service-role key only (no anon reads or writes)
-- These are applied automatically to service-role connections;
-- add explicit policies if you also need anon-key access.
