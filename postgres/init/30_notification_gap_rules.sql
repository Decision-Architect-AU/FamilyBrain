-- Pattern gap rules — defines expected graph relationships that must exist.
-- Rule watcher checks these and fires PATTERN_GAP notifications when missing.

SET search_path = personal, public;

CREATE TABLE IF NOT EXISTS personal.notification_gap_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    anchor_label    TEXT NOT NULL,      -- AGE node label to anchor the check on
    anchor_filter   JSONB,              -- property filters on anchor node
    expected_label  TEXT NOT NULL,      -- AGE node label that must exist
    expected_rel    TEXT,               -- edge type that must connect them
    window_days     INTEGER NOT NULL,   -- how far ahead to look
    severity        TEXT NOT NULL DEFAULT 'MEDIUM'
                    CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW'))
);

-- ── Seed rules ────────────────────────────────────────────────────────────────

INSERT INTO personal.notification_gap_rules
    (name, anchor_label, anchor_filter, expected_label, expected_rel, window_days, severity)
VALUES
    ('Specialist referral gap',
     'Appointment', '{"subtype": "specialist"}',
     'Appointment', 'REQUIRES', 60, 'HIGH'),

    ('Dentist appointment gap',
     'Reminder', '{"category": "medical", "name_contains": "dentist"}',
     'Appointment', 'SCHEDULES', 30, 'MEDIUM'),

    ('Property settlement children gap',
     'PropertyEvent', '{"subtype": "contract"}',
     'PropertyEvent', 'DEPENDS_ON', 60, 'HIGH'),

    ('School fee invoice gap',
     'SchoolEvent', '{"subtype": "term_start"}',
     'Invoice', NULL, 14, 'LOW');

-- ── Grants ────────────────────────────────────────────────────────────────────
GRANT SELECT ON personal.notification_gap_rules TO dashboard_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON personal.notification_gap_rules TO curator;
