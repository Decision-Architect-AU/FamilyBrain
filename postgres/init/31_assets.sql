-- Asset table — master record for anything that generates events over its lifetime.
-- vehicle | medication | property | subscription | person | device | pet
-- Rules (jsonb array) define what events each asset should generate and when.

SET search_path = personal, public;

CREATE TABLE IF NOT EXISTS personal.asset (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    name                TEXT NOT NULL,
    asset_type          TEXT NOT NULL
                        CHECK (asset_type IN (
                            'vehicle', 'medication', 'property',
                            'subscription', 'person', 'device', 'pet'
                        )),
    subtype             TEXT,
    -- vehicle: car | motorcycle | trailer
    -- medication: prescription | OTC | supplement
    -- property: PPR | investment | commercial

    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive', 'disposed', 'sold')),

    person_id           BIGINT REFERENCES personal.person(id),
    -- NULL = household asset (car, property)
    -- set  = individual asset (medication, passport)

    acquired_date       DATE,
    next_event_date     DATE,       -- nearest upcoming generated event (maintained by rule watcher)
    last_event_date     DATE,       -- most recent completed event

    event_gen_enabled   BOOLEAN NOT NULL DEFAULT true,
    -- false = pause rule generation without deleting asset (e.g. car in storage)

    -- Type-specific fields — validated at write time against ASSET_FACT_SCHEMAS in asset_writer.py
    facts               JSONB NOT NULL DEFAULT '{}',

    -- Rules defining what events this asset generates
    -- Schema: see default_rules_for_type() in ingestor/src/asset_writer.py
    rules               JSONB NOT NULL DEFAULT '[]',

    -- Pointer back to source relational table: schema.table:id
    ref                 TEXT,

    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_asset_type_status  ON personal.asset (asset_type, status);
CREATE INDEX IF NOT EXISTS idx_asset_next_event   ON personal.asset (next_event_date);
CREATE INDEX IF NOT EXISTS idx_asset_person       ON personal.asset (person_id);

-- ── Link events to assets ─────────────────────────────────────────────────────
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS asset_id           BIGINT REFERENCES personal.asset(id),
    ADD COLUMN IF NOT EXISTS generated_by_rule  TEXT;

CREATE INDEX IF NOT EXISTS idx_event_asset ON personal.event (asset_id)
    WHERE asset_id IS NOT NULL;

-- ── Grants ────────────────────────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE ON personal.asset TO dashboard_ro;
GRANT SELECT, INSERT, UPDATE ON personal.asset TO curator;
GRANT USAGE, SELECT ON SEQUENCE personal.asset_id_seq TO curator;
