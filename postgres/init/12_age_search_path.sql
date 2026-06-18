-- Allow dashboard_ro to query AGE graphs via cypher()
GRANT USAGE ON SCHEMA ag_catalog TO openclaw_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO openclaw_readonly;

-- Set default search_path to include ag_catalog for all users
ALTER DATABASE openclaw SET search_path = ag_catalog, "$user", public;
