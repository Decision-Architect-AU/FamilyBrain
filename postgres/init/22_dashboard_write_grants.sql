-- dashboard_ro needs write access to action items on the review queue
-- and to manage sender rules (email_filter, email_message category/status).
-- Despite the role name, it has elevated write access for dashboard actions only.

-- email_message: update ingest_status, category, financial_processed
GRANT SELECT, UPDATE ON personal.email_message TO dashboard_ro;

-- email_filter: insert/update for blocking and unblocking
GRANT SELECT, INSERT, UPDATE ON personal.email_filter TO dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.email_filter_id_seq TO dashboard_ro;

-- financial_domain: insert/update for learning new domains
GRANT SELECT, INSERT, UPDATE ON personal.financial_domain TO dashboard_ro;
