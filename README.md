# OpenClaw — Self-Hosted Personal AI Stack

A fully self-hosted, multi-mode AI agent system built on a GMKtec Core Ultra 9 mini PC (96 GB RAM, Intel Arc GPU 48 GB VRAM, NPU). Runs entirely on-device — no cloud APIs, no data leaving the machine.

---

## What it does

OpenClaw continuously ingests your digital life — emails, files, calendar events, messages — classifies and enriches them with LLM extraction, stores structured knowledge in a graph database, and surfaces insights through a dashboard. It supports three operational modes (core, normal, podcast) that can be toggled without restarting the whole stack.

The core design principle is **adaptive self-improvement**: the system continuously learns from new information and updates its own state without manual intervention. Appointments evolve as context changes, bills auto-resolve when payment is detected, proactive reminders fire based on rules and real-world timelines, and extraction models refine themselves as they accumulate domain-specific ground truth.

---

## Adaptive Intelligence

OpenClaw is not a static knowledge store — it is designed to update its own representations as new information arrives. This section describes the key adaptive behaviours.

### Adaptive Appointment Management

Appointments are living entities. Their descriptions, priorities, and associated context are updated automatically as new information is ingested.

**How it works:**
- Every new email, file, or message is cross-referenced against existing `Event` nodes in `personal_graph`
- If new content is semantically related to an upcoming event (same person, same organisation, same topic), the event description is enriched with the new context
- As an appointment gets closer in time, the system applies **temporal reprioritisation** — events within 48 hours are surfaced more prominently; events that have passed are archived

**Example:** A GP appointment ingested in January has a description of "annual checkup." In February an email arrives with blood test results. In March a referral letter arrives. Each enriches the event node so that by the appointment date the description reads: "Annual checkup — blood test results on file (Feb), referral to cardiologist noted (Mar), bring Medicare card."

**Triggers for adaptive updates:**
- New document, email, or message linked to a known Person or Organisation associated with an event
- Direct keyword or entity match against event title or existing description
- LLM semantic similarity above a configurable threshold (`EVENT_ENRICH_THRESHOLD`, default `0.75`)

### Rules-Based Proactive Scheduling

OpenClaw applies a configurable rule engine to detected entities and dates to generate proactive follow-up events. Rules fire when new information is ingested and when periodic scans run.

**Built-in rule examples:**

| Trigger | Detected entity / date | Action |
|---------|------------------------|--------|
| Holiday / travel booking detected | Departure date | Check passport expiry. If expiry < 6 months before departure → create `Passport renewal` reminder at 12 months before departure and `Passport organised` checkpoint at 3 months before |
| Bill or invoice ingested | Due date | Create `Bill due` event. If payment later detected → auto-resolve to `Paid` |
| NDIS plan review date detected | Plan expiry | Create reminders at 3 months, 6 weeks, and 2 weeks before expiry |
| Insurance policy renewal | Renewal date | Create `Review insurance` reminder 6 weeks before |
| Prescription / medication noted | Duration or quantity | Create `Reorder medication` reminder when supply is estimated to run low |
| School term dates ingested | Term start/end | Auto-populate school calendar events for all children on file |

**Rule anatomy:**

```yaml
# Example rule — passport expiry check on holiday booking
name: passport_check_on_travel
trigger:
  entity_type: Event
  keywords: ["flight", "holiday", "travel", "departure", "hotel", "airbnb"]
condition:
  passport_expiry_months_before_departure: 6   # warn if expiry is < 6 months out
actions:
  - create_event:
      title: "Passport renewal required"
      offset_months: -12    # 12 months before departure
      priority: high
  - create_event:
      title: "Get passport organised"
      offset_months: -3     # 3 months before departure
      priority: medium
  - notify_whatsapp: true
```

Rules are stored in `personal.scheduling_rules` and can be added, modified, or disabled from the dashboard without restarting the stack.

### Auto-Resolving Bills

When a bill or invoice is ingested it creates a `Bill` node with status `unpaid`. The system watches subsequent inbound transactions (bank feeds, payment confirmation emails, receipts) and automatically marks matching bills as `paid`.

**Matching logic (applied in order):**
1. **Exact amount + payee name** — highest confidence, auto-resolves immediately
2. **Payee name + amount within 5%** — resolves after human confirmation (dashboard notification)
3. **Reference number match** — auto-resolves regardless of amount (useful for partial payments, fees)
4. **LLM semantic match** — for ambiguous cases; queued for human review

**Bill lifecycle:**

```
ingested → unpaid → [payment signal detected] → pending_confirmation → paid
                                                                      ↘ disputed
```

Bills that remain `unpaid` past their due date surface as `overdue` in the dashboard and generate a WhatsApp nudge.

### Auto-Training and Feedback Loops

The system is designed to improve extraction quality over time without manual retraining.

**Entity correction feedback:** When you correct a misclassified entity via the dashboard or WhatsApp ("that's not a person, it's a company"), the correction is written to `personal.extraction_feedback`. Periodically the curator agent uses this table to build a few-shot correction dataset and updates the extraction prompt templates.

**Confidence calibration:** Each extracted entity and claim carries a `confidence` score. When a human confirms or rejects an extraction, the system adjusts the confidence thresholds used for auto-commit vs. human-review routing. Over time the auto-commit rate increases for high-accuracy domains.

**Template evolution:** The `response_templates` table (seeded at init) is updated by the wa-agent whenever a response is edited or regenerated. Frequently edited templates are flagged for review and eventually replaced with the preferred variants.

**Schema reconciliation:** As new entity types emerge from ingested data, the graph-api's `/schemas/reconcile` endpoint proposes new node labels and properties. Accepted proposals are applied to the live schema and stored in `entity_schemas`.

### Three knowledge domains

| Domain | Schema | AGE Graph | What it captures |
|--------|--------|-----------|-----------------|
| **Personal** | `personal` | `personal_graph` | Family, NDIS care, household, appointments, personal notes |
| **Property** | `property_deals` | `property_graph` | Property listings, market research, financial analysis |
| **Decision** | `decision_architect` | `decision_graph` | Organisational frameworks, thought leadership, PR content |

---

## Architecture

```
Windows Host (Core Ultra 9)
├── Ollama (native)           ← LLM API on host, GPU-accelerated
├── OpenVINO Inference Server ← OpenVINO GenAI on Intel Arc GPU/NPU
│
└── WSL2 / Docker Compose
    ├── [core]   postgres          :5432   PostgreSQL + AGE + pgvector
    ├── [core]   dashboard         :3000   Next.js UI + Cypher console
    ├── [core]   audit-logger      :4000   Append-only audit log API
    ├── [core]   ingestor          :4001   File/email/event/message ingestion
    ├── [normal] n8n               :5678   Workflow orchestration
    ├── [normal] agents                    CrewAI PR/research agents
    ├── [normal] scraper                   Property listing scraper
    ├── [normal] email-sync                Gmail + Outlook/Hotmail sync
    ├── [normal] age-viewer        :8888   Graph explorer (Apache AGE Viewer)
    ├── [podcast] whisper          :9000   Whisper ASR (OpenAI-compatible)
    └── [podcast] tts              :5500   Piper TTS
```

### Data flow

```
File drop / Email / WhatsApp / Voice
         │
         ▼
    [Ingestor]
    ├── extract text (PDF / DOCX / TXT / CSV)
    ├── classify → personal / property / decision
    ├── embed → pgvector (nomic-embed-text)
    ├── store → PostgreSQL (schema-specific tables)
    │
    ├── Pass 1: qwen2.5:3b  — quick extraction, inline, writes graph immediately
    ├── Pass 2: qwen2.5:14b — rich extraction, background thread
    └── Pass 3: qwen2.5:32b — deep extraction, optional (EXTRACT_DEEPER_PASS=true)
         │
         ▼
    [AGE Graph]
    Nodes: Document, Concept, Person, Organisation, Claim, Framework, Theme,
           Message, Sender, Event
    Edges: MENTIONS, RELATES_TO, ASSERTS, FROM_FRAMEWORK, PART_OF, AUTHORED_BY,
           APPLIES_TO, SYNONYM_OF, ANTONYM_OF, RELATED_TO, LINKED_TO, FROM
```

---

## Hardware

| Component | Spec |
|-----------|------|
| CPU | Intel Core Ultra 9 185H |
| RAM | 96 GB DDR5 |
| GPU | Intel Arc 140T — 48 GB shared VRAM |
| NPU | Intel AI Boost (used for future NPU inference) |
| OS | Windows 11 Pro + WSL2/Ubuntu |

### Model assignments

| Model | Format | Device | Role |
|-------|--------|--------|------|
| qwen2.5:3b | OpenVINO INT4 | Intel Arc GPU | Fast extraction (Pass 1), classification |
| qwen2.5:14b | OpenVINO INT4 | Intel Arc GPU | Rich extraction (Pass 2) |
| qwen2.5:32b | OpenVINO INT4 | CPU/RAM | Deep extraction (Pass 3, optional) |
| nomic-embed-text | OpenVINO | Intel Arc GPU | Semantic embeddings (384-dim) |
| whisper-small | OpenVINO | CPU | Speech-to-text transcription |

Ollama also runs natively on Windows alongside the OpenVINO inference server — services route to whichever is available.

---

## Prerequisites

- Docker Desktop for Windows (WSL2 backend)
- WSL2 / Ubuntu
- [Ollama for Windows](https://ollama.com) — `winget install Ollama.Ollama`
- Python 3.11+ (for inference server)
- OpenVINO GenAI runtime (for inference server)
- Models converted to OpenVINO format (see [Model Conversion](#model-conversion))

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/youruser/openclaw.git
cd openclaw
cp .env.example .env
# Edit .env — fill in all required secrets (see Environment Variables section)
```

### 2. Create the data directory (Windows)

```powershell
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\personal
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\property
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\decision
New-Item -ItemType Directory -Force C:\DataFiles\Processing
New-Item -ItemType Directory -Force C:\DataFiles\Ingested
```

### 3. Start the inference server (Windows, run once at startup)

```powershell
cd openclaw\inference-server
& ".\start.bat"
```

Or run it in the background as a Windows startup task.

### 4. Pull Ollama models

```bash
ollama pull qwen2.5:3b
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
```

### 5. Start the core stack

```bash
docker compose --profile core up -d
```

### 6. Start additional profiles as needed

```bash
# Full normal mode (agents, scraper, email sync, graph viewer)
docker compose --profile normal up -d

# Podcast mode (voice transcription + TTS)
docker compose --profile podcast up -d
```

---

## Profiles

| Profile | Services | Use case |
|---------|----------|----------|
| `core` | postgres, dashboard, audit-logger, ingestor | Always running — minimum viable stack |
| `normal` | + n8n, agents, scraper, email-sync, age-viewer | Day-to-day operation |
| `podcast` | + whisper, tts, podcast-agents | Voice and podcast workflows |

Profiles can be combined: `docker compose --profile normal --profile podcast up -d`

---

## Services

### Postgres (`postgres:5432`)

PostgreSQL 16 with three extensions:
- **Apache AGE 1.6.0** — graph layer (Cypher queries over relational data)
- **pgvector** — semantic similarity search
- **pg_trgm** — fuzzy text matching

Three logical schemas map to three AGE graphs:

| Schema | Graph | Tables |
|--------|-------|--------|
| `personal` | `personal_graph` | note, event, email_message, email_account, calendar_sync_map |
| `property_deals` | `property_graph` | scraped_listing, property, analysis |
| `decision_architect` | `decision_graph` | theme, content_item, framework |

Role model: each service has a dedicated Postgres role with minimum required permissions. `dashboard_ro` is read-only. `curator` can write to all schemas. `audit_writer` can only INSERT to `audit.log`.

`session_preload_libraries = 'age'` is set globally so any role can use Cypher without superuser `LOAD 'age'`.

### Dashboard (`dashboard:3000`)

Next.js application providing:
- **Mode switcher** — toggle between core/normal/podcast
- **Audit log viewer** — real-time feed of all agent activity
- **Cypher console** — run Cypher queries against any of the three graphs
  - Read-only by default; set `allowWrites: true` in the API call to enable writes
  - Parses the `RETURN` clause to build the AGE column definition list automatically
  - Uses `$cypher$...$cypher$` dollar-quoting to avoid conflicts with psycopg2's `$1` syntax

### Audit Logger (`audit-logger:4000`)

Lightweight HTTP service (FastAPI) that all agents POST to for every significant action. Writes to `audit.log` which is append-only (UPDATE/DELETE revoked at DB level). Provides a queryable history for the dashboard.

### Ingestor (`ingestor:4001`)

Watches `C:\DataFiles\ReadyToIngest\` and ingests any supported file dropped there. Also exposes webhook endpoints for programmatic ingestion.

**File watching**: polls every 15 seconds (configurable via `INGEST_SCAN_INTERVAL`). Uses directory polling rather than inotify because WSL2's 9P filesystem does not propagate Windows-side `inotify` events reliably.

**Supported file types**: `.pdf`, `.docx`, `.doc`, `.txt`, `.md`, `.csv`

**Folder routing**:
- `ReadyToIngest/personal/` → personal schema
- `ReadyToIngest/property/` → property schema
- `ReadyToIngest/decision/` → decision schema
- `ReadyToIngest/` (root drop) → auto-classified by LLM

**File lifecycle**: `ReadyToIngest/` → `Processing/` → `Ingested/<schema>/`

**Webhook endpoints**:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ingest/email` | Email payload from email-sync |
| `POST` | `/ingest/event` | Calendar event (any source) |
| `POST` | `/ingest/message` | Generic inbound (WhatsApp, SMS, voice) |
| `POST` | `/ingest/observation` | Approved CommentOS items |
| `GET` | `/health` | Liveness check |
| `GET` | `/scan` | Force immediate scan of ReadyToIngest |

**Multi-pass extraction**:

| Pass | Model | Prompt | Mode | Schema |
|------|-------|--------|------|--------|
| 1 (Quick) | qwen2.5:3b | Simple — concepts, people, orgs, claims | Inline (blocks) | Flat lists |
| 2 (Deep) | qwen2.5:14b | Rich — adds frameworks, relationships | Background thread | Full schema |
| 3 (Deeper) | qwen2.5:32b | Rich — same prompt as Pass 2 | Background (opt-in) | Full schema |

Pass 3 is disabled by default. Enable via `EXTRACT_DEEPER_PASS=true` in docker-compose env.

**Graph nodes written per document**:
- `Document` — filename, schema, preview
- `Concept` — name, description; optionally linked to `Framework` via `PART_OF`
- `Person` — name, description; documents linked via `AUTHORED_BY` if `is_author=true`
- `Organisation` — name, description
- `Claim` — text, significance, confidence, framework; linked to `Framework` via `APPLIES_TO`
- `Framework` — name, description, domain (e.g. Agile, Six Sigma, ADKAR, ISO 31000, WH&S)
- `Theme` — links document to its classification theme

**Graph edges written**:
- `MENTIONS` — Document → Concept/Person/Organisation
- `ASSERTS` — Document → Claim
- `RELATES_TO` — Document → Theme
- `FROM_FRAMEWORK` — Document → Framework
- `AUTHORED_BY` — Document → Person
- `PART_OF` — Concept → Framework
- `APPLIES_TO` — Claim → Framework
- `SYNONYM_OF` — Concept → Concept (same idea, different terminology across frameworks)
- `ANTONYM_OF` — Concept → Concept (opposing concepts)
- `RELATED_TO` — Concept → Concept (general relationship)

### Email Sync (`email-sync`, internal)

Syncs Gmail and Outlook/Hotmail inboxes on a configurable poll interval. See [email-sync/SETUP.md](email-sync/SETUP.md) for full OAuth2 setup instructions.

- Deduplication via `personal.email_message` — each message ingested exactly once
- Gmail uses History API for incremental sync; Outlook uses delta queries
- Calendar sync is bidirectional between Gmail and Outlook via `personal.calendar_sync_map`
- New emails are POSTed to the ingestor's `/ingest/email` webhook

### n8n (`n8n:5678`)

Workflow automation. Stores config in `personal.n8n_*` tables (same Postgres instance). Used for webhook triggers, scheduled tasks, and connecting external services.

### Scraper (`scraper`, internal)

Scrapes property listings from Domain.com.au on a configurable cron schedule. Stores raw listings in `property_deals.scraped_listing` and runs deduplication every 15 minutes. Configurable via env vars: `SCRAPE_SUBURBS`, `SCRAPE_MIN_BEDS`, `SCRAPE_MAX_PRICE`.

### Agents (`agents`, internal)

CrewAI-based multi-agent system. Currently implements a PR/thought leadership writer that runs on a configurable daily schedule (`PR_CRON_TIME`, default 07:00). Reads from `decision_architect` schema and writes polished content drafts.

### AGE Viewer (`age-viewer:8888`)

Visual graph explorer for Apache AGE. Based on `joefagan/incubator-age-viewer` with two patches applied at build time:

1. **Metadata queries** — replaced slow full-graph Cypher traversal (`MATCH ()-[V]-()`) with instant `pg_catalog` lookups using `ag_label` + `pg_class.reltuples`. The original query times out on graphs with thousands of edges.
2. **`graphid` fix** — AGE 1.4+ renamed `oid` to `graphid` in `ag_catalog.ag_graph`. The upstream image still uses `oid`.

**Connection details** (enter in the viewer's connection form):
- Host: `postgres`
- Port: `5432`
- Database: `openclaw`
- User: `geoff` (or your superuser name from `POSTGRES_SUPERUSER`)
- Graph: `personal_graph` / `property_graph` / `decision_graph`
- Flavor: **Apache AGE** (not AgensGraph)

### WhatsApp Bridge (`whatsapp:3002`)

Connects to WhatsApp using `whatsapp-web.js` (unofficial, no Meta Business API required). You scan a QR code once; the session is persisted in a Docker volume so subsequent restarts don't require re-scanning.

**Setup:**
1. Start the normal profile: `docker compose --profile normal up -d`
2. Open http://localhost:3002/qr in a browser
3. On your phone: WhatsApp → Settings → Linked Devices → Link a Device → scan the QR code
4. The page auto-refreshes and shows "✅ WhatsApp connected" when done

To restrict which numbers can talk to the bot, set `WA_ALLOWED_NUMBERS` in `.env` (comma-separated, E.164 format, e.g. `+61412345678,+61498765432`). Leave empty to allow all numbers.

If the session expires (rare with multi-device), just re-scan at http://localhost:3002/qr.

### WhatsApp Agent (`wa-agent:4002`)

Receives messages from the WhatsApp bridge, routes to the right knowledge graph(s), retrieves context, and generates a response.

**Query routing** (multi-layer, fast to slow):

1. **Keyword match** — instant, no LLM call
   - Property keywords (house, suburb, listing, mortgage…) → `property_graph`
   - Decision keywords (agile, adkar, framework, linkedin…) → `decision_graph`
   - Personal keywords (ndis, appointment, school, family…) → `personal_graph`
2. **Multi-domain** — if keywords span domains, all matching graphs are searched
3. **LLM classification** — for ambiguous messages with no keywords, the LLM classifies
4. **Default** — falls back to `personal_graph`

**Retrieval per graph:**
- Embed the query with `nomic-embed-text`
- Vector similarity search against the primary text table (`personal.note`, `property_deals.property`, `decision_architect.theme`)
- Supplementary: upcoming calendar events (personal), frameworks (decision)
- Cypher: fetch Concepts and high-confidence Claims linked to top-matched Documents

**Conversation memory:** last 6 turns (3 user + 3 assistant) are kept per sender in memory so follow-up questions work naturally. History is cleared on container restart, or via `DELETE /history/{sender}`.

**Example conversations:**

```
You: What's on this week?
Bot: You have 3 events this week: school pickup Tuesday 3pm, medical appointment 
     Thursday 10am, and Shannon's birthday on Saturday...

You: Any NDIS reviews coming up?
Bot: I found 2 NDIS-related notes. Your plan review is due in March 2026...

You: What does ADKAR say about resistance to change?
Bot: ADKAR addresses resistance in the Desire phase — it distinguishes between 
     lack of awareness (handled in phase 1) and active resistance...

You: how does that compare to Kotter's 8-step model?
Bot: Both models treat resistance as a communication failure. Kotter's step 4 
     (enlist a volunteer army) maps roughly to ADKAR's Desire phase...
```

### OpenVINO Inference Server (Windows host, not Docker)

FastAPI server providing Ollama-compatible API endpoints (`/api/generate`, `/api/embeddings`, `/v1/audio/transcriptions`) backed by OpenVINO GenAI models running on the Intel Arc GPU.

Config: [`inference-server/models.yaml`](inference-server/models.yaml)

GPU throttle is controllable via the `GPU_QUEUE_THROTTLE` environment variable (`LOW` / `MEDIUM` / `HIGH`, default `MEDIUM`).

OpenAI-compatible transcription endpoint:
```
POST http://localhost:11435/v1/audio/transcriptions
Content-Type: multipart/form-data
file=<audio.wav>
model=whisper-small
language=en   (optional)
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in:

```env
# Postgres
POSTGRES_SUPERUSER=geoff
POSTGRES_SUPERUSER_PASSWORD=<required>
DASHBOARD_DB_PASSWORD=<required>
AUDIT_DB_PASSWORD=<required>
N8N_DB_PASSWORD=<required>
SCRAPER_DB_PASSWORD=<required>
AGENTS_DB_PASSWORD=<required>
CURATOR_DB_PASSWORD=<required>
PODCAST_DB_PASSWORD=<required>

# n8n
N8N_ENCRYPTION_KEY=<required>
N8N_WEBHOOK_URL=http://localhost:5678

# Google OAuth2 (Gmail + Calendar)
GOOGLE_CLIENT_ID=<required>
GOOGLE_CLIENT_SECRET=<required>

# Microsoft OAuth2 (Outlook/Hotmail)
MICROSOFT_CLIENT_ID=<required>
MICROSOFT_TENANT_ID=consumers

# Model routing (defaults shown)
AGENT_MODEL=qwen2.5:3b
EMBED_MODEL=nomic-embed-text
EXTRACT_MODEL_QUICK=qwen2.5:3b
EXTRACT_MODEL_DEEP=qwen2.5:14b
EXTRACT_MODEL_DEEPER=qwen2.5:32b
EXTRACT_DEEP_PASS=true
EXTRACT_DEEPER_PASS=false

# Ingestor
INGEST_SCAN_INTERVAL=15          # seconds between ReadyToIngest polls

# Scraper
SCRAPE_SUBURBS=Brisbane,QLD
SCRAPE_MIN_BEDS=3
SCRAPE_MAX_PRICE=1500000
SCRAPE_CRON=0 */6 * * *
DEDUP_CRON=*/15 * * * *

# Agents
PR_CRON_TIME=07:00
PR_RUN_ON_START=false
```

---

## Model conversion

All models run as OpenVINO IR format. Convert from HuggingFace with `optimum-cli`:

```bash
# qwen2.5:3b INT4
optimum-cli export openvino \
  --model Qwen/Qwen2.5-3B-Instruct \
  --weight-format int4 \
  C:\Users\Glenn\qwen2.5-3b-ov

# qwen2.5:14b INT4
optimum-cli export openvino \
  --model Qwen/Qwen2.5-14B-Instruct \
  --weight-format int4 \
  C:\Users\Glenn\qwen2.5-14b-ov

# nomic-embed-text (bge-base compatible, 384-dim)
optimum-cli export openvino \
  --model nomic-ai/nomic-embed-text-v1 \
  C:\Users\Glenn\embed-ov

# whisper-small
optimum-cli export openvino \
  --model openai/whisper-small \
  C:\Users\Glenn\whisper-small-ov
```

---

## Graph schema

### Node labels

| Label | Key properties |
|-------|---------------|
| `Document` | `filename`, `row_id`, `schema`, `preview` |
| `Concept` | `name`, `description` |
| `Person` | `name`, `description` |
| `Organisation` | `name`, `description` |
| `Claim` | `claim_id`, `text`, `significance`, `confidence`, `framework` |
| `Framework` | `name`, `description`, `domain` |
| `Theme` | `theme_id` |
| `Message` | `source`, `source_id`, `from_handle`, `from_name`, `subject`, `received_at`, `preview` |
| `Sender` | `handle`, `name`, `source` |
| `Event` | `event_key`, `title`, `starts_at`, `ends_at`, `event_type`, `calendar_source`, `description`, `enriched_at`, `priority` |
| `Bill` | `bill_id`, `payee`, `amount`, `due_date`, `status` (`unpaid`/`paid`/`overdue`/`disputed`), `reference`, `resolved_at` |
| `SchedulingRule` | `rule_id`, `name`, `trigger_keywords`, `condition_json`, `actions_json`, `enabled` |

### Edge types

| Edge | From → To | Properties |
|------|-----------|------------|
| `MENTIONS` | Document → Concept/Person/Organisation | — |
| `ASSERTS` | Document → Claim | — |
| `RELATES_TO` | Document → Theme | — |
| `FROM_FRAMEWORK` | Document → Framework | — |
| `AUTHORED_BY` | Document → Person | — |
| `PART_OF` | Concept → Framework | — |
| `APPLIES_TO` | Claim → Framework | — |
| `SYNONYM_OF` | Concept → Concept | `notes` |
| `ANTONYM_OF` | Concept → Concept | `notes` |
| `RELATED_TO` | Concept → Concept | `notes` |
| `LINKED_TO` | Message → Document | — |
| `FROM` | Message → Sender | — |
| `ENRICHES` | Document/Message → Event | `confidence`, `enriched_at` |
| `TRIGGERED` | Event → Event | `rule_id`, `rule_name` (proactive follow-up events) |
| `RESOLVES` | Document/Message → Bill | `match_type`, `confidence`, `resolved_at` |

### Example Cypher queries

```cypher
-- All frameworks and how many concepts belong to each
MATCH (c:Concept)-[:PART_OF]->(f:Framework)
RETURN f.name AS framework, count(c) AS concepts
ORDER BY concepts DESC

-- Synonym chains between Agile and Six Sigma concepts
MATCH (a:Concept)-[:SYNONYM_OF]->(b:Concept)
MATCH (a)-[:PART_OF]->(fa:Framework)
MATCH (b)-[:PART_OF]->(fb:Framework)
WHERE fa.name <> fb.name
RETURN a.name, fa.name, b.name, fb.name

-- Recent documents and their key claims
MATCH (d:Document)-[:ASSERTS]->(c:Claim)
WHERE c.confidence = 'high'
RETURN d.filename, c.text, c.significance
LIMIT 20

-- People who authored multiple frameworks
MATCH (d:Document)-[:AUTHORED_BY]->(p:Person)
MATCH (d)-[:FROM_FRAMEWORK]->(f:Framework)
RETURN p.name, collect(DISTINCT f.name) AS frameworks
ORDER BY size(frameworks) DESC
```

---

## Port map

See [PORT_MAP.md](PORT_MAP.md).

---

## Known issues and workarounds

### WSL2 filesystem events
WSL2's 9P filesystem does not propagate Windows-side `inotify` events to Docker containers. The ingestor uses directory polling instead of `watchdog`. If you need an immediate scan without waiting for the 15-second interval, hit the `/scan` endpoint:

```powershell
Invoke-WebRequest http://localhost:4001/scan
```

### `docker compose up` recreating postgres
If postgres gets recreated (e.g. because `docker-compose.yml` was edited), the AGE Viewer's connection pool becomes stale. Restart the viewer:

```bash
docker compose restart age-viewer
```

Always use `--no-deps` when restarting a single service to avoid cascading recreates:

```bash
docker compose up -d --no-deps ingestor
docker compose up -d --no-deps age-viewer
```

### AGE Viewer metadata timeout
The upstream AGE Viewer uses `MATCH ()-[V]-()` for edge counts which scans every edge bidirectionally. This patch replaces it with `pg_catalog` lookups. If you pull a new upstream image, rebuild:

```bash
docker compose build age-viewer
docker compose up -d --no-deps age-viewer
```

### OpenVINO `GPU_UTILIZATION_HINT` error
`GPU_UTILIZATION_HINT` is not a valid OpenVINO property (causes "Option not found" crash). Use `GPU_QUEUE_THROTTLE` instead: `LOW` | `MEDIUM` | `HIGH`.

---

## Roadmap

### Infrastructure
- [x] WhatsApp chat interface with graph-routed knowledge retrieval
- [ ] Stage 7: Curator agent — cross-schema review queue, staging table, dashboard approval UI
- [ ] Stage 8: Mode switching API + WhatsApp/n8n integration
- [ ] Stage 9: Voice services — podcast recording, live transcription, TTS response
- [ ] qwen2.5:14b INT4 OpenVINO conversion
- [ ] phi3.5-mini INT4 for NPU-based classification
- [ ] Inference server as Windows startup service (currently manual via `start.bat`)
- [ ] Enable Pass 3 (32b) via `EXTRACT_DEEPER_PASS=true` once model is ready
- [ ] Azure app registration for Outlook email sync (refresh token flow)

### Adaptive Intelligence
- [ ] Adaptive appointment enrichment — `ENRICHES` edge written when new docs/messages relate to existing `Event` nodes; event description updated in-place
- [ ] Temporal reprioritisation — event priority escalation as appointment date approaches (configurable windows: 2 weeks / 48 hours)
- [ ] Rules engine — `personal.scheduling_rules` table, rule evaluator runs on ingest and on daily cron; seed with built-in passport/holiday/NDIS/insurance rules
- [ ] Proactive event generation — passport renewal chain (12 months + 3 months before travel), NDIS review reminders, medication reorder, school calendar auto-population
- [ ] Bill auto-resolution — `Bill` node status lifecycle; payment signal matching (exact → fuzzy → reference → LLM); overdue nudge via WhatsApp
- [ ] Bank feed / payment confirmation ingestion — parse transaction emails (CBA, ANZ, NAB patterns), match against open bills
- [ ] Entity correction feedback loop — dashboard correction UI writes to `personal.extraction_feedback`; curator uses this to refine few-shot extraction prompts
- [ ] Confidence calibration — human confirm/reject signals adjust auto-commit thresholds per entity type
- [ ] Response template evolution — wa-agent tracks edited/regenerated responses; curator flags low-quality templates for replacement
