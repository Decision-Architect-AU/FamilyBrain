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

## Edge confidence (AGE)

Every edge in `personal_graph` carries `confidence INT` (0–100, backfilled per source-type prior — email-derived 40, manual 65, system-structural e.g. participant bindings 90). Suppressing an edge sets `confidence = 0` plus `zeroed_by`/`zeroed_at`/`zero_reason`/`zero_prev_confidence` rather than deleting it — see the main [README's Asset Dossier & Suppression section](../README.md#asset-dossier--suppression) for the full semantics. `confidence > 0` is the universal read-path predicate; there is no separate suppression flag to check anywhere in retrieval, enrichment, or the dossier.

`postgres/init/32_graph_indexes.sql` — btree on vertex `name` + GIN on vertex `properties`, one label at a time, for `personal_graph`/`decision_graph`/`property_graph`. There is **no index that helps an unlabeled or undirected Cypher `MATCH`** — AGE stores each vertex/edge label as its own physical table, so a labeled, directed `MATCH (a:Asset {ref: '...'})-[r]->(n)` only ever scans that one small table, while an unlabeled `MATCH (n {ref: '...'})` or an undirected `-[r]-` scans every label table in the graph. On a graph with hundreds of thousands of edges this is the difference between milliseconds and multi-hour hangs — always label and direct Cypher queries used in a hot path (anything run per-row in a maintenance loop).

## Initialisation

`postgres/init/` contains ordered SQL scripts that run on first container start. To re-run a migration manually:

```bash
docker exec familybrain-postgres psql -U geoff -d familybrain -f /path/to/migration.sql
```

Migrations in `postgres/migrations/` must be applied manually after initial setup:

```bash
docker exec familybrain-postgres psql -U geoff -d familybrain -f /docker-entrypoint-initdb.d/<migration>.sql
```

## Environment variables

```env
POSTGRES_USER=geoff
POSTGRES_PASSWORD=<required>
POSTGRES_DB=familybrain
DASHBOARD_DB_PASSWORD=<required>
AUDIT_DB_PASSWORD=<required>
N8N_DB_PASSWORD=<required>
CURATOR_DB_PASSWORD=<required>
```
