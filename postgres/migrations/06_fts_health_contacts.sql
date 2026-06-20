-- Migration 06: Full-text search, health tables, contact fields, new intent rules
-- Apply with: docker exec -i openclaw-postgres psql -U openclaw -d openclaw < migrations/06_fts_health_contacts.sql

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Full-text search: generated tsvector columns ──────────────────────────────
-- personal.note
ALTER TABLE personal.note
    ADD COLUMN IF NOT EXISTS body_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_note_body_tsv   ON personal.note USING gin(body_tsv);
CREATE INDEX IF NOT EXISTS idx_note_body_trgm  ON personal.note USING gin(body gin_trgm_ops);

-- personal.event
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS title_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(title, '') || ' ' || coalesce(notes, ''))
        ) STORED;

CREATE INDEX IF NOT EXISTS idx_event_title_tsv ON personal.event USING gin(title_tsv);

-- personal.person  (name + relationship + notes for FTS; plus trigram on name for fuzzy)
ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS person_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(name, '') || ' ' ||
                coalesce(relationship, '') || ' ' ||
                coalesce(notes, ''))
        ) STORED;

CREATE INDEX IF NOT EXISTS idx_person_person_tsv  ON personal.person USING gin(person_tsv);
CREATE INDEX IF NOT EXISTS idx_person_name_trgm   ON personal.person USING gin(name gin_trgm_ops);

-- property_deals.property
ALTER TABLE property_deals.property
    ADD COLUMN IF NOT EXISTS prop_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(address, '') || ' ' ||
                coalesce(suburb, '') || ' ' ||
                coalesce(notes, ''))
        ) STORED;

CREATE INDEX IF NOT EXISTS idx_property_prop_tsv ON property_deals.property USING gin(prop_tsv);

-- decision_architect.theme
ALTER TABLE decision_architect.theme
    ADD COLUMN IF NOT EXISTS theme_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, ''))
        ) STORED;

CREATE INDEX IF NOT EXISTS idx_theme_theme_tsv ON decision_architect.theme USING gin(theme_tsv);

-- ── Contact fields on personal.person ────────────────────────────────────────
ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS phone        TEXT,
    ADD COLUMN IF NOT EXISTS email        TEXT,
    ADD COLUMN IF NOT EXISTS address      TEXT,
    ADD COLUMN IF NOT EXISTS organisation TEXT;

-- ── Medication table ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS personal.medication (
    id          BIGSERIAL   PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    person_id   BIGINT      REFERENCES personal.person(id),
    name        TEXT        NOT NULL,
    dose        TEXT,
    frequency   TEXT,                           -- daily | twice daily | as needed | weekly
    prescriber  TEXT,
    started_at  DATE,
    ended_at    DATE,
    active      BOOLEAN     NOT NULL DEFAULT true,
    notes       TEXT,
    med_tsv     TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('english',
                        coalesce(name, '') || ' ' ||
                        coalesce(prescriber, '') || ' ' ||
                        coalesce(notes, ''))
                ) STORED
);

CREATE INDEX IF NOT EXISTS idx_medication_person  ON personal.medication (person_id);
CREATE INDEX IF NOT EXISTS idx_medication_active  ON personal.medication (active) WHERE active;
CREATE INDEX IF NOT EXISTS idx_medication_med_tsv ON personal.medication USING gin(med_tsv);

CREATE OR REPLACE FUNCTION personal.set_medication_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_medication_updated BEFORE UPDATE ON personal.medication
    FOR EACH ROW EXECUTE FUNCTION personal.set_medication_updated_at();

-- Grants
GRANT SELECT, INSERT, UPDATE ON personal.medication TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.medication_id_seq TO openclaw_curator_role;
GRANT SELECT, INSERT, UPDATE ON personal.medication TO openclaw_n8n_role;
GRANT USAGE ON SEQUENCE personal.medication_id_seq TO openclaw_n8n_role;

-- ── New intent rules ──────────────────────────────────────────────────────────

INSERT INTO config.intent_rule (graph, name, label, pattern, priority, weights) VALUES

-- Health: appointments, medication, providers
('personal_graph', 'health_intent',
 'Health / medical query',
 'doctor|gp|specialist|appointment|medical|health|medication|prescription|dose|pharmacy|hospital|clinic|physio|physiotherapist|occupational therapist|ot\b|psychologist|psych|dentist|optometrist|allied health|script|repeat script|blood test|pathology|referral|next appointment|last appointment|upcoming appointment',
 10,
 '{"health_event":5,"medication":5,"event":4,"note":3,"file":2,"financial_doc":1}'
),

-- People: contacts, phone numbers, schedule
('personal_graph', 'people_intent',
 'People / contacts query',
 'who is|contact|phone|mobile|number|email address|address|reach|call|text|next week|schedule|calendar|upcoming|meeting with|catch up|appointment with|when do i see|when am i seeing|when is .+ coming|who manages|who runs|who handles|support worker|coordinator|provider',
 9,
 '{"contact":5,"event":4,"note":3,"file":2,"health_event":2,"financial_doc":1}'
),

-- Next week / upcoming schedule
('personal_graph', 'schedule_intent',
 'Upcoming schedule / week ahead',
 'next week|this week|tomorrow|upcoming|schedule|what''s on|what is on|what have i got|what do i have|agenda|week ahead|coming up',
 8,
 '{"event":5,"health_event":4,"contact":3,"note":2,"file":1}'
)

ON CONFLICT (graph, name) DO UPDATE
    SET label = EXCLUDED.label,
        pattern = EXCLUDED.pattern,
        priority = EXCLUDED.priority,
        weights = EXCLUDED.weights,
        updated_at = now();

-- Update default weights to include new source types
UPDATE config.intent_rule
SET weights = weights ||
    '{"health_event":3,"medication":3,"contact":2}'::jsonb,
    updated_at = now()
WHERE name = '__default__' AND graph = 'personal_graph';
