-- Throttle state for maintenance tasks that must not run every 5-minute
-- maintenance-cron tick (expensive LLM-backed tasks like the query-flags
-- review pass). Existing tasks (link, audit_concepts, review_data_expectations)
-- use a /tmp flag file instead — confirmed live to be fragile: /tmp is
-- container-local and doesn't survive a restart, so any restart (a crash, an
-- image rebuild, a host reboot) resets the throttle to "never run", and the
-- very next cron tick re-fires the expensive task immediately. Observed
-- directly this session: repeated wa-agent rebuilds caused `link` and
-- `audit_concepts` to re-fire on nearly every 5-minute tick instead of daily,
-- visibly degrading concurrent live query quality via GPU contention. New
-- throttled tasks should use this table, not a /tmp file; migrating the
-- three existing ones is a separate follow-up, not bundled into this change.
CREATE TABLE IF NOT EXISTS config.maintenance_throttle (
    task_name   TEXT PRIMARY KEY,
    last_run_at TIMESTAMPTZ NOT NULL
);

GRANT SELECT, INSERT, UPDATE ON config.maintenance_throttle TO openclaw_curator_role, openclaw_n8n_role;
