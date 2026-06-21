-- Add effective_date to personal.event
-- starts_at is TIMESTAMPTZ (UTC), which shifts all-day events by timezone.
-- effective_date is the actual calendar date in Brisbane time (UTC+10),
-- timezone-free, so date-range queries work correctly regardless of DST.

ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS effective_date DATE;

-- Backfill existing rows from starts_at in Brisbane timezone
UPDATE personal.event
SET effective_date = (starts_at AT TIME ZONE 'Australia/Brisbane')::date
WHERE effective_date IS NULL;

-- Index for date-range queries (upcoming events, reset scripts, etc.)
CREATE INDEX IF NOT EXISTS idx_personal_event_effective_date
    ON personal.event (effective_date);

COMMENT ON COLUMN personal.event.effective_date IS
    'Calendar date in Brisbane local time (UTC+10). Use this for date filtering instead of starts_at.';
