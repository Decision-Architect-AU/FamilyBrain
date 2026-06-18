-- Allow all roles to load the AGE library
GRANT EXECUTE ON FUNCTION ag_catalog.load_age() TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA ag_catalog GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
GRANT USAGE ON SCHEMA ag_catalog TO openclaw_curator_role;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA ag_catalog TO openclaw_curator_role;
GRANT ALL ON ALL TABLES IN SCHEMA ag_catalog TO openclaw_curator_role;
