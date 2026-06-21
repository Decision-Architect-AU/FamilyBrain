-- personal schema: family, NDIS, household finance
-- Read/write restricted to curator agent + superuser only
-- Dashboard gets NO access to this schema

SET search_path = personal, public;

-- ── People (family members, contacts, providers) ──────────────────────────────
CREATE TABLE personal.person (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    relationship    TEXT,                               -- self | partner | child | provider | advisor
    date_of_birth   DATE,
    ndis_participant BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT
);

-- ── NDIS plans ────────────────────────────────────────────────────────────────
CREATE TABLE personal.ndis_plan (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    person_id       BIGINT NOT NULL REFERENCES personal.person(id),
    plan_start      DATE NOT NULL,
    plan_end        DATE NOT NULL,
    total_funding   NUMERIC(12,2),
    plan_manager    TEXT,
    support_coordinator TEXT,
    goals           TEXT[],
    notes           TEXT
);

CREATE INDEX idx_ndis_plan_person ON personal.ndis_plan (person_id);
CREATE INDEX idx_ndis_plan_end    ON personal.ndis_plan (plan_end);

-- ── NDIS support items ────────────────────────────────────────────────────────
CREATE TABLE personal.ndis_support (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    plan_id         BIGINT NOT NULL REFERENCES personal.ndis_plan(id),
    category        TEXT NOT NULL,                      -- core | capacity_building | capital
    support_item    TEXT NOT NULL,
    allocated       NUMERIC(10,2),
    spent           NUMERIC(10,2) DEFAULT 0,
    provider_id     BIGINT REFERENCES personal.person(id)
);

CREATE INDEX idx_ndis_support_plan ON personal.ndis_support (plan_id);

-- ── Household finance ─────────────────────────────────────────────────────────
CREATE TABLE personal.recurring_obligation (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    category        TEXT,                               -- insurance | utility | subscription | loan | other
    amount          NUMERIC(10,2),
    frequency       TEXT NOT NULL,                      -- weekly | fortnightly | monthly | quarterly | annual
    next_due        DATE,
    auto_pay        BOOLEAN NOT NULL DEFAULT false,
    notes           TEXT
);

CREATE INDEX idx_obligation_due ON personal.recurring_obligation (next_due);

-- ── Calendar events ───────────────────────────────────────────────────────────
CREATE TABLE personal.event (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    calendar_event_id   TEXT UNIQUE,                    -- Google/Outlook event ID
    title               TEXT NOT NULL,
    event_type          TEXT,                           -- school | medical | ndis | household | family
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ,
    person_id           BIGINT REFERENCES personal.person(id),
    calendar_source     TEXT,                           -- which calendar (primary | partner | kids)
    notes               TEXT
);

CREATE INDEX idx_personal_event_starts ON personal.event (starts_at);
CREATE INDEX idx_personal_event_type   ON personal.event (event_type);
CREATE INDEX idx_personal_event_person ON personal.event (person_id);

-- ── Household knowledge / notes ───────────────────────────────────────────────
-- Structured capture for "Hey Geoff" voice notes that don't fit other tables
CREATE TABLE personal.note (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'voice',      -- voice | manual | agent
    body            TEXT NOT NULL,
    tags            TEXT[],
    person_id       BIGINT REFERENCES personal.person(id),
    embedding       vector(768)
);

CREATE INDEX idx_personal_note_embed ON personal.note USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
CREATE INDEX idx_personal_note_tags  ON personal.note USING gin (tags);

-- ── Grants ────────────────────────────────────────────────────────────────────
-- personal schema is curator + superuser only
-- dashboard_ro gets NO access (intentional — private family data)
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA personal TO openclaw_curator_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA personal TO openclaw_curator_role;

-- n8n needs access for calendar sync and WhatsApp orchestration
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA personal TO openclaw_n8n_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA personal TO openclaw_n8n_role;

-- Trigger
CREATE OR REPLACE FUNCTION personal.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_person_updated BEFORE UPDATE ON personal.person
    FOR EACH ROW EXECUTE FUNCTION personal.set_updated_at();
