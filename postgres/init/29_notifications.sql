-- Unified notification table
-- Single row per detected issue, deduplicated by dedup_key.
-- Types: COLLISION | SYSTEM_HEALTH | PATTERN_GAP | STALENESS | ACTION_REQUIRED

SET search_path = personal, public;

CREATE TABLE IF NOT EXISTS personal.notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    type            TEXT NOT NULL CHECK (type IN (
                        'COLLISION',
                        'SYSTEM_HEALTH',
                        'PATTERN_GAP',
                        'STALENESS',
                        'ACTION_REQUIRED'
                    )),
    severity        TEXT NOT NULL DEFAULT 'MEDIUM'
                    CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW')),

    status          TEXT NOT NULL DEFAULT 'DETECTED'
                    CHECK (status IN (
                        'DETECTED', 'TRIAGED', 'PENDING', 'RESOLVED', 'IGNORED'
                    )),

    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    node_refs       JSONB NOT NULL DEFAULT '[]',
    options         JSONB NOT NULL DEFAULT '[]',

    -- One active notification per dedup_key (RESOLVED/IGNORED are re-openable)
    dedup_key       TEXT UNIQUE,

    -- Resolution
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    resolution_key  TEXT,
    resolution_note TEXT,

    -- Auto-expiry (SYSTEM_HEALTH self-heal, PENDING reminders)
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notifications_status   ON personal.notifications (status);
CREATE INDEX IF NOT EXISTS idx_notifications_type     ON personal.notifications (type);
CREATE INDEX IF NOT EXISTS idx_notifications_severity ON personal.notifications (severity);
CREATE INDEX IF NOT EXISTS idx_notifications_created  ON personal.notifications (created_at DESC);

-- ── Grants ────────────────────────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE ON personal.notifications TO dashboard_ro;
GRANT SELECT, INSERT, UPDATE ON personal.notifications TO curator;
