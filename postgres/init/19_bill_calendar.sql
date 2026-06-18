-- Bill calendar event tracking
ALTER TABLE personal.note ADD COLUMN IF NOT EXISTS bill_event_id TEXT;
ALTER TABLE personal.note ADD COLUMN IF NOT EXISTS bill_event_enriched BOOLEAN DEFAULT FALSE;
