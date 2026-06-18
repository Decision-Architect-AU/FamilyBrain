-- Ingestor uses the curator role (already has write access to all schemas)
-- This script just documents the intent explicitly

-- personal.note — insert new notes from file ingestion
GRANT INSERT ON personal.note TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.note_id_seq TO openclaw_curator_role;

-- property_deals.scraped_listing + scrape_job — file ingestion writes raw docs here
GRANT INSERT ON property_deals.scrape_job TO openclaw_curator_role;
GRANT INSERT ON property_deals.scraped_listing TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE property_deals.scrape_job_id_seq TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE property_deals.scraped_listing_id_seq TO openclaw_curator_role;

-- decision_architect.published_content — file ingestion creates drafts
GRANT INSERT ON decision_architect.published_content TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE decision_architect.published_content_id_seq TO openclaw_curator_role;
