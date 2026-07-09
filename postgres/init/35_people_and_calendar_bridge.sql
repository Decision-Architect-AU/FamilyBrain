-- ── Correct person records and add calendar→availability bridge ───────────────

-- 1. Add Meg (Nanna) and Ray (Poppy) as persons
--    person_id=4 (Cindy Blignaut) was a YMCA contact incorrectly seeded as Nanna.
--    Meg and Ray are Glenn's parents-in-law / the kids' grandparents.

INSERT INTO personal.person (name, relationship, notes)
VALUES
    ('Meg',  'grandmother', 'Nanna — Olivia & Elliana''s grandmother. Provider for Wednesday and Thursday pickups.'),
    ('Ray',  'grandfather', 'Poppy — Olivia & Elliana''s grandfather. Travels with Meg.')
ON CONFLICT DO NOTHING;

-- 2. Fix the Nanna provider rows in event_participant to point at Meg (not Cindy Blignaut)
--    We do this by name match so it's idempotent even if Meg gets a different id on first run.

UPDATE personal.event_participant
SET person_id = (SELECT id FROM personal.person WHERE name = 'Meg' LIMIT 1)
WHERE display_name = 'Nanna'
  AND role = 'provider'
  AND person_id = 4;   -- only correct the wrongly-seeded rows

-- 3. Shannon email_account row (person_id=3 already exists, just missing the account row)
INSERT INTO personal.email_account
    (provider, email_address, display_name, owner_person_id,
     refresh_token, sync_email, sync_calendar, is_partner_calendar)
VALUES
    ('gmail', 'shannon.garner@gmail.com', 'Shannon West', 3,
     '', true, true, true)
ON CONFLICT (email_address) DO UPDATE
    SET owner_person_id   = EXCLUDED.owner_person_id,
        is_partner_calendar = true;

-- 4. Elliana email_address (placeholder until she has a real account)
UPDATE personal.person
SET email = 'elliana.west@student.eq.edu.au'   -- update if wrong; just a placeholder
WHERE id = 2 AND email IS NULL;

-- 5. routine_gap_calendar_event table — bridge between ingested calendar events
--    and asset_availability. The appointment_updater populates this when it
--    recognises a personal-travel or away event involving a known person.
--    The gap detector reads it to create/update asset_availability rows automatically.

CREATE TABLE IF NOT EXISTS personal.calendar_availability_hint (
    id              BIGSERIAL PRIMARY KEY,
    event_id        BIGINT NOT NULL REFERENCES personal.event(id) ON DELETE CASCADE,
    person_id       BIGINT REFERENCES personal.person(id),
    asset_id        BIGINT REFERENCES personal.asset(id),
    display_name    TEXT   NOT NULL,
    hint_start      DATE   NOT NULL,
    hint_end        DATE   NOT NULL,
    availability_id BIGINT REFERENCES personal.asset_availability(id) ON DELETE SET NULL,
    processed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cah_has_ref CHECK (person_id IS NOT NULL OR asset_id IS NOT NULL OR display_name IS NOT NULL),
    CONSTRAINT cah_date_order CHECK (hint_end >= hint_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cah_event_person
    ON personal.calendar_availability_hint(event_id, COALESCE(person_id, -1));
CREATE INDEX IF NOT EXISTS idx_cah_unprocessed
    ON personal.calendar_availability_hint(processed_at) WHERE processed_at IS NULL;
