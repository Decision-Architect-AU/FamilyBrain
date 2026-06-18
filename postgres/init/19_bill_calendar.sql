-- Bill calendar event tracking
ALTER TABLE personal.note ADD COLUMN IF NOT EXISTS bill_event_id TEXT;
ALTER TABLE personal.note ADD COLUMN IF NOT EXISTS bill_event_enriched BOOLEAN DEFAULT FALSE;

-- file_path dedup: one note per saved financial document
ALTER TABLE personal.note ADD COLUMN IF NOT EXISTS file_path TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_note_file_path
    ON personal.note (file_path) WHERE file_path IS NOT NULL;
