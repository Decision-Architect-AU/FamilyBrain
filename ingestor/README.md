# ingestor

Central ingestion service. Accepts documents from all channels and routes them into the knowledge base.

## What it does

- Watches `C:\DataFiles\ReadyToIngest\` for dropped files (PDF, DOCX, TXT, images, spreadsheets)
- Accepts webhook payloads from email-sync, WhatsApp, and other services
- Classifies each item into a schema (`personal` / `property` / `decision`)
- Writes a structured row to the appropriate Postgres table and a note embedding to pgvector
- Runs multi-pass LLM concept extraction and writes typed nodes to the AGE graph
- Detects asset-type entities (vehicles, medications, subscriptions, pets, etc.) and upserts them into `personal.asset`, then fires rule_watcher to generate any due events
- Runs notification detectors and rule evaluation on demand

## Ports

| Port | Purpose |
|------|---------|
| `4001` | HTTP API |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ingest/email` | Email payload from email-sync (inbox or sent) |
| `POST` | `/ingest/event` | Calendar event from any source |
| `POST` | `/ingest/message` | Generic inbound message (WhatsApp, voice, SMS) |
| `POST` | `/ingest/observation` | Approved observation item |
| `GET`  | `/scan` | Force immediate scan of ReadyToIngest directory |
| `GET`  | `/api/notifications` | Active notifications (DETECTED / TRIAGED / PENDING) |
| `GET`  | `/api/assets` | All tracked assets with rule counts |
| `GET`  | `/api/assets/:id/dossier` | Full dossier for one asset — facts, factsrc, summary, neighbourhood sections by edge type, events, routine participation. Query param `include_suppressed=1` to include zeroed edges. |
| `POST` | `/api/edges/suppress` | Zero an edge's confidence (`{edge_id, reason}`) — durable against re-ingestion, enqueues fact re-derivation for any fact sourced from it |
| `POST` | `/api/edges/restore` | Restore a suppressed edge to its pre-zero confidence (`{edge_id}`) |
| `GET`  | `/api/events/pending-sync` | Rule-generated events awaiting Google Calendar sync |
| `POST` | `/api/events/mark-synced` | Mark an event synced with its gcal_event_id |
| `POST` | `/notifications/run-detectors` | Trigger a full notification detector sweep |
| `POST` | `/notifications/run-rules` | Trigger rule_watcher across all assets |

## Multi-pass extraction

| Pass | Model | Mode | Extracts |
|------|-------|------|---------|
| 1 — Quick | qwen2.5:3b | Inline | Concepts, people, organisations, claims |
| 2 — Deep | qwen2.5:14b | Background thread | Full schema, frameworks, relationships |
| 3 — Deeper | qwen2.5:32b | Background, opt-in | Structured data-dense documents (spreadsheets) |

Enable passes via env vars: `EXTRACT_DEEP_PASS=true`, `EXTRACT_DEEPER_PASS=true`.

## Asset routing

After any personal-schema ingest, `asset_router.py` runs in a background thread:
1. Asks the LLM to classify entity type and extract structured fields
2. Calls `classify_for_asset()` → `upsert_asset()` → `trigger_rules_for_asset()`
3. Fires for all channels (file, email, WhatsApp) — no per-channel wiring needed

## Key source files

| File | Purpose |
|------|---------|
| `src/main.py` | HTTP server, file watcher, ingest routing |
| `src/ingest.py` | Schema-specific Postgres writers |
| `src/classify.py` | LLM schema classifier |
| `src/triage.py` | Fast email gate (keyword rules → LLM fallback) |
| `src/extract_concepts.py` | Multi-pass LLM concept extraction |
| `src/graph.py` | AGE graph node writers |
| `src/asset_router.py` | Channel-agnostic asset detection and upsert |
| `src/asset_writer.py` | personal.asset upsert + default rule generation |
| `src/asset_classifier.py` | Entity type → asset route classification |
| `src/rule_watcher.py` | Asset rule evaluation and event generation |
| `src/notification_detectors.py` | Collision, health, staleness, pattern gap, action detectors |
| `src/graph.py` | Also: `zero_edge()` / `restore_edge()` (suppression), `get_asset_neighbourhood()` (dossier), `is_user_zeroed()` (re-ingestion guard) |

## Edge suppression and the dossier

Every edge written to `personal_graph` carries `confidence INT` (0–100). Suppressing an edge (`zero_edge()` in `graph.py`) sets `confidence = 0` plus `zeroed_by` / `zeroed_at` / `zero_reason` / `zero_prev_confidence` — it never deletes the edge or either node. A `zeroed_by='user'` edge is permanent: any writer that would otherwise create/rescore a MENTIONS/NOTE/etc. edge between the same pair must call `is_user_zeroed()` first and skip if true. System-zeroed edges (`zeroed_by='system'`) may still be re-scored.

`get_asset_neighbourhood(asset_id)` — **always match by `ref` (`"personal.asset:{id}"`), never by an `asset_id` property.** `asset_id` is only ever set by `write_asset_node()` (ingestion-path assets); assets synced solely via wa-agent's `task_asset_graph_sync` never get it set. `ref` is the universal MERGE key on both write paths. The function runs two directed, `:Asset`-labeled queries (`-[r]->` and `<-[r]-`) rather than one unlabeled undirected `MATCH` — AGE has no index support for an unlabeled undirected traversal and it will scan every vertex/edge label table in the graph (verified: this hung for 2+ hours against a ~350k-edge graph before being fixed).

## Environment variables

```env
DATABASE_URL=postgresql://curator:<password>@postgres:5432/familybrain
AUDIT_SERVICE_URL=http://audit-logger:4000
OLLAMA_URL=http://172.23.96.1:11434
AGENT_MODEL=qwen2.5:3b
EMBED_MODEL=nomic-embed-text
EXTRACT_MODEL_QUICK=qwen2.5:3b
EXTRACT_MODEL_DEEP=qwen2.5:14b
EXTRACT_MODEL_DEEPER=qwen2.5:32b
EXTRACT_DEEP_PASS=false
EXTRACT_DEEPER_PASS=false
INGEST_WATCH_DIR=/data/ReadyToIngest
```
