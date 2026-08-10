-- _sync_calendar_hints_to_availability() (maintenance.py) inserts into
-- personal.asset_availability with `ON CONFLICT DO NOTHING`, but the table
-- has no unique constraint beyond its primary key — every INSERT is a fresh
-- row regardless of whether an identical one already exists. Confirmed live:
-- running the (unthrottled, every-5-minutes) detect_provider_gaps task twice
-- against the same still-open holiday event created two duplicate rows, not
-- one deduplicated one. Since a real holiday spans many days and this task
-- runs every 5 minutes for its whole duration, this would have accumulated
-- unbounded duplicates the same way the link/dedup throttle gaps did.
--
-- Mirrors calendar_availability_hint's existing COALESCE(...,-1) pattern for
-- treating NULL person_id/asset_id as a comparable value in the unique key.
CREATE UNIQUE INDEX idx_asset_availability_dedup ON personal.asset_availability
    (COALESCE(person_id, -1), COALESCE(asset_id, -1), start_date, end_date, source);
