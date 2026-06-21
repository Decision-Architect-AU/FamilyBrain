-- Add document_date to personal.note.
-- A note is self-contained: document_date is the Brisbane-local date the source
-- document was received or created. It is set at ingest time and never depends
-- on tracing back through source links.
-- source_email_id / file_path remain as audit trail only.

ALTER TABLE personal.note
    ADD COLUMN IF NOT EXISTS document_date DATE;

-- Backfill from linked email_message where available
UPDATE personal.note n
SET document_date = (em.received_at AT TIME ZONE 'Australia/Brisbane')::date
FROM personal.email_message em
WHERE em.id = n.source_email_id
  AND n.document_date IS NULL;

CREATE INDEX IF NOT EXISTS idx_note_document_date
    ON personal.note (document_date)
    WHERE document_date IS NOT NULL;
