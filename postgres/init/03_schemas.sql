-- Create the three logical schemas and the audit schema

CREATE SCHEMA IF NOT EXISTS personal;
CREATE SCHEMA IF NOT EXISTS property_deals;
CREATE SCHEMA IF NOT EXISTS decision_architect;
CREATE SCHEMA IF NOT EXISTS audit;

-- AGE graphs — must load AGE and set search_path per session before calling create_graph
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT create_graph('personal_graph');
SELECT create_graph('property_graph');
SELECT create_graph('decision_graph');

-- Schema ownership stays with superuser; access is controlled via grants in 05_grants.sql
