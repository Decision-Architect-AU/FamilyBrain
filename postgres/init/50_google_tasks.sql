-- Increment 5: Google Tasks channel — bidirectional TODO sync.
-- See C:\Users\Glenn\Downloads\increment-5-google-tasks-channel.md and the
-- approved plan for full design rationale.

-- Dedicated task lifecycle column. Deliberately NOT reusing the shared
-- `status` column — its CHECK constraint is calendar-lifecycle-specific
-- (scheduled/cancelled/rescheduled/completed/generated/superseded/ingested/
-- confirmed/suspended) and shared across every event type. Same reasoning
-- already proven for investigation_status in Increment 4
-- (postgres/init/49_products.sql): a dedicated column avoids conflating two
-- differently-scoped lifecycles on one field.
ALTER TABLE personal.event
    ADD COLUMN task_status TEXT CHECK (task_status IN ('open', 'done'));

-- Generalizes calendar_sync_map from calendar-mirror-specific (built for
-- "the same calendar event exists in two calendar accounts, keep them in
-- sync" — source_account_id/mirror_account_id pair, target_cal_provider_id,
-- etag) to a channel-discriminated sync map: "our event and one external
-- resource on some channel." Confirmed live it had no such discriminator
-- before this. Existing rows backfill to 'gcal_mirror' so current
-- calendar-mirror behavior is untouched; new Task rows use 'gtask_primary'.
ALTER TABLE personal.calendar_sync_map
    ADD COLUMN channel TEXT NOT NULL DEFAULT 'gcal_mirror';

ALTER TABLE personal.calendar_sync_map
    DROP CONSTRAINT calendar_sync_map_source_account_id_source_provider_id_key;
ALTER TABLE personal.calendar_sync_map
    ADD CONSTRAINT calendar_sync_map_channel_source_key
        UNIQUE (channel, source_account_id, source_provider_id);

INSERT INTO personal.channel (slug, name, direction, provider, config) VALUES
    ('google_tasks',  'Google Tasks (inbound)',  'inbound',  'google', '{}'),
    ('gtask_primary', 'Google Tasks (primary)',  'outbound', 'google', '{}');

-- Confirmed live (channel_resolver.py's resolve()): item_type IS NULL is a
-- wildcard, so this rule is scoped specifically to item_type='task' and
-- won't affect any other outbound routing.
INSERT INTO personal.channel_rule (channel_id, item_type, schedule, target_slot)
    SELECT id, 'task', 'immediate', 'default' FROM personal.channel WHERE slug = 'gtask_primary';
