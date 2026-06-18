-- Central audit log — append-only, all agents write here from day one

CREATE TABLE audit.log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent           TEXT        NOT NULL,           -- e.g. 'scraper', 'pr_writer', 'curator'
    action_type     TEXT        NOT NULL,           -- read | write | query | publish | approve | reject
    target_schema   TEXT,                           -- personal | property_deals | decision_architect
    target_table    TEXT,                           -- table or AGE graph name
    node_id         TEXT,                           -- AGE node id or relational row pk, if applicable
    summary         TEXT        NOT NULL,           -- human-readable one-liner
    mode_active     TEXT        NOT NULL,           -- normal | podcast | core
    metadata        JSONB       DEFAULT '{}'::jsonb -- action-specific detail
);

-- Append-only: revoke UPDATE/DELETE from all roles
REVOKE UPDATE, DELETE, TRUNCATE ON audit.log FROM PUBLIC;

-- Only audit_writer can INSERT
GRANT INSERT ON audit.log TO openclaw_audit_writer_role;
GRANT USAGE ON SEQUENCE audit.log_id_seq TO openclaw_audit_writer_role;

-- All agent roles and readonly get SELECT
GRANT SELECT ON audit.log TO
    openclaw_readonly,
    openclaw_scraper_role,
    openclaw_pr_agent_role,
    openclaw_curator_role,
    openclaw_podcast_role,
    openclaw_n8n_role;

-- Indexes for dashboard queries
CREATE INDEX idx_audit_log_ts         ON audit.log (ts DESC);
CREATE INDEX idx_audit_log_agent      ON audit.log (agent);
CREATE INDEX idx_audit_log_action     ON audit.log (action_type);
CREATE INDEX idx_audit_log_mode       ON audit.log (mode_active);
