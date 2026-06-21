-- Channel registry + scheduling rules
-- Channels are inbound sources (gmail, outlook, voice) and outbound destinations
-- (gcal_family, gcal_bills, task_list, etc.).
-- Rules per channel control: what matches, when to push (schedule), how to route.

SET search_path = personal, public;

-- ── Channel registry ──────────────────────────────────────────────────────────

CREATE TABLE personal.channel (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    slug        TEXT NOT NULL UNIQUE,       -- gcal_family, gmail_inbox, etc.
    name        TEXT NOT NULL,
    direction   TEXT NOT NULL,              -- inbound | outbound | both
    provider    TEXT NOT NULL,              -- gmail | outlook | gcal | voice | manual
    config      JSONB NOT NULL DEFAULT '{}',-- provider-specific (calendar_id, etc.)
    enabled     BOOLEAN NOT NULL DEFAULT true
);

-- ── Channel rules ─────────────────────────────────────────────────────────────
-- Evaluated in priority order (lower number = higher priority).
-- First matching rule wins. NULL condition fields = wildcard (match anything).

CREATE TABLE personal.channel_rule (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel_id  BIGINT NOT NULL REFERENCES personal.channel(id) ON DELETE CASCADE,
    priority    INT NOT NULL DEFAULT 100,

    -- Match conditions (NULL = wildcard)
    item_type   TEXT,   -- calendar_event | payment | task | observation | bill | *
    category    TEXT,   -- financial | family | holiday | bills | *
    source_slug TEXT,   -- which inbound channel slug (NULL = any)

    -- Scheduling: when to materialise next_update_at
    -- immediate          → now()
    -- before_event:Nd    → effective_date - N days (at 06:00 AEST)
    -- on_due_date        → effective_date (at 06:00 AEST)
    -- batch:daily:HH:MM  → next occurrence of HH:MM AEST
    -- never              → NULL (only process on explicit change)
    schedule    TEXT NOT NULL DEFAULT 'immediate',

    -- Routing hints for outbound channels
    target_slot TEXT,   -- bills | family | holidays | default (maps to AccountCalendars slots)
    color_id    TEXT,   -- GCal colorId override

    enabled     BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX idx_channel_rule_channel  ON personal.channel_rule (channel_id, priority)
    WHERE enabled = true;

-- ── Seed channels ─────────────────────────────────────────────────────────────

INSERT INTO personal.channel (slug, name, direction, provider, config) VALUES
-- Inbound
('gmail_inbox',       'Gmail Inbox',            'inbound',  'gmail',   '{}'),
('outlook_inbox',     'Outlook Inbox',           'inbound',  'outlook', '{}'),
('voice_notes',       'Voice Notes',             'inbound',  'voice',   '{}'),
('manual_entry',      'Manual Entry',            'inbound',  'manual',  '{}'),
-- Outbound
('gcal_primary',      'Primary Calendar',        'outbound', 'gcal',    '{"slot":"default"}'),
('gcal_family',       'Family Calendar',         'outbound', 'gcal',    '{"slot":"family"}'),
('gcal_bills',        'Bills Calendar',          'outbound', 'gcal',    '{"slot":"bills"}'),
('gcal_holidays',     'Holidays Calendar',       'outbound', 'gcal',    '{"slot":"holidays"}'),
('task_list',         'Task List (notes)',        'outbound', 'manual',  '{}'),
('observations',      'Observations (notes)',     'outbound', 'manual',  '{}')
ON CONFLICT (slug) DO NOTHING;

-- ── Seed rules ────────────────────────────────────────────────────────────────
-- gcal_family: family events → push immediately
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot, color_id)
SELECT id, 10, 'calendar_event', 'family', 'immediate', 'family', NULL
FROM personal.channel WHERE slug = 'gcal_family'
ON CONFLICT DO NOTHING;

-- gcal_family: holiday events → push immediately + day expansion handled by updater
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot, color_id)
SELECT id, 20, 'calendar_event', 'holiday', 'immediate', 'holidays', '2'
FROM personal.channel WHERE slug = 'gcal_holidays'
ON CONFLICT DO NOTHING;

-- gcal_bills: payments → 3 days before due date (first notice), then day-of (final)
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot, color_id)
SELECT id, 10, 'payment', NULL, 'before_event:3d', 'bills', '11'
FROM personal.channel WHERE slug = 'gcal_bills'
ON CONFLICT DO NOTHING;

INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot, color_id)
SELECT id, 20, 'bill', NULL, 'before_event:3d', 'bills', '11'
FROM personal.channel WHERE slug = 'gcal_bills'
ON CONFLICT DO NOTHING;

-- task_list: tasks → immediate
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot)
SELECT id, 10, 'task', NULL, 'immediate', NULL
FROM personal.channel WHERE slug = 'task_list'
ON CONFLICT DO NOTHING;

-- observations: batch daily at 07:00
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot)
SELECT id, 10, 'observation', NULL, 'batch:daily:07:00', NULL
FROM personal.channel WHERE slug = 'observations'
ON CONFLICT DO NOTHING;

-- Default catch-all: anything not matched → push immediately to primary
INSERT INTO personal.channel_rule (channel_id, priority, item_type, category, schedule, target_slot)
SELECT id, 999, NULL, NULL, 'immediate', 'default'
FROM personal.channel WHERE slug = 'gcal_primary'
ON CONFLICT DO NOTHING;

-- ── Grants ────────────────────────────────────────────────────────────────────
GRANT SELECT ON personal.channel TO openclaw_curator_role, dashboard_ro;
GRANT SELECT ON personal.channel_rule TO openclaw_curator_role, dashboard_ro;
GRANT INSERT, UPDATE ON personal.channel_rule TO openclaw_curator_role, dashboard_ro;
GRANT USAGE ON SEQUENCE personal.channel_rule_id_seq TO openclaw_curator_role, dashboard_ro;
