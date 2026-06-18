-- Run as superuser on first container init

-- pgvector: semantic similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Apache AGE: graph layer
LOAD 'age';
CREATE EXTENSION IF NOT EXISTS age;
SET search_path = ag_catalog, "$user", public;

-- pg_trgm: fuzzy text search (useful for scraper dedup)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
