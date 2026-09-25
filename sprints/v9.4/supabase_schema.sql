-- Sprint v9.4 -- stop_states.updated_at must refresh on upsert.
--
-- Applied to the live project on 2026-09-25.
--
-- Why: the column is declared `timestamptz default now()`, and a default only
-- applies on INSERT. run_signal upserts one row per ticker, so re-runs rewrote
-- the row while leaving updated_at at the time of the first ever insert. The
-- stop_states rows still read 2026-08-12 even though later runs had written them,
-- which made updated_at useless for telling how fresh the states were.
--
-- The client now sends updated_at explicitly as well (so the behaviour is visible
-- in code and covered by a test). This trigger is the durable guarantee, and it
-- also covers any future writer that forgets.

create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists stop_states_set_updated_at on public.stop_states;
create trigger stop_states_set_updated_at
    before update on public.stop_states
    for each row execute function public.set_updated_at();
