-- Ownership entity registry: maps folder slugs to legal entity names
CREATE TABLE IF NOT EXISTS personal.ownership_entity (
    id          serial PRIMARY KEY,
    folder_slug text NOT NULL UNIQUE,  -- Trust1, Trust2, SMSF, NDIS, Personal
    full_name   text NOT NULL,
    keywords    text[] NOT NULL DEFAULT '{}',
    notes       text
);

INSERT INTO personal.ownership_entity (folder_slug, full_name, keywords) VALUES
('Trust1', 'West Property Inv No1 PTY LTD atf West Property Inv No1 Disc Trust',
 ARRAY['west property inv no1','no1 disc trust','no 1 disc trust','inv no1']),
('Trust2', 'West Property Inv No2 PTY LTD atf West Property Inv No2 Disc Trust',
 ARRAY['west property inv no2','no2 disc trust','no 2 disc trust','inv no2']),
('Trust3', 'West Property Inv No3 PTY LTD atf West Property Inv No3 Disc Trust',
 ARRAY['west property inv no3','no3 disc trust','no 3 disc trust','inv no3']),
('Trust4', 'West Property Inv No4 PTY LTD atf West Property Inv No4 Disc Trust',
 ARRAY['west property inv no4','no4 disc trust','no 4 disc trust','inv no4']),
('SMSF',  'West Property Investment SMSF / West Property Investment Property Bare Trust',
 ARRAY['west property investment smsf','bare trust','smsf','superannuation']),
('NDIS',  'NDIS / Olivia West',
 ARRAY['ndis','olivia west','olivia','participant','support worker','ndis plan']),
('Personal', 'Glenn West Personal', ARRAY[]::text[])
ON CONFLICT (folder_slug) DO NOTHING;

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
