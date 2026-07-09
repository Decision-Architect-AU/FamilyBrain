-- ── Routine Context Pack infrastructure ──────────────────────────────────────
-- event_config     : key-value store for assembler horizons and thresholds
-- event_participant: links a routine asset to its provider / subject / location
-- asset_availability: unavailability intervals for providers and subjects

-- ── event_config ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personal.event_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO personal.event_config (key, value) VALUES
    ('DIFF_HORIZON_DAYS',      '21'),   -- differences block horizon (lead time for gaps)
    ('OCC_HORIZON_DAYS',       '7'),    -- occurrences block horizon (near window)
    ('COLLISION_FLOOR',        '50'),   -- min confidence to classify as collision
    ('IMMEDIATE_NOTIFY_MIN',   '70')    -- min confidence to lead-item a subject collision
ON CONFLICT (key) DO NOTHING;

-- ── event_participant ─────────────────────────────────────────────────────────
-- A participant is either a person (person_id) or an asset (asset_id); at least
-- one must be set. display_name is always populated as a human-readable fallback.

CREATE TABLE IF NOT EXISTS personal.event_participant (
    id               BIGSERIAL PRIMARY KEY,
    routine_asset_id BIGINT NOT NULL REFERENCES personal.asset(id) ON DELETE CASCADE,
    role             TEXT   NOT NULL CHECK (role IN ('provider', 'subject', 'location')),
    person_id        BIGINT REFERENCES personal.person(id),
    asset_id         BIGINT REFERENCES personal.asset(id),
    display_name     TEXT   NOT NULL,
    is_reassignable  BOOLEAN NOT NULL DEFAULT true,  -- applies to provider role only
    CONSTRAINT ep_has_ref CHECK (person_id IS NOT NULL OR asset_id IS NOT NULL OR display_name IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_ep_routine ON personal.event_participant(routine_asset_id);
CREATE INDEX IF NOT EXISTS idx_ep_person  ON personal.event_participant(person_id) WHERE person_id IS NOT NULL;

-- ── asset_availability ────────────────────────────────────────────────────────
-- Tracks when a person or asset is NOT available (away, leave, etc.).
-- availability_type: 'unavailable' (gaps) | 'available' (explicit availability windows)
-- confidence: 0-100 (100 = confirmed, <50 = low confidence)
-- source: 'manual', 'email', 'calendar', 'inferred'

CREATE TABLE IF NOT EXISTS personal.asset_availability (
    id                BIGSERIAL PRIMARY KEY,
    person_id         BIGINT REFERENCES personal.person(id),
    asset_id          BIGINT REFERENCES personal.asset(id),
    availability_type TEXT   NOT NULL DEFAULT 'unavailable'
                            CHECK (availability_type IN ('unavailable', 'available')),
    start_date        DATE   NOT NULL,
    end_date          DATE   NOT NULL,
    confidence        INT    NOT NULL DEFAULT 100 CHECK (confidence BETWEEN 0 AND 100),
    source            TEXT   NOT NULL DEFAULT 'manual'
                            CHECK (source IN ('manual', 'email', 'calendar', 'inferred')),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT avail_has_ref CHECK (person_id IS NOT NULL OR asset_id IS NOT NULL),
    CONSTRAINT avail_date_order CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_avail_person     ON personal.asset_availability(person_id, start_date, end_date) WHERE person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_avail_asset      ON personal.asset_availability(asset_id,  start_date, end_date) WHERE asset_id  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_avail_date_range ON personal.asset_availability(start_date, end_date);

-- ── Seed event_participant rows from existing routine assets ──────────────────
-- Derived from facts.who / facts.kids in the 10 existing routine assets.
-- Persons: Olivia West=1, Elliana West=2, Shannon West=3, Cindy Blignaut=4 (Nanna)

-- Varsity College - Olivia (asset 59): subject=Olivia, location=Varsity College
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (59, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (59, 'location', 'Varsity College', false)
    ON CONFLICT DO NOTHING;

-- Varsity College - Elliana (asset 60): subject=Elliana, location=Varsity College
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (60, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (60, 'location', 'Varsity College', false)
    ON CONFLICT DO NOTHING;

-- Monday - Babysitter Pickup (asset 61): provider=Babysitter, subject=Olivia+Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (61, 'provider', 'Babysitter', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (61, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (61, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Tuesday - After School Care (asset 62): provider=After School Care, subject=Olivia+Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (62, 'provider', 'After School Care', false)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (62, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (62, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Wednesday - Dancing - Elliana (asset 63): subject=Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (63, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Wednesday - After School Care - Olivia (asset 64): provider=After School Care, subject=Olivia
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (64, 'provider', 'After School Care', false)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (64, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;

-- Wednesday - Nanna Pickup (asset 65): provider=Nanna, subject=Olivia+Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (65, 'provider', 4, 'Nanna', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (65, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (65, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Thursday - Nanna Pickup (asset 66): provider=Nanna, subject=Olivia+Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (66, 'provider', 4, 'Nanna', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (66, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (66, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Friday - Jen Jen Pickup (asset 67): provider=Jen Jen, subject=Olivia+Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, display_name, is_reassignable)
    VALUES (67, 'provider', 'Jen Jen', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (67, 'subject', 1, 'Olivia', true)
    ON CONFLICT DO NOTHING;
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (67, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;

-- Tuesday - Cello Class - Elliana (asset 68): subject=Elliana
INSERT INTO personal.event_participant (routine_asset_id, role, person_id, display_name, is_reassignable)
    VALUES (68, 'subject', 2, 'Elliana', true)
    ON CONFLICT DO NOTHING;
