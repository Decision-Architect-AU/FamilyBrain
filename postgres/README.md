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
| `personal` | curator | Family, care, household, appointments, notes, assets, events, products |
| `property_deals` | curator | Property listings, market research |
| `decision_architect` | curator | Frameworks, thought leadership |
| `decision_os` | curator | Decision OS state |
| `ed_core` | commentos | CommentOS concept ontology — concepts, failure modes, outcomes, MEANS edges |
| `audit` | audit_writer | Append-only activity log |
| `config` | curator | Intent rules, response personas, channel rules, maintenance throttles |
| `n8n` | n8n | n8n workflow state |
| `personal_graph`, `property_graph`, `decision_graph` | curator | AGE graph storage — one label per physical table, created lazily on first write |

The database itself is named **`openclaw`**, not `familybrain` — the repo directory and the container prefix differ, so `psql -d familybrain` fails.

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
| `commentos` | Read/write ed_core (CommentOS ontology) |

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

`idx_property_concept_props` (the GIN index the paragraph above depends on for `property_graph`) went missing on the live database even though it's in that file as `CREATE INDEX IF NOT EXISTS` — confirmed live: AGE label tables are created lazily on first write, not by `create_graph()`, so the statement silently no-opped the one time the file ran against a `property_graph` that had zero `Concept` nodes yet, while the other two graphs' indexes succeeded. The gap went unnoticed until `task_dedup()` (wa-agent/src/maintenance.py) started timing out: without it, every per-node `MATCH (c:Concept {name: ...})` in that task's dedup loop fell back to a full Seq Scan over 20k+ rows (~700ms/call instead of ~0.4ms), and multiplied across ~9,400 duplicate `property_graph` concepts that's hours, not the 20-minute task ceiling — while the loop's single uncommitted per-graph transaction held locks the whole time, reproducing the exact "backends stuck blocking live /query traffic" incident below. Re-applied live via `CREATE INDEX CONCURRENTLY`; see `32_graph_indexes.sql`'s comment for how to re-verify or reapply.

## Maintenance task throttling

`config.maintenance_throttle` (`postgres/init/41_maintenance_throttle.sql`) tracks per-task last-run timestamps for wa-agent's maintenance cycle. This replaced a `/tmp`-file-based throttle: the container filesystem resets on every restart, so a task throttled to "once per day" would silently run on every restart instead — during a session with frequent rebuilds this let the `dedup`/`prune` task run unthrottled and hold Postgres backends in lock contention for minutes at a time, blocking live queries. A Postgres-backed throttle survives restarts by construction, since it lives in the same durable store the rest of the state does.

## Recent schema additions

| Script | What it adds |
|---|---|
| `48_email_account_sync_status.sql` | `email_account.last_sync_error` / `last_sync_error_at` — the most recent failure message per account, cleared automatically on the next success. Added because a revoked Gmail token failed on every attempt for over two weeks with the only trace in container logs; `last_synced_at` showed the account was stale but never why. |
| `49_products.sql` | `personal.product` — products owned under an asset, with warranty window, vendor address string and investigation lifecycle. `tier` defaults to `secondary` so Asset stays the default-view entity and Product surfaces only on drill-down. |
| `49_graph_edge_indexes.sql` | Edge indexes for `personal_graph` traversal. |
| `50_google_tasks.sql` | `personal.event.task_status` (`open`/`done`) and a `channel` discriminator on `calendar_sync_map`, generalising it from calendar-mirror-only to "our event ↔ one external resource on some channel". Existing rows backfill to `gcal_mirror`, so mirror behaviour is untouched; tasks use `gtask_primary`. |

Two scripts share the `49_` prefix (`49_products.sql`, `49_graph_edge_indexes.sql`). They are independent, so either order works, but the next addition should not assume the prefix is unique.

## Initialisation

`postgres/init/` contains ordered SQL scripts that run **only on first container start**, against an empty data directory. Adding a file to that directory does not apply it to a database that already exists — a new migration has to be run by hand as well:

```bash
docker exec familybrain-postgres psql -U geoff -d openclaw -f /docker-entrypoint-initdb.d/<migration>.sql
```

Scripts in `postgres/migrations/` are never run automatically and always have to be applied this way. To run one from a path on the host, copy it in first with `docker cp`.

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
