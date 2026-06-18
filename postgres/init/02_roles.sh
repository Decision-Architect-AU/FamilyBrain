#!/usr/bin/env bash
# Creates roles and login users with passwords from env vars
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Role groups
    DO \$\$ BEGIN
        CREATE ROLE openclaw_readonly;         EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_scraper_role;     EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_pr_agent_role;    EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_curator_role;     EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_audit_writer_role; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_podcast_role;     EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
    DO \$\$ BEGIN
        CREATE ROLE openclaw_n8n_role;         EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;

    -- Login users
    DO \$\$ BEGIN
        CREATE USER dashboard_ro  WITH PASSWORD '${DASHBOARD_DB_PASSWORD}'  IN ROLE openclaw_readonly;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER dashboard_ro  WITH PASSWORD '${DASHBOARD_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER audit_writer  WITH PASSWORD '${AUDIT_DB_PASSWORD}'       IN ROLE openclaw_audit_writer_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER audit_writer  WITH PASSWORD '${AUDIT_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER n8n           WITH PASSWORD '${N8N_DB_PASSWORD}'          IN ROLE openclaw_n8n_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER n8n           WITH PASSWORD '${N8N_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER scraper       WITH PASSWORD '${SCRAPER_DB_PASSWORD}'      IN ROLE openclaw_scraper_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER scraper       WITH PASSWORD '${SCRAPER_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER pr_agent      WITH PASSWORD '${AGENTS_DB_PASSWORD}'       IN ROLE openclaw_pr_agent_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER pr_agent      WITH PASSWORD '${AGENTS_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER curator       WITH PASSWORD '${CURATOR_DB_PASSWORD}'      IN ROLE openclaw_curator_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER curator       WITH PASSWORD '${CURATOR_DB_PASSWORD}';
    END \$\$;
    DO \$\$ BEGIN
        CREATE USER podcast_agent WITH PASSWORD '${PODCAST_DB_PASSWORD}'      IN ROLE openclaw_podcast_role;
        EXCEPTION WHEN duplicate_object THEN
            ALTER USER podcast_agent WITH PASSWORD '${PODCAST_DB_PASSWORD}';
    END \$\$;
EOSQL
