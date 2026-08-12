-- Force-review flags: pick a specific personal.event row and trigger immediate
-- re-verification instead of waiting for the nightly maintenance sweep. Grew out
-- of a live incident where two calendar events were thin Google-auto-detected
-- placeholders because their real confirmation emails were silently dropped by
-- Gmail category filtering / a bulk-header heuristic. See email-sync/src/item_review.py.

CREATE TABLE IF NOT EXISTS personal.item_flag (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    entity_type       TEXT NOT NULL CHECK (entity_type IN ('event', 'note', 'asset')),
    entity_id         BIGINT NOT NULL,
    reason            TEXT,
    source            TEXT NOT NULL DEFAULT 'dashboard' CHECK (source IN ('dashboard', 'whatsapp')),
    requested_by      TEXT,

    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'reviewing', 'needs_user_input', 'resolved', 'failed')),

    found_email_id    BIGINT REFERENCES personal.email_message(id),
    new_event_id      BIGINT REFERENCES personal.event(id),
    resolution_notes  TEXT,
    resolved_at       TIMESTAMPTZ
);

-- One active flag per item at a time; re-flagging after resolve/fail is fine.
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_flag_active
    ON personal.item_flag (entity_type, entity_id)
    WHERE status IN ('pending', 'reviewing', 'needs_user_input');

CREATE INDEX IF NOT EXISTS idx_item_flag_status  ON personal.item_flag (status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_item_flag_entity  ON personal.item_flag (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_item_flag_created ON personal.item_flag (created_at DESC);

-- personal.event has no email-linkage column, unlike note/asset (both already
-- have source_email_id) — lets the recovery pipeline supersede by exact new-event-id
-- instead of fuzzy title/date matching.
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS source_email_id BIGINT REFERENCES personal.email_message(id);
CREATE INDEX IF NOT EXISTS idx_event_source_email ON personal.event (source_email_id)
    WHERE source_email_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON personal.item_flag TO openclaw_curator_role, openclaw_n8n_role, openclaw_readonly;
GRANT USAGE, SELECT ON SEQUENCE personal.item_flag_id_seq TO openclaw_curator_role, openclaw_n8n_role, openclaw_readonly;

-- Dashboard needs to search events to flag them — no general events page exists yet,
-- and personal.event was never previously exposed to dashboard_ro.
GRANT SELECT ON personal.event TO openclaw_readonly;
