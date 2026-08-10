-- Tentative calendar (family-brain-weekly-digest-spec.md P0-1b) — same pattern
-- as bills/holidays/family in 17_calendar_routing.sql. The spec describes
-- personal.channel/channel_rule as the live routing mechanism, but the actual
-- calendar-ID resolution (calendar_router.py's target_calendar_id()) reads
-- hardcoded email_account columns, not channel.config — the channel registry
-- only supplies the route *name*, not where it points. Matching that existing,
-- real pattern rather than the spec's more idealised one, confirmed as the
-- right call rather than reworking the live calendar-write path.
ALTER TABLE personal.email_account
    ADD COLUMN IF NOT EXISTS tentative_calendar_id TEXT;

COMMENT ON COLUMN personal.email_account.tentative_calendar_id IS 'Gmail calendarId or Outlook calendar objectId for the Tentative (quarantine) calendar';

-- Channel registry row, for consistency with the other three outbound slots
-- and so anything reading personal.channel (dashboard, future code) sees it.
INSERT INTO personal.channel (slug, name, direction, provider, config) VALUES
('gcal_tentative', 'Tentative Calendar', 'outbound', 'gcal', '{"slot":"tentative"}')
ON CONFLICT (slug) DO NOTHING;
