-- Add category fields to email_message
-- Run this as a migration on existing databases
-- (New installs get it via 15_schema_email.sql order — this file runs after)

ALTER TABLE personal.email_message
    ADD COLUMN IF NOT EXISTS category            TEXT,
    ADD COLUMN IF NOT EXISTS category_confidence NUMERIC(4,3),  -- 0.000–1.000
    ADD COLUMN IF NOT EXISTS categorised_at      TIMESTAMPTZ;

-- category values:
--   ndis        NDIS invoices, service agreements, support worker comms
--   health      Medical appointments, referrals, prescriptions, test results
--   finance     Bank statements, bills, tax, BAS, accounting
--   property    Ownership statements, rates, maintenance, agent comms, lease
--   insurance   Policy documents, renewals, claims, certificates of currency
--   travel      Flight/hotel/car rental confirmations, itineraries
--   vehicle     Rego, CTP, roadside, dealer
--   school      School newsletters, activity notices, permission slips
--   legal       Contracts, legal notices, conveyancing, ASIC
--   personal    Personal correspondence, family, friends

CREATE INDEX IF NOT EXISTS idx_email_message_category
    ON personal.email_message (category)
    WHERE category IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_email_message_uncategorised
    ON personal.email_message (id)
    WHERE ingest_status = 'ingested' AND category IS NULL;

-- Grant curator access (email-sync + ingestor run as curator role)
GRANT UPDATE (category, category_confidence, categorised_at)
    ON personal.email_message TO openclaw_curator_role;
