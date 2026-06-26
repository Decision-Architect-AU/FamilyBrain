# OpenClaw — Self-Hosted Personal AI Stack

A fully self-hosted, multi-mode AI agent system built on a GMKtec Core Ultra 9 mini PC (96 GB RAM, Intel Arc GPU 48 GB VRAM, NPU). Runs entirely on-device — no cloud APIs, no data leaving the machine.

---

## What it does

OpenClaw continuously ingests your digital life — emails, files, calendar events, messages — classifies and enriches them with LLM extraction, stores structured knowledge in a graph database, and surfaces insights through a dashboard and WhatsApp agent. It supports three operational modes (core, normal, podcast) that can be toggled without restarting the whole stack.

The core design principle is **adaptive self-improvement**: the system continuously learns from new information and updates its own state without manual intervention. Appointments evolve as context arrives, bills auto-resolve when payment is detected, proactive reminders fire based on channel rules and real-world timelines, and the graph accumulates rich typed facts that make every future answer more precise.

---

## Architecture

### System layout

```
Windows Host (Core Ultra 9)
├── OpenVINO Inference Server  ← OpenVINO GenAI on Intel Arc GPU + NPU
│     ├── nomic-embed-text (NPU)        768-dim semantic embeddings
│     ├── ms-marco-reranker (NPU)       Cross-encoder result reranking
│     ├── qwen2.5:3b  (AUTO:GPU,CPU)    Fast classification
│     ├── qwen2.5:14b (GPU)             Primary extraction
│     ├── qwen2.5:32b (GPU,CPU)         Deep reasoning (optional)
│     └── whisper-small (CPU)           Speech-to-text
│
└── WSL2 / Docker Compose
    ├── [core]   postgres          :5432   PostgreSQL + AGE + pgvector
    ├── [core]   dashboard         :3000   Next.js UI + Cypher console
    ├── [core]   audit-logger      :4000   Append-only audit log API
    ├── [core]   ingestor          :4001   File/email/event/message ingestion
    ├── [normal] n8n               :5678   Workflow orchestration
    ├── [normal] agents                    CrewAI PR/research agents
    ├── [normal] scraper                   Property listing scraper
    ├── [normal] email-sync                Gmail + Outlook sync + appointment updater
    ├── [normal] age-viewer        :8888   Graph explorer (Apache AGE Viewer)
    ├── [podcast] whisper          :9000   Whisper ASR (OpenAI-compatible)
    └── [podcast] tts              :5500   Piper TTS
```

### Pipeline architecture

The pipeline is split into three layers: **inbound channels**, a **centralised knowledge core**, and **outbound channels**. Each layer is independent — new sources and destinations plug in without touching the others.

```
┌─────────────────────────────────────────────────────────┐
│  INBOUND CHANNELS  (rules per channel)                  │
│                                                         │
│  Gmail inbox ──┐                                        │
│  Outlook inbox ┤                                        │
│  Voice notes   ├──► Normalise ──► email_message / note  │
│  File drop     ┤                                        │
│  Manual entry  ┘                                        │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  EMAIL DECOMPOSER  (LLM, qwen2.5:14b)                   │
│                                                         │
│  One email → N typed items:                             │
│    calendar_event  → personal.event                     │
│    payment         → financial_doc note → bill_calendar │
│    observation     → personal.note                      │
│    task            → personal.note (tagged)             │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  KNOWLEDGE CORE                                         │
│                                                         │
│  personal.event          effective_date, updated_at,    │
│  personal.note           next_update_at, gcal_event_id  │
│  personal.email_message                                 │
│                                                         │
│  AGE graph  (:Event, :Bill, :Person, :Document …)       │
│  pgvector   semantic embeddings                         │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  CHANNEL RULES  (personal.channel_rule)                 │
│                                                         │
│  item_type + category → schedule → next_update_at       │
│                                                         │
│  payment   → before_event:3d  (3 days before due)       │
│  family    → immediate                                  │
│  holiday   → immediate + day expansion                  │
│  task      → immediate                                  │
│  observation → batch:daily:07:00                        │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  APPOINTMENT UPDATER  (outbound scheduler)              │
│                                                         │
│  Polls: WHERE next_update_at <= now()                   │
│      OR gcal_event_id IS NULL                           │
│      OR updated_at > calendar_written_at                │
│                                                         │
│  Routes to target calendar (Bills / Family / Holidays / │
│  Primary) then sets next scheduled re-check or NULL.    │
│                                                         │
│  OUTBOUND CHANNELS:                                     │
│  gcal_bills ──► Bills calendar                          │
│  gcal_family ──► Family calendar (Child1/Child2 tags)   │
│  gcal_holidays ──► Holidays + daily expansion           │
│  gcal_primary ──► Primary calendar                      │
│  task_list ──► notes (tagged task)                      │
│  observations ──► notes (daily batch)                   │
└─────────────────────────────────────────────────────────┘
```

---

## Events as First-Class Citizens

### The philosophy

Life is driven by events. Not just appointments — every meaningful moment in a household has a date, a state, and a consequence.

A bill is an event. The moment it is paid is an event. A prescription is an event with a renewal date. A school holiday is an event that reshapes every other event around it. A travel booking is an event that triggers a chain of downstream events — passport check, insurance review, pet care reminder. A GP referral is an event that creates a specialist appointment, which creates a pathology order, which creates a results review. A salary payment is an event. An insurance renewal is an event. A child's therapy block is a recurring event with a funding expiry.

Most personal AI systems treat calendars as one data source among many. OpenClaw inverts this: **the event is the atomic unit of life, and everything else is context that enriches it.**

This means:

| What it looks like | What it is in OpenClaw |
|---|---|
| A bill arriving | `:Event { event_type: "bill", status: "unpaid" }` |
| A payment made | `:Event { event_type: "payment" }` + `RESOLVES` edge to bill |
| A prescription | `:Event { event_type: "medication", fact_supply: "30 days" }` |
| A doctor appointment | `:Event { event_type: "medical", fact_provider: "…", fact_notes: "…" }` |
| A placeholder ("remember to check X") | `:Event { event_type: "task" }` |
| A school holiday | `:Event { event_type: "holiday" }` + individual day events via `TRIGGERED` |
| A travel booking | `:Event { event_type: "booking" }` + passport/insurance reminders via `TRIGGERED` |
| A funding plan expiry | `:Event { event_type: "review" }` + reminders at 3m, 6w, 2w via `TRIGGERED` |
| An insurance renewal | `:Event { event_type: "renewal" }` + `Review insurance` reminder via `TRIGGERED` |

Every one of these lives in the same graph, shares the same enrichment pipeline, and flows through the same outbound channel rules to the right calendar. The model does not distinguish between "important" and "administrative" — it honours them equally, because all of them have dates that matter to someone.

### Contextual updating — why the event model matters

By treating everything as an event connected to a person and a time, the system can do something no calendar app can: **enrich a future appointment with everything that has happened since the last one.**

When a medical appointment is approaching, the nightly enrichment sweep traverses the graph outward from the `:Event` node — following `ENRICHES` edges to find every document, message, and note linked to that person over the past 3, 6, or 12 months. Pathology results, specialist letters, prescription changes, therapy progress notes, school reports, NDIS review documents — anything the system has ingested that is connected to the same person becomes context for the upcoming appointment.

The result is an appointment description that arrives in the calendar already briefed:

```
Child1 Paediatrician — 6-month review
Provider: Dr J Smith, City Paediatrics
What's changed since last visit (6 months):
  • Medication dose adjusted Feb 2026 (letter from prescriber)
  • OT progress note Mar 2026 — fine motor goals met, sensory processing ongoing
  • School report May 2026 — teacher flagged attention difficulties in afternoon sessions
  • NDIS plan review due Aug 2026 — funding category breakdown attached
Bring: referral (expires Jul 2026), Medicare card, previous reports
Referral needed: current referral expires before appointment — reminder created
```

None of this was manually assembled. The graph knew the person, knew the appointment, and knew what had happened in between.

This same pattern applies across every event type:

- **Financial review** — enriched with the last 12 months of bills, payments, and outstanding items for that entity
- **Insurance renewal** — enriched with any claims, correspondence, or policy changes since last renewal
- **Specialist appointment** — enriched with the GP referral that triggered it, any pre-appointment pathology ordered, and the last visit summary
- **Medication review** — enriched with dispensing history, dose change letters, and any side-effect notes

The system also generates **proactive reminders** when it detects that an appointment requires something that hasn't arrived yet — a referral that is expiring, a pathology order with no results, a pre-appointment form that was sent but not returned. These become `TRIGGERED` events in their own right, routed to the calendar days or weeks before the appointment.

### What makes an event

An event in OpenClaw is more than a calendar entry. It is a **living knowledge node** in the AGE graph with typed facts attached:

```
:Event {
  event_key        — stable dedup key (source:id)
  title            — enriched display title (e.g. "Child1 Physio — bring report")
  starts_at        — TIMESTAMPTZ
  ends_at          — TIMESTAMPTZ
  effective_date   — DATE in local timezone (no UTC drift on all-day events)
  event_type       — medical | bill | family | holiday | task | appointment | …
  gcal_event_id    — Google Calendar event ID after outbound write
  next_update_at   — when the appointment updater should revisit this event
  facts_updated_at — last time fact_* properties were written

  fact_location    — resolved venue or address
  fact_provider    — practitioner or organisation name
  fact_notes       — preparation instructions, what to bring
  fact_cost        — expected cost or Medicare gap
  fact_duration    — expected duration
  ref              — "personal.event:{row_id}" — hydration handle back to Postgres
}
```

Facts (`fact_*` properties) are written by the graph enrichment pipeline and are never truncated — they carry the full extracted content. The `ref` field is a hydration handle that lets any service look up the full Postgres row without duplicating relational data in the graph.

### Event lifecycle

```
Email / file arrives
        │
        ▼
Email decomposer (qwen2.5:14b)
  ├── Extracts: title, date, location, provider, notes
  ├── Writes: personal.event row
  └── Writes: :Event node in personal_graph
              with fact_* properties from extraction

        │
        ▼
Scheduler maintenance (nightly, before queue drain)
  ├── Dedup:   merge duplicate Event nodes by event_key
  ├── Enrich:  find documents/messages linked to each Event
  │            via ENRICHES edges, update fact_* properties
  └── Promote: escalate priority as effective_date approaches

        │
        ▼
Appointment updater (polls next_update_at <= now())
  ├── Reads enriched facts from graph node
  ├── Builds enriched title + description from fact_* props
  ├── Routes to correct Google Calendar
  └── Sets next_update_at or NULL (past events)
```

### Time-contextual scheduling

Rather than recalculating "should this event be updated now?" at query time, the re-check schedule is materialised as a single `next_update_at` column the moment an event is ingested. The appointment updater is a single indexed poll with no scheduling logic of its own.

| Schedule type | When `next_update_at` is set | Used for |
|---|---|---|
| `immediate` | `now()` | Family events, tasks, catch-all |
| `before_event:3d` | 06:00 AEST, 3 days before effective_date | Bill reminders |
| `on_due_date` | 06:00 AEST on effective_date | Final bill / appointment check |
| `batch:daily:07:00` | Next 07:00 AEST | Observation digests |
| `never` | `NULL` | Only re-process on explicit change |

As an appointment approaches, the schedule tightens automatically:

| Days until event | Re-check cadence |
|---|---|
| > 7 days | Re-check 3 days before |
| 2 – 7 days | Re-check day before |
| Today or tomorrow | No further auto-check (final state written) |
| Past | `next_update_at = NULL` (done) |

This means a GP appointment created 3 months in advance will be quietly ignored until 3 days before, then checked again the day before, then written in final form on the morning of the appointment — picking up any enrichment that arrived in the intervening months.

### Adaptive appointment enrichment

Appointments are enriched continuously as new information arrives. Every inbound document, email, or message is cross-referenced against existing `Event` nodes:

- If the sender domain matches a known provider linked to an event, an `ENRICHES` edge is created
- If LLM extraction finds a date, person, or organisation that matches an upcoming event, the event's `fact_*` properties are updated
- The scheduler `enrich` phase runs nightly and sweeps all upcoming events for new linked content

**Example:** A specialist appointment is created from a GP referral letter in January. The description at creation: `"Referral to cardiologist."` By the appointment date in March, three more documents have arrived — a pre-appointment questionnaire, an appointment confirmation, and a pathology results email. The nightly enrich phase has written:

```
fact_provider    = "Dr J Smith — City Cardiology"
fact_location    = "Level 3, 123 Main St"
fact_notes       = "Bring referral letter, fasting bloods ordered, Medicare card required"
fact_cost        = "Gap fee $85"
```

When the appointment updater writes the event to Google Calendar the day before, the description contains all of this context — automatically, with no manual input.

### Proactive rule-triggered events

When key facts are detected during extraction, the scheduler can generate proactive follow-up events via `TRIGGERED` edges:

| Detected fact | Proactive event created |
|---|---|
| Travel / holiday booking | Passport expiry check (if < 6 months at departure date) |
| Bill or invoice | `Bill due` event; auto-resolves to `Paid` when payment detected |
| NDIS plan review date | Reminders at 3 months, 6 weeks, 2 weeks before plan expiry |
| Insurance renewal | `Review insurance` reminder 6 weeks before renewal |
| Medication noted | `Reorder medication` reminder based on estimated supply |
| School term dates | Auto-populate school calendar events for all children |

Triggered events are linked to their source event via `TRIGGERED` edges, so the graph retains the causal chain.

---

## Graph Facts — Storing Knowledge as Properties

OpenClaw uses Apache AGE (PostgreSQL graph extension) as its knowledge layer. The key design decision is that structured facts extracted from documents are stored as **typed properties on graph nodes** — not just as text chunks for vector search.

### The `fact_*` property pattern

Every graph node that represents a real-world entity can carry `fact_*` properties — arbitrary key-value pairs written by the extraction pipeline:

```cypher
MATCH (e:Event {event_key: "gmail:abc123"})
SET e.fact_provider    = "Dr J Smith"
SET e.fact_location    = "City Medical Centre, Level 2"
SET e.fact_notes       = "Bring previous scans. Fasting required."
SET e.fact_cost        = "$180 gap"
SET e.facts_updated_at = "2026-06-27T10:00:00+10:00"
```

These properties are:
- **Never truncated** — unlike display fields capped at 500 chars, `fact_*` values carry the full extracted text
- **Idempotently written** — `set_node_facts()` in `graph.py` uses MERGE + SET, safe to run multiple times
- **Queryable via Cypher** — the graph agent can retrieve specific facts without scanning full document text
- **Visible in the graph explorer** — the AGE Viewer shows all properties on each node

### The `ref` hydration handle

Every graph node that corresponds to a Postgres row carries a `ref` property:

```
ref = "personal.event:4721"
ref = "personal.ingest_document:892"
ref = "personal.note:103"
```

This is a lightweight pointer. When the wa-agent or any service needs the full relational row (all columns, joins, permissions), it resolves the ref back to Postgres rather than duplicating the data in the graph. The graph stores *what something is*; Postgres stores *everything about it*.

### Why graph + relational together

| Layer | What it stores | What it's good at |
|---|---|---|
| **Postgres** (`personal.*`) | Full structured rows — events, notes, emails, medications, contacts | Relational queries, ACID writes, calendar sync state, OAuth tokens |
| **AGE graph** (`personal_graph`) | Entity relationships, typed facts, claims, enrichment history | Multi-hop traversal, entity disambiguation, context assembly for LLM |
| **pgvector** (on `personal.note`) | Semantic embeddings | Similarity search across unstructured text |

The wa-agent's search pipeline uses all three in a single request: Cypher for entity/relationship traversal, pgvector for semantic similarity (top-20 candidates), cross-encoder reranker (NPU) to re-score candidates, then the top-5 go to the LLM as context.

---

## Adaptive Intelligence

### next_update_at scheduling

See [Events as First-Class Citizens](#events-as-first-class-citizens) above for the full scheduling model.

### Auto-Resolving Bills

When a bill or invoice is ingested it creates a `Bill` node with status `unpaid`. The system watches subsequent inbound transactions and automatically marks matching bills as `paid`.

**Bill lifecycle:**

```
ingested → unpaid → [payment signal detected] → pending_confirmation → paid
                                                                      ↘ disputed
```

Bills that remain `unpaid` past their due date surface as `overdue` in the dashboard.

### Three knowledge domains

| Domain | Schema | AGE Graph | What it captures |
|---|---|---|---|
| **Personal** | `personal` | `personal_graph` | Family, care, household, appointments, personal notes |
| **Property** | `property_deals` | `property_graph` | Property listings, market research, financial analysis |
| **Decision** | `decision_architect` | `decision_graph` | Organisational frameworks, thought leadership, PR content |

---

## Key design decisions

**Channels are symmetric** — inbound and outbound use the same registry (`personal.channel`). Rules live in `personal.channel_rule` ordered by priority. Adding a new source (SMS, WhatsApp) or destination (Notion, Linear) is a connector + channel row, no pipeline changes.

**`next_update_at` materialised on ingest** — when an item enters the knowledge core, the channel rule resolver immediately writes the concrete datetime it should first be pushed outbound. The appointment updater is a single indexed query with no scheduling logic of its own.

**`effective_date` is timezone-correct** — all events store a `DATE` column in local time alongside the `TIMESTAMPTZ`. All-day events from any timezone resolve to the correct calendar date without UTC drift.

**Email decomposer breaks emails into typed items** — a single email containing a meeting invite, a payment request, and an observation produces three independent items routed to three different outbound channels.

**Separate sync cursors** — `sync_cursor` (email historyId / Outlook deltaLink) and `calendar_sync_cursor` (GCal syncToken) are independent columns on `personal.email_account`. They never overwrite each other.

**Graph facts are never truncated** — `fact_*` properties on graph nodes carry the full extracted value. Display fields (`preview`, `description`) are capped at 500 chars for UI. The distinction is enforced in `build_props()` in `ingestor/src/graph.py`.

**Single-pass classification** — the wa-agent runs one LLM call that returns both graph routing targets (`["personal_graph"]`) and persona selection as a single JSON response, replacing two previously separate calls.

**Two-stage retrieval** — vector search and FTS retrieve 20 candidates; the cross-encoder reranker (NPU, `ms-marco-MiniLM-L-6-v2`) rescores them; the top 5 go to the LLM. This gives reranker-quality context at half the LLM token cost.

---

## Hardware

| Component | Spec |
|---|---|
| CPU | Intel Core Ultra 9 185H |
| RAM | 96 GB DDR5 |
| GPU | Intel Arc 140T — 48 GB shared VRAM |
| NPU | Intel AI Boost |
| OS | Windows 11 Pro + WSL2/Ubuntu |

### Model assignments

| Model | Format | Device | Role |
|---|---|---|---|
| qwen2.5:14b | OpenVINO INT4 | Arc GPU | Email decomposition, financial extraction, bill calendar |
| qwen2.5:3b | OpenVINO INT4 | AUTO:GPU,CPU | Fast classification (Pass 1) |
| qwen2.5:32b | OpenVINO INT4 | GPU,CPU | Deep extraction (Pass 3, optional) |
| nomic-embed-text | OpenVINO | NPU | Semantic embeddings (768-dim) |
| ms-marco-reranker | OpenVINO INT8 | NPU | Cross-encoder reranking of search candidates |
| whisper-small | OpenVINO | CPU | Speech-to-text transcription |

The NPU runs embedding and reranking continuously without competing with the GPU for LLM inference, giving low-latency semantic search at effectively zero GPU cost.

---

## Prerequisites

- Docker Desktop for Windows (WSL2 backend)
- WSL2 / Ubuntu
- Python 3.11+ (for inference server)
- OpenVINO GenAI runtime + optimum-intel (for inference server)

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/youruser/openclaw.git
cd openclaw
cp .env.example .env
# Edit .env — fill in all required secrets (see Environment variables below)
```

### 2. Create the data directory (Windows)

```powershell
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\personal
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\property
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\decision
New-Item -ItemType Directory -Force C:\DataFiles\Processing
New-Item -ItemType Directory -Force C:\DataFiles\Ingested
```

### 3. Convert and place models

Models are not included. Convert with `optimum-cli` and place at the paths in `inference-server/models.yaml`:

```powershell
# Embeddings (NPU)
optimum-cli export openvino --model nomic-ai/nomic-embed-text-v1.5 --task feature-extraction C:\Users\<you>\embed-ov

# Reranker (NPU, INT8)
optimum-cli export openvino --model cross-encoder/ms-marco-MiniLM-L-6-v2 --task text-classification --weight-format int8 C:\Users\<you>\reranker-ov

# LLMs (GPU)
optimum-cli export openvino --model Qwen/Qwen2.5-14B-Instruct --weight-format int4 C:\Users\<you>\qwen2.5-14b-ov
```

### 4. Start the inference server (Windows)

```bat
cd inference-server
start.bat
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
|---|---|---|
| `core` | postgres, dashboard, audit-logger, ingestor | Always running — minimum viable stack |
| `normal` | + n8n, agents, scraper, email-sync, age-viewer | Day-to-day operation |
| `podcast` | + whisper, tts, podcast-agents | Voice and podcast workflows |

---

## Services

### Postgres (`postgres:5432`)

PostgreSQL 16 with three extensions:
- **Apache AGE 1.6.0** — graph layer (Cypher queries over relational data)
- **pgvector** — semantic similarity search
- **pg_trgm** — fuzzy text matching

Role model: each service has a dedicated Postgres role with minimum required permissions. `dashboard_ro` is read-only. `curator` can write to all schemas.

### Dashboard (`dashboard:3000`)

Next.js application providing:
- **Mode switcher** — toggle between core/normal/podcast
- **Audit log viewer** — real-time feed of all agent activity
- **Review queue** — emails pending categorisation
- **Senders hub** — manage inbound channel senders (rescue, block, recategorise, learn multi-entity domains)
- **Graph explorer** — Cypher console against any of the three graphs
- **Chat** — WhatsApp-agent interface with thumbs-down feedback for review queue

### Email Sync (`email-sync`, internal)

Runs five sequential stages after each email poll:

| Stage | What it does |
|---|---|
| **Email sync** | Incremental Gmail (history API) + Outlook (delta query) → `personal.email_message` |
| **Email decomposer** | LLM breaks each email into typed items (calendar_event / payment / observation / task) |
| **Financial processor** | Structured attachment extraction (PDFs, invoices) → `personal.note` |
| **Bill calendar** | Creates/enriches Google Calendar events for financial notes |
| **Appointment updater** | Polls `next_update_at <= now()` → writes all pending events to Google Calendar |

**Calendar routing:**
- Bills → Bills calendar (3 days before due, day-of reminder)
- Family events → Family calendar (Child1/Child2 colour-coded) — configure names via `CHILD1_NAMES`/`CHILD2_NAMES` in `.env`
- Holidays → Holidays calendar + individual day events in Family calendar
- Everything else → Primary calendar

**Key DB tables:**

| Table | Purpose |
|---|---|
| `personal.email_account` | One row per inbox; holds OAuth tokens, `sync_cursor`, `calendar_sync_cursor` |
| `personal.email_message` | Dedup + ingestion state per message |
| `personal.event` | All calendar events; `effective_date` (local date), `next_update_at`, `gcal_event_id` |
| `personal.calendar_sync_map` | Source→target event ID mapping for bidirectional sync |
| `personal.channel` | Channel registry (inbound + outbound) |
| `personal.channel_rule` | Scheduling + routing rules per channel |
| `personal.financial_domain` | Trusted financial sender domains; `entity_slug=NULL` = multi-entity LLM mode |
| `personal.email_filter` | Block/allow rules (domain, sender, keyword) |

### Ingestor (`ingestor:4001`)

Watches `C:\DataFiles\ReadyToIngest\` and ingests any supported file. Also accepts webhooks from email-sync.

**Webhook endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest/email` | Email payload from email-sync |
| `POST` | `/ingest/event` | Calendar event (any source) |
| `POST` | `/ingest/message` | Generic inbound (WhatsApp, SMS, voice) |
| `POST` | `/ingest/observation` | Approved items |
| `GET` | `/scan` | Force immediate scan of ReadyToIngest |

**Multi-pass extraction:**

| Pass | Model | Mode |
|---|---|---|
| 1 (Quick) | qwen2.5:3b | Inline — concepts, people, orgs, claims |
| 2 (Deep) | qwen2.5:14b | Background thread — full schema + fact extraction |
| 3 (Deeper) | qwen2.5:32b | Background, opt-in (`EXTRACT_DEEPER_PASS=true`) |

### Inference Server (`inference-server`, Windows native)

OpenVINO-backed server on port 11434 (Ollama-compatible API). Serves embeddings, reranking, generation, and transcription from a single process. Model registry driven by `inference-server/models.yaml`.

---

## Environment variables

```env
# Postgres
POSTGRES_SUPERUSER=<required>
POSTGRES_SUPERUSER_PASSWORD=<required>
DASHBOARD_DB_PASSWORD=<required>
AUDIT_DB_PASSWORD=<required>
CURATOR_DB_PASSWORD=<required>

# Google OAuth2 (Gmail + Calendar)
GOOGLE_CLIENT_ID=<required>
GOOGLE_CLIENT_SECRET=<required>

# Microsoft OAuth2 (Outlook/Hotmail)
MICROSOFT_CLIENT_ID=<required>
MICROSOFT_TENANT_ID=consumers

# Model routing
AGENT_MODEL=qwen2.5:14b
EMBED_MODEL=nomic-embed-text
EXTRACT_MODEL_QUICK=qwen2.5:3b
EXTRACT_MODEL_DEEP=qwen2.5:14b
EXTRACT_DEEPER_PASS=false

# Search / reranker
RERANK_ENABLED=true
RERANK_MODEL=ms-marco-reranker
WA_SEARCH_TOP_K=5

# Family calendar routing (comma-separated name variants, no real names in repo)
CHILD1_NAMES=<firstname,nickname>
CHILD2_NAMES=<firstname,nickname>
CALENDAR_MIRROR_PRIMARY_EMAIL=<email>
CALENDAR_MIRROR_SECONDARY_EMAIL=<email>
CALENDAR_MIRROR_PARTNER_EMAIL=<email>

# Poll intervals
EMAIL_POLL_INTERVAL_SECS=300
CALENDAR_POLL_INTERVAL_SECS=900
```

---

## Graph schema

### Node labels

| Label | Key properties |
|---|---|
| `Document` | `filename`, `row_id`, `schema`, `preview`, `ref` |
| `Concept` | `name`, `description`, `type` |
| `Person` | `name`, `description`, `ref` |
| `Organisation` | `name`, `description`, `ref` |
| `Claim` | `claim_id`, `text`, `significance`, `confidence`, `framework` |
| `Framework` | `name`, `description`, `domain` |
| `Message` | `source`, `source_id`, `from_handle`, `subject`, `received_at`, `preview`, `ref` |
| `Sender` | `handle`, `name`, `source` |
| `Event` | `event_key`, `title`, `starts_at`, `ends_at`, `effective_date`, `event_type`, `gcal_event_id`, `next_update_at`, `fact_*`, `ref` |
| `Bill` | `bill_id`, `payee`, `amount`, `due_date`, `status`, `reference`, `resolved_at` |

`fact_*` properties on `Event` and other nodes are open-ended — extracted fields such as `fact_provider`, `fact_location`, `fact_notes`, `fact_cost`, `fact_duration` are written by the enrichment pipeline and are never truncated. The `ref` field on each node is a hydration handle (`"schema.table:id"`) for resolving back to the full Postgres row.

### Edge types

| Edge | From → To | Notes |
|---|---|---|
| `MENTIONS` | Document → Concept/Person/Organisation | |
| `ASSERTS` | Document → Claim | |
| `RELATES_TO` | Document → Theme | |
| `AUTHORED_BY` | Document → Person | |
| `PART_OF` | Concept → Framework | |
| `LINKED_TO` | Message → Document | |
| `FROM` | Message → Sender | |
| `ENRICHES` | Document/Message → Event | `confidence`, `enriched_at` |
| `TRIGGERED` | Event → Event | `rule_id` — proactive follow-up events |
| `RESOLVES` | Document/Message → Bill | `match_type`, `confidence` |

---

## Port map

See [PORT_MAP.md](PORT_MAP.md).

---

## Known issues and workarounds

### WSL2 filesystem events
WSL2's 9P filesystem does not propagate Windows-side `inotify` events. The ingestor uses directory polling. Force an immediate scan:

```powershell
Invoke-WebRequest http://localhost:4001/scan
```

### docker compose recreating postgres
If postgres gets recreated, restart the AGE Viewer:

```bash
docker compose restart age-viewer
```

Always use `--no-deps` when restarting a single service:

```bash
docker compose up -d --no-deps email-sync
```

### NPU model shapes
OpenVINO NPU requires fully static input shapes. Models loaded on NPU are reshaped to `[1, max_length]` at startup in `inference-server/src/model_registry.py`. If you see `ZE_RESULT_ERROR_INVALID_ARGUMENT` during model load, check that `max_length` in `models.yaml` matches the tokenizer's expected sequence length.

---

## Roadmap

### Infrastructure
- [x] Channel + rule architecture — inbound/outbound channels with per-channel scheduling rules
- [x] `next_update_at` materialised on ingest — appointment updater is a single indexed poll
- [x] Email decomposer — LLM breaks emails into typed items (event / payment / task / observation)
- [x] `effective_date` — timezone-correct calendar date on all events (local time, no UTC drift)
- [x] Separate email/calendar sync cursors — no cursor overwrite between email and calendar loops
- [x] Holiday day expansion — multi-day holidays create individual day events in Family calendar
- [x] Multi-entity domain support — `financial_domain.entity_slug = NULL` triggers per-email LLM classification
- [x] Senders management hub — rescue/block/recategorise senders, learn multi-entity domains
- [x] `fact_*` property pattern — typed, non-truncated facts on graph nodes
- [x] `ref` hydration handle — every graph node carries a pointer back to its Postgres row
- [x] `set_node_facts()` — idempotent fact writer in graph.py
- [x] NPU embedding — nomic-embed-text on Intel AI Boost NPU
- [x] NPU reranker — cross-encoder ms-marco on NPU, 20-candidate → top-5 pipeline
- [x] Single-pass classifier — one LLM call returns graph routing + persona selection as JSON
- [x] Thumbs-down feedback — chat UI flags poor responses to `config.query_feedback` review queue
- [ ] Adaptive appointment enrichment — nightly enrich sweep updates `fact_*` from linked documents
- [ ] Temporal reprioritisation — event priority escalation as effective_date approaches
- [ ] Voice notes channel — direct `upsert_event` / `personal.note` write, channel rules handle routing
- [ ] SMS / WhatsApp inbound channel
- [ ] Bank feed ingestion — parse transaction emails, match against open bills
- [ ] Bill auto-resolution — `Bill` node status lifecycle; payment signal matching
- [ ] Entity correction feedback loop — dashboard UI writes to `extraction_feedback`
- [ ] Azure app registration for Outlook (refresh token flow)
- [ ] Graph hydration endpoint — `POST /hydrate` in graph-api resolves `ref` back to full Postgres row
