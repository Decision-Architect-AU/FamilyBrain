-- Ownership entity registry: maps folder slugs to legal entity names
CREATE TABLE IF NOT EXISTS personal.ownership_entity (
    id          serial PRIMARY KEY,
    folder_slug text NOT NULL UNIQUE,  -- Trust1, Trust2, SMSF, NDIS, Personal
    full_name   text NOT NULL,
    keywords    text[] NOT NULL DEFAULT '{}',
    notes       text
);

-- Populate this table with your own entity names — do NOT commit real names to git.
-- Example (run manually or via a private seed script outside this repo):
--
-- INSERT INTO personal.ownership_entity (folder_slug, full_name, keywords) VALUES
-- ('Trust1', 'Your Trust 1 PTY LTD atf Your Trust 1 Disc Trust',
--  ARRAY['your trust 1','no1 disc trust','no 1 disc trust']),
-- ('SMSF',   'Your SMSF Name',
--  ARRAY['your smsf','bare trust','smsf','superannuation']),
-- ('NDIS',   'NDIS Participant',
--  ARRAY['ndis','participant','support worker','ndis plan']),
-- ('Personal', 'Personal', ARRAY[]::text[])
-- ON CONFLICT (folder_slug) DO NOTHING;

-- Property address → ownership entity mapping.
-- Populate with known property addresses so filing is automatic.
-- address_pattern is a case-insensitive substring match against email subject/body.
CREATE TABLE IF NOT EXISTS personal.ownership_property (
    id              serial PRIMARY KEY,
    address_pattern text NOT NULL,        -- e.g. 'drews road', 'anchusa', 'macarthur'
    entity_slug     text NOT NULL REFERENCES personal.ownership_entity(folder_slug),
    notes           text
);

-- financial_processed flag on email_message
ALTER TABLE personal.email_message
    ADD COLUMN IF NOT EXISTS financial_processed boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_email_message_financial
    ON personal.email_message (account_id, financial_processed)
    WHERE ingest_status = 'ingested' AND financial_processed = false;
