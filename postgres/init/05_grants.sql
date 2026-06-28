-- Schema-level grants per role
-- More granular table-level grants are added in later stage init scripts

-- dashboard_ro: read everything except personal schema
GRANT USAGE ON SCHEMA property_deals     TO familybrain_readonly;
GRANT USAGE ON SCHEMA decision_architect TO familybrain_readonly;
GRANT USAGE ON SCHEMA audit              TO familybrain_readonly;

-- personal schema: curator + superuser only (no group grant here)
GRANT USAGE ON SCHEMA personal TO curator;

-- dashboard_ro: needs personal schema for review queue and financial data
GRANT USAGE ON SCHEMA personal TO dashboard_ro;

-- scraper: write to property_deals only
GRANT USAGE ON SCHEMA property_deals     TO familybrain_scraper_role;
GRANT USAGE ON SCHEMA audit              TO familybrain_scraper_role;

-- pr_agent: read/write decision_architect, read property_deals
GRANT USAGE ON SCHEMA decision_architect TO familybrain_pr_agent_role;
GRANT USAGE ON SCHEMA property_deals     TO familybrain_pr_agent_role;
GRANT USAGE ON SCHEMA audit              TO familybrain_pr_agent_role;

-- curator: read all three, write decision_architect + personal
GRANT USAGE ON SCHEMA personal           TO familybrain_curator_role;
GRANT USAGE ON SCHEMA property_deals     TO familybrain_curator_role;
GRANT USAGE ON SCHEMA decision_architect TO familybrain_curator_role;
GRANT USAGE ON SCHEMA audit              TO familybrain_curator_role;

-- podcast_agent: read decision_architect only
GRANT USAGE ON SCHEMA decision_architect TO familybrain_podcast_role;
GRANT USAGE ON SCHEMA audit              TO familybrain_podcast_role;

-- n8n: needs access to all schemas for orchestration workflows
GRANT USAGE ON SCHEMA personal           TO familybrain_n8n_role;
GRANT USAGE ON SCHEMA property_deals     TO familybrain_n8n_role;
GRANT USAGE ON SCHEMA decision_architect TO familybrain_n8n_role;
GRANT USAGE ON SCHEMA audit              TO familybrain_n8n_role;
