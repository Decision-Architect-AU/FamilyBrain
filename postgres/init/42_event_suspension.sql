-- Collision resolution: suspend, don't delete (family-brain-weekly-digest-spec.md P0-1).
--
-- Previously, task_generate_events() deleted a routine's generated occurrence
-- outright when a colliding event was found on its date (_delete_generated_event
-- in maintenance.py). Deletion is lossy: nothing downstream (the weekly digest,
-- routine_context_pack) can distinguish "this pickup never existed" from "this
-- pickup was skipped because the provider is on holiday" — the second is exactly
-- the kind of exception a digest needs to call out, and a deleted row can't.
--
-- 'suspended' joins the existing status vocabulary as a first-class state,
-- carrying why and what caused it, so the occurrence row survives and stays
-- queryable/reportable instead of disappearing.
ALTER TABLE personal.event
    ADD COLUMN suspended_reason TEXT,
    ADD COLUMN suspended_by_event_id BIGINT REFERENCES personal.event(id);

ALTER TABLE personal.event DROP CONSTRAINT event_status_check;
ALTER TABLE personal.event ADD CONSTRAINT event_status_check
    CHECK (status = ANY (ARRAY[
        'scheduled', 'cancelled', 'rescheduled', 'completed', 'generated',
        'superseded', 'ingested', 'confirmed', 'suspended'
    ]));

CREATE INDEX idx_event_suspended ON personal.event (asset_id, occurrence_date) WHERE status = 'suspended';
