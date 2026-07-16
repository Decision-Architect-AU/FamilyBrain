-- Invoice line items — per-service-line extraction from provider invoices
-- (e.g. Centre of Movement: one line for OT, one for physio). Service and
-- practitioner are resolved per line item, never per organisation, so one
-- provider org can correctly feed two different routines.

SET search_path = personal, public;

CREATE TABLE IF NOT EXISTS personal.invoice_line_item (
    id                  BIGSERIAL PRIMARY KEY,
    note_id             BIGINT REFERENCES personal.note(id),
    service_type        TEXT NOT NULL,           -- e.g. 'OT', 'physio'
    practitioner_name   TEXT,                    -- as extracted, before resolution
    practitioner_person_id BIGINT REFERENCES personal.person(id),
    subject_person_id   BIGINT REFERENCES personal.person(id),  -- who received the service
    org_slug            TEXT,
    line_date           DATE,
    amount              NUMERIC(10,2),
    match_action        TEXT,                    -- 'linked' | 'created' | 'queued'
    match_score         NUMERIC(4,3),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_subject_service
    ON personal.invoice_line_item (subject_person_id, service_type, line_date DESC)
    WHERE subject_person_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_invoice_line_practitioner
    ON personal.invoice_line_item (practitioner_person_id) WHERE practitioner_person_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON personal.invoice_line_item TO curator;
GRANT USAGE, SELECT ON SEQUENCE personal.invoice_line_item_id_seq TO curator;

-- Practitioner resolution review queue — low-confidence fuzzy matches or
-- unresolved practitioner names land here instead of silently forking a
-- new person node or merging into the wrong one.
CREATE TABLE IF NOT EXISTS personal.practitioner_review_queue (
    id                      BIGSERIAL PRIMARY KEY,
    extracted_name          TEXT NOT NULL,
    org_slug                TEXT,
    service_type            TEXT,
    suggested_person_id     BIGINT REFERENCES personal.person(id),
    suggested_person_name   TEXT,
    match_score             NUMERIC(4,3),
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected')),
    resolved_person_id      BIGINT REFERENCES personal.person(id),
    resolved_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_practitioner_review_status ON personal.practitioner_review_queue (status);

GRANT SELECT, INSERT, UPDATE ON personal.practitioner_review_queue TO curator, dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.practitioner_review_queue_id_seq TO curator, dashboard_ro;
