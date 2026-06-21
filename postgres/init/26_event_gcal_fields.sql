-- Appointment updater fields on personal.event
-- Decouples all Google Calendar writes from sync sources.
-- appointment_updater.py is the single place that touches GCal.

ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS gcal_event_id       TEXT,           -- event ID in target GCal
    ADD COLUMN IF NOT EXISTS gcal_calendar_id    TEXT,           -- which GCal calendar it lives in
    ADD COLUMN IF NOT EXISTS calendar_written_at TIMESTAMPTZ,    -- when last pushed to GCal
    ADD COLUMN IF NOT EXISTS next_update_at      TIMESTAMPTZ;    -- scheduled re-evaluation (NULL = skip)

CREATE INDEX IF NOT EXISTS idx_event_gcal_id
    ON personal.event (gcal_event_id) WHERE gcal_event_id IS NOT NULL;

-- appointment_updater polls this index
CREATE INDEX IF NOT EXISTS idx_event_next_update
    ON personal.event (next_update_at) WHERE next_update_at IS NOT NULL;

COMMENT ON COLUMN personal.event.gcal_event_id IS
    'Event ID in the target Google Calendar (Bills/Family/Holidays/primary). Managed by appointment_updater.';
COMMENT ON COLUMN personal.event.next_update_at IS
    'When appointment_updater should next process this event. NULL = only process on change.';
