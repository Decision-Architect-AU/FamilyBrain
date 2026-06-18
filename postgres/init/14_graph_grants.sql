-- Grant dashboard_ro read access to AGE graph schemas (created dynamically by AGE)
GRANT USAGE ON SCHEMA decision_graph TO dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA decision_graph TO dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA decision_graph GRANT SELECT ON TABLES TO dashboard_ro;

GRANT USAGE ON SCHEMA personal_graph TO dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA personal_graph TO dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA personal_graph GRANT SELECT ON TABLES TO dashboard_ro;

GRANT USAGE ON SCHEMA property_graph TO dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA property_graph TO dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA property_graph GRANT SELECT ON TABLES TO dashboard_ro;
