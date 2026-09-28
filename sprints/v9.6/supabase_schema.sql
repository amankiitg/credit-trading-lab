-- Sprint v9.6 -- the positions snapshot must carry share counts.
--
-- Applied to the live project on 2026-09-28.
--
-- Why: the drift check compared dollar values (Alpaca market_value) against the
-- cached snapshot. A price move changes market_value with no trade at all, so on any
-- ordinary day the two disagreed by more than DELTA_MIN_NOTIONAL and the drift alert
-- fired for nothing. Share counts only change when something actually trades, so the
-- check needs them in the snapshot and the snapshot did not have them.
--
-- Nullable on purpose: rows written before this column existed have no share count,
-- and the drift check reports those as uncomparable rather than as a flat position,
-- so the first run after this migration cannot raise a false alert.

alter table positions
    add column if not exists shares numeric;
