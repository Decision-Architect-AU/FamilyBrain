-- Email account registry + message deduplication
-- Supports multiple Gmail accounts and Outlook/Hotmail accounts
-- Tokens stored here; email-sync service reads/updates them

SET search_path = personal, public;

-- ── Email accounts ─────────────────────────────────────────────────────────────
-- One row per connected inbox. provider = gmail | outlook
CREATE TABLE personal.email_account (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider        TEXT NOT NULL,                      -- gmail | outlook
    email_address   TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    owner_person_id BIGINT REFERENCES personal.person(id),

    -- OAuth2 tokens (stored as encrypted text via pgcrypto if desired,
    -- or plain if the DB itself is access-controlled — curator only)
    access_token    TEXT,
    refresh_token   TEXT NOT NULL,
    token_expiry    TIMESTAMPTZ,
    token_scope     TEXT,

    -- Provider-specific metadata
    -- Gmail: history_id for incremental sync (Gmail History API)
    -- Outlook: delta_link for incremental sync (MS Graph delta query)
    sync_cursor     TEXT,

    -- Per-account toggles
    sync_email      BOOLEAN NOT NULL DEFAULT true,
    sync_calendar   BOOLEAN NOT NULL DEFAULT true,
    calendar_id     TEXT,                               -- Gmail: calendarId; Outlook: calendar object id
    is_primary      BOOLEAN NOT NULL DEFAULT false,     -- primary account drives calendar writes

    last_synced_at  TIMESTAMPTZ,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    notes           TEXT
);

CREATE INDEX idx_email_account_provider ON personal.email_account (provider);
CREATE INDEX idx_email_account_enabled  ON personal.email_account (enabled) WHERE enabled = true;

-- ── Email messages (deduplication + ingestion state) ──────────────────────────
-- Tracks which messages have been seen/ingested. Body text lives in personal.note
-- (ingested via the standard ingestor pipeline → personal_graph).
CREATE TABLE personal.email_message (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id      BIGINT NOT NULL REFERENCES personal.email_account(id),

    -- Provider message identifier (Gmail message ID or Outlook item id)
    provider_msg_id TEXT NOT NULL,
    thread_id       TEXT,                               -- Gmail threadId / Outlook conversationId

    -- Envelope fields
    from_address    TEXT,
    from_name       TEXT,
    to_addresses    TEXT[],
    subject         TEXT,
    received_at     TIMESTAMPTZ,

    -- Ingestion state
    schema_routed   TEXT,                               -- personal | property | decision
    note_id         BIGINT REFERENCES personal.note(id),
    ingest_status   TEXT NOT NULL DEFAULT 'pending',    -- pending | ingested | skipped | error
    ingest_error    TEXT,
    ingest_at       TIMESTAMPTZ,

    -- Dedup: unique per account + provider message id
    UNIQUE (account_id, provider_msg_id)
);

CREATE INDEX idx_email_message_account  ON personal.email_message (account_id);
CREATE INDEX idx_email_message_status   ON personal.email_message (ingest_status) WHERE ingest_status = 'pending';
CREATE INDEX idx_email_message_received ON personal.email_message (received_at DESC);

-- ── Calendar sync log ─────────────────────────────────────────────────────────
-- Tracks events synced across providers so we don't create duplicates.
-- personal.event already has calendar_event_id (provider ID) and calendar_source.
-- This table maps provider event IDs across accounts for bidirectional sync.
CREATE TABLE personal.calendar_sync_map (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id        BIGINT NOT NULL REFERENCES personal.event(id) ON DELETE CASCADE,

    -- Source of truth for this event
    source_account_id   BIGINT NOT NULL REFERENCES personal.email_account(id),
    source_provider_id  TEXT NOT NULL,                  -- provider's event ID

    -- Mirror copy in the other provider (NULL if not yet mirrored)
    mirror_account_id   BIGINT REFERENCES personal.email_account(id),
    mirror_provider_id  TEXT,                           -- provider's event ID in mirror

    -- Routed copy in a non-default target calendar (Bills/Family/Health etc.)
    target_cal_provider_id TEXT,                        -- event ID in the routed calendar

    sync_status     TEXT NOT NULL DEFAULT 'pending',    -- pending | synced | conflict | error
    last_synced_at  TIMESTAMPTZ,
    etag            TEXT,                               -- for change detection
    last_etag       TEXT,                               -- etag at last target-cal write

    UNIQUE (source_account_id, source_provider_id)
);

CREATE INDEX idx_cal_sync_event   ON personal.calendar_sync_map (event_id);
CREATE INDEX idx_cal_sync_status  ON personal.calendar_sync_map (sync_status) WHERE sync_status IN ('pending', 'error');

-- ── Email filters (junk / blocklist) ─────────────────────────────────────────
-- Controls what gets ingested. Checked before sending to ingestor.
-- filter_type:
--   sender_block   — exact from_address match
--   domain_block   — anything @domain.com
--   keyword_block  — subject or body contains this string (case-insensitive)
--   sender_allow   — override: always ingest even if heuristics flag it as bulk
CREATE TABLE personal.email_filter (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    filter_type     TEXT NOT NULL,          -- sender_block | domain_block | keyword_block | sender_allow
    value           TEXT NOT NULL,          -- the address, domain, or keyword
    note            TEXT,                   -- why this rule exists
    enabled         BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (filter_type, value)
);

CREATE INDEX idx_email_filter_type ON personal.email_filter (filter_type) WHERE enabled = true;

-- Seed with common junk domains and patterns
INSERT INTO personal.email_filter (filter_type, value, note) VALUES
    ('domain_block', 'mailchimp.com',         'Marketing platform'),
    ('domain_block', 'sendgrid.net',           'Marketing platform'),
    ('domain_block', 'klaviyo.com',            'Marketing platform'),
    ('domain_block', 'constantcontact.com',    'Marketing platform'),
    ('domain_block', 'campaignmonitor.com',    'Marketing platform'),
    ('domain_block', 'salesforce.com',         'CRM bulk mail'),
    ('domain_block', 'hubspot.com',            'CRM bulk mail'),
    ('domain_block', 'marketo.net',            'Marketing platform'),
    ('domain_block', 'bounce.linkedin.com',    'LinkedIn notifications'),
    ('domain_block', 'facebookmail.com',       'Facebook notifications'),
    ('domain_block', 'notifications.google.com', 'Google notifications'),
    ('keyword_block', 'unsubscribe',           'Newsletter/marketing indicator'),
    ('keyword_block', 'click here to unsubscribe', 'Newsletter footer'),
    ('keyword_block', 'view in browser',       'HTML newsletter indicator'),
    ('keyword_block', 'email preferences',     'Marketing footer'),
    ('keyword_block', 'manage your subscription', 'Marketing footer')
ON CONFLICT (filter_type, value) DO NOTHING;

-- ── Triggers ──────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION personal.set_updated_at_generic()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_email_account_updated BEFORE UPDATE ON personal.email_account
    FOR EACH ROW EXECUTE FUNCTION personal.set_updated_at_generic();

CREATE TRIGGER trg_cal_sync_updated BEFORE UPDATE ON personal.calendar_sync_map
    FOR EACH ROW EXECUTE FUNCTION personal.set_updated_at_generic();

-- ── Grants ────────────────────────────────────────────────────────────────────
-- email-sync service runs as curator role
GRANT SELECT, INSERT, UPDATE ON personal.email_account TO openclaw_curator_role;
GRANT SELECT, INSERT, UPDATE ON personal.email_message TO openclaw_curator_role;
GRANT SELECT, INSERT, UPDATE ON personal.calendar_sync_map TO openclaw_curator_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON personal.email_filter TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.email_account_id_seq TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.email_message_id_seq TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.calendar_sync_map_id_seq TO openclaw_curator_role;
GRANT USAGE ON SEQUENCE personal.email_filter_id_seq TO openclaw_curator_role;

-- n8n may need to read email accounts for workflow triggers
GRANT SELECT ON personal.email_account TO openclaw_n8n_role;
GRANT SELECT ON personal.email_message TO openclaw_n8n_role;
