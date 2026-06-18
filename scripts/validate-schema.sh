#!/usr/bin/env bash
# Quick sanity check: connect as superuser and verify all tables exist
set -euo pipefail

PGPASSWORD="${POSTGRES_SUPERUSER_PASSWORD}" psql \
    -h localhost -p 5432 \
    -U "${POSTGRES_SUPERUSER:-geoff}" \
    -d openclaw \
    -c "
SELECT schemaname, tablename
FROM pg_tables
WHERE schemaname IN ('personal','property_deals','decision_architect','audit')
ORDER BY schemaname, tablename;
"

echo ""
echo "-- Extensions:"
PGPASSWORD="${POSTGRES_SUPERUSER_PASSWORD}" psql \
    -h localhost -p 5432 \
    -U "${POSTGRES_SUPERUSER:-geoff}" \
    -d openclaw \
    -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','age','pg_trgm');"

echo ""
echo "-- AGE graphs:"
PGPASSWORD="${POSTGRES_SUPERUSER_PASSWORD}" psql \
    -h localhost -p 5432 \
    -U "${POSTGRES_SUPERUSER:-geoff}" \
    -d openclaw \
    -c "SELECT name FROM ag_catalog.ag_graph;"

echo ""
echo "-- Seed themes:"
PGPASSWORD="${POSTGRES_SUPERUSER_PASSWORD}" psql \
    -h localhost -p 5432 \
    -U "${POSTGRES_SUPERUSER:-geoff}" \
    -d openclaw \
    -c "SELECT name, priority FROM decision_architect.theme ORDER BY priority;"
