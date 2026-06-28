-- property_deals schema: property portfolio, deal pipeline, scraping jobs
-- Relational tables for structured data; AGE graph (property_graph) for case studies

SET search_path = property_deals, public;

-- ── Properties ────────────────────────────────────────────────────────────────
CREATE TABLE property_deals.property (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    external_id     TEXT UNIQUE,                        -- scraper source ID
    source_url      TEXT,
    address         TEXT NOT NULL,
    suburb          TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'QLD',
    postcode        TEXT,
    property_type   TEXT,                               -- house | unit | townhouse | commercial | land
    bedrooms        SMALLINT,
    bathrooms       SMALLINT,
    car_spaces      SMALLINT,
    land_size_sqm   NUMERIC(10,2),
    floor_size_sqm  NUMERIC(10,2),
    year_built      SMALLINT,
    listing_price   NUMERIC(12,2),
    listing_type    TEXT,                               -- sale | auction | expression_of_interest
    status          TEXT NOT NULL DEFAULT 'raw',        -- raw | reviewed | active | passed | acquired | sold
    notes           TEXT,
    embedding       vector(768)                         -- nomic-embed-text dim
);

CREATE INDEX idx_property_suburb   ON property_deals.property (suburb);
CREATE INDEX idx_property_status   ON property_deals.property (status);
CREATE INDEX idx_property_embed    ON property_deals.property USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- ── Deal pipeline ─────────────────────────────────────────────────────────────
CREATE TABLE property_deals.deal (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    property_id     BIGINT NOT NULL REFERENCES property_deals.property(id),
    stage           TEXT NOT NULL DEFAULT 'lead',       -- lead | due_diligence | offer | under_contract | settled | dead
    purchase_price  NUMERIC(12,2),
    purchase_date   DATE,
    settlement_date DATE,
    deposit_amount  NUMERIC(12,2),
    deposit_due     DATE,
    finance_amount  NUMERIC(12,2),
    finance_due     DATE,
    rental_yield    NUMERIC(5,4),                       -- decimal e.g. 0.0475
    projected_growth NUMERIC(5,4),
    tags            TEXT[],
    notes           TEXT
);

CREATE INDEX idx_deal_property   ON property_deals.deal (property_id);
CREATE INDEX idx_deal_stage      ON property_deals.deal (stage);
CREATE INDEX idx_deal_settlement ON property_deals.deal (settlement_date);

-- ── Scrape jobs ───────────────────────────────────────────────────────────────
CREATE TABLE property_deals.scrape_job (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    source          TEXT NOT NULL,                      -- domain.com.au | realestate.com.au | custom
    search_params   JSONB NOT NULL DEFAULT '{}',        -- suburb filters, price range, etc.
    status          TEXT NOT NULL DEFAULT 'pending',    -- pending | running | done | failed
    listings_found  INT DEFAULT 0,
    listings_new    INT DEFAULT 0,
    error_message   TEXT
);

-- ── Scraped listings (raw, pre-dedup) ────────────────────────────────────────
CREATE TABLE property_deals.scraped_listing (
    id              BIGSERIAL PRIMARY KEY,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    job_id          BIGINT REFERENCES property_deals.scrape_job(id),
    source          TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    raw_data        JSONB NOT NULL,
    processed       BOOLEAN NOT NULL DEFAULT false,
    property_id     BIGINT REFERENCES property_deals.property(id),  -- set after dedup/merge
    UNIQUE(source, external_id)
);

CREATE INDEX idx_scraped_listing_processed ON property_deals.scraped_listing (processed) WHERE NOT processed;

-- ── Events (calendar-synced) ──────────────────────────────────────────────────
CREATE TABLE property_deals.event (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    calendar_event_id TEXT UNIQUE,                      -- Google/Outlook event ID
    title           TEXT NOT NULL,
    event_type      TEXT,                               -- settlement | inspection | finance_due | meeting | deadline
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ,
    deal_id         BIGINT REFERENCES property_deals.deal(id),
    property_id     BIGINT REFERENCES property_deals.property(id),
    notes           TEXT
);

CREATE INDEX idx_pd_event_starts ON property_deals.event (starts_at);
CREATE INDEX idx_pd_event_type   ON property_deals.event (event_type);

-- ── Grants ────────────────────────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE ON
    property_deals.property,
    property_deals.deal,
    property_deals.scrape_job,
    property_deals.scraped_listing,
    property_deals.event
TO familybrain_scraper_role;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA property_deals TO familybrain_scraper_role;

GRANT SELECT ON
    property_deals.property,
    property_deals.deal,
    property_deals.event
TO familybrain_pr_agent_role, familybrain_curator_role, familybrain_readonly;

GRANT SELECT ON
    property_deals.scrape_job,
    property_deals.scraped_listing
TO familybrain_curator_role, familybrain_readonly;

-- Curator can write to property (for case-study promotion)
GRANT INSERT, UPDATE ON property_deals.property TO familybrain_curator_role;
GRANT USAGE ON SEQUENCE property_deals.property_id_seq TO familybrain_curator_role;

-- n8n needs full access for orchestration
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA property_deals TO familybrain_n8n_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA property_deals TO familybrain_n8n_role;

-- Trigger: keep updated_at current
CREATE OR REPLACE FUNCTION property_deals.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_property_updated BEFORE UPDATE ON property_deals.property
    FOR EACH ROW EXECUTE FUNCTION property_deals.set_updated_at();
CREATE TRIGGER trg_deal_updated BEFORE UPDATE ON property_deals.deal
    FOR EACH ROW EXECUTE FUNCTION property_deals.set_updated_at();
