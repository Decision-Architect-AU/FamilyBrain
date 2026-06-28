# postgres

PostgreSQL 16 with Apache AGE 1.6, pgvector, and pg_trgm. The single source of truth for all structured data.

## Extensions

| Extension | Purpose |
|-----------|---------|
| Apache AGE 1.6 | Graph layer — Cypher queries over relational data |
| pgvector | Semantic similarity search on embeddings |
| pg_trgm | Fuzzy text matching and trigram similarity |

## Schemas

| Schema | Owner | Purpose |
|--------|-------|---------|
| `personal` | curator | Family, care, household, appointments, notes, assets, events |
| `property_deals` | curator | Property listings, market research |
| `decision_architect` | curator | Frameworks, thought leadership |
| `audit` | audit_writer | Append-only activity log |
| `config` | curator | Intent rules, response personas, channel rules |
| `n8n` | n8n | n8n workflow state |

## Roles

| Role | Permissions |
|------|------------|
| `geoff` | Superuser |
| `curator` | Read/write all schemas |
| `dashboard_ro` | Read-only on personal, property_deals, decision_architect, config, audit |
| `audit_writer` | Append-only on audit.log |
| `n8n` | Read/write n8n schema only |
| `scraper` | Write to property_deals |
| `pr_agent` | Read/write decision_architect |

## AGE usage

Every connection that uses Cypher must run:
```sql
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
```

This is handled automatically in `graph.py` and `search.py`. The AGE Viewer (port 8888) does this on connection.

## Initialisation

`postgres/init/` contains ordered SQL scripts that run on first container start. To re-run a migration manually:

```bash
docker exec openclaw-postgres psql -U geoff -d openclaw -f /path/to/migration.sql
```

Migrations in `postgres/migrations/` must be applied manually after initial setup:

```bash
docker exec openclaw-postgres psql -U geoff -d openclaw -f /docker-entrypoint-initdb.d/<migration>.sql
```

## Environment variables

```env
POSTGRES_USER=geoff
POSTGRES_PASSWORD=<required>
POSTGRES_DB=openclaw
DASHBOARD_DB_PASSWORD=<required>
AUDIT_DB_PASSWORD=<required>
N8N_DB_PASSWORD=<required>
CURATOR_DB_PASSWORD=<required>
```
