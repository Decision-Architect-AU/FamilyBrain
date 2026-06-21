# OpenClaw — Self-Hosted Personal AI Stack

A fully self-hosted, multi-mode AI agent system built on a GMKtec Core Ultra 9 mini PC (96 GB RAM, Intel Arc GPU 48 GB VRAM, NPU). Runs entirely on-device — no cloud APIs, no data leaving the machine.

---

## What it does

OpenClaw continuously ingests your digital life — emails, files, calendar events, messages — classifies and enriches them with LLM extraction, stores structured knowledge in a graph database, and surfaces insights through a dashboard. It supports three operational modes (core, normal, podcast) that can be toggled without restarting the whole stack.

The core design principle is **adaptive self-improvement**: the system continuously learns from new information and updates its own state without manual intervention. Appointments evolve as context changes, bills auto-resolve when payment is detected, proactive reminders fire based on channel rules and real-world timelines, and extraction models refine themselves as they accumulate domain-specific ground truth.

---

## Architecture

### System layout

```
Windows Host (Core Ultra 9)
├── Ollama (native)            ← LLM API, GPU-accelerated (qwen2.5:14b default)
├── OpenVINO Inference Server  ← OpenVINO GenAI on Intel Arc GPU/NPU
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
│  gcal_family ──► Family calendar (Olivia/Elliana tags)  │
│  gcal_holidays ──► Holidays + daily expansion           │
│  gcal_primary ──► Primary calendar                      │
│  task_list ──► notes (tagged task)                      │
│  observations ──► notes (daily batch)                   │
└─────────────────────────────────────────────────────────┘
```

### Key design decisions

**Channels are symmetric** — inbound and outbound use the same registry (`personal.channel`). Rules live in `personal.channel_rule` ordered by priority. Adding a new source (SMS, WhatsApp) or destination (Notion, Linear) is a connector + channel row, no pipeline changes.

**`next_update_at` materialised on ingest** — when an item enters the knowledge core, the channel rule resolver immediately writes the concrete datetime it should first be pushed outbound. The appointment updater is a single indexed query with no scheduling logic of its own.

**`effective_date` is timezone-correct** — all events store a `DATE` column in Brisbane local time alongside the `TIMESTAMPTZ`. All-day events from any timezone resolve to the correct calendar date without UTC drift.

**Email decomposer breaks emails into typed items** — a single email containing a meeting invite, a payment request, and an observation produces three independent items routed to three different outbound channels.

**Separate sync cursors** — `sync_cursor` (email historyId / Outlook deltaLink) and `calendar_sync_cursor` (GCal syncToken / Outlook calendarView deltaLink) are independent columns on `personal.email_account`. They never overwrite each other.

---

## Adaptive Intelligence

### Adaptive Appointment Management

Appointments are living entities. Their descriptions, priorities, and associated context are updated automatically as new information is ingested.

**How it works:**
- Every new email, file, or message is cross-referenced against existing `Event` nodes in `personal_graph`
- If new content is semantically related to an upcoming event (same person, same organisation, same topic), the event description is enriched with the new context
- As an appointment gets closer, **temporal reprioritisation** applies — events within 48 hours are surfaced more prominently; events that have passed are archived

**Example:** A GP appointment ingested in January has a description of "annual checkup." In February an email arrives with blood test results. In March a referral letter arrives. Each enriches the event node so that by the appointment date the description reads: "Annual checkup — blood test results on file (Feb), referral to cardiologist noted (Mar), bring Medicare card."

**Triggers:**
- New document, email, or message linked to a known Person or Organisation associated with an event
- Direct keyword or entity match against event title or existing description
- LLM semantic similarity above a configurable threshold (`EVENT_ENRICH_THRESHOLD`, default `0.75`)

### next_update_at scheduling

Rather than calculating "should this event be updated now?" at query time, the schedule is materialised as a single `TIMESTAMPTZ` column when the item is first ingested. Rules:

| Schedule | When next_update_at is set | Used for |
|----------|---------------------------|---------|
| `immediate` | `now()` | Family events, tasks, catch-all |
| `before_event:3d` | 06:00 AEST, 3 days before effective_date | Bill reminders |
| `on_due_date` | 06:00 AEST on effective_date | Final bill check |
| `batch:daily:07:00` | Next 07:00 AEST | Observation digests |
| `never` | `NULL` | Only re-process on explicit change |

After the outbound write, the appointment updater sets the next scheduled check or `NULL` if the event is past.

### Rules-Based Proactive Scheduling

OpenClaw applies a configurable rule engine to detected entities and dates to generate proactive follow-up events.

**Built-in rule examples:**

| Trigger | Detected entity / date | Action |
|---------|------------------------|--------|
| Holiday / travel booking detected | Departure date | Check passport expiry. If expiry < 6 months before departure → create `Passport renewal` reminder at 12 months before departure and `Passport organised` checkpoint at 3 months before |
| Bill or invoice ingested | Due date | Create `Bill due` event. If payment later detected → auto-resolve to `Paid` |
| NDIS plan review date detected | Plan expiry | Create reminders at 3 months, 6 weeks, and 2 weeks before expiry |
| Insurance policy renewal | Renewal date | Create `Review insurance` reminder 6 weeks before |
| Prescription / medication noted | Duration or quantity | Create `Reorder medication` reminder when supply is estimated to run low |
| School term dates ingested | Term start/end | Auto-populate school calendar events for all children on file |

### Auto-Resolving Bills

When a bill or invoice is ingested it creates a `Bill` node with status `unpaid`. The system watches subsequent inbound transactions and automatically marks matching bills as `paid`.

**Bill lifecycle:**

```
ingested → unpaid → [payment signal detected] → pending_confirmation → paid
                                                                      ↘ disputed
```

Bills that remain `unpaid` past their due date surface as `overdue` in the dashboard and generate a WhatsApp nudge.

### Three knowledge domains

| Domain | Schema | AGE Graph | What it captures |
|--------|--------|-----------|-----------------|
| **Personal** | `personal` | `personal_graph` | Family, NDIS care, household, appointments, personal notes |
| **Property** | `property_deals` | `property_graph` | Property listings, market research, financial analysis |
| **Decision** | `decision_architect` | `decision_graph` | Organisational frameworks, thought leadership, PR content |

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
| qwen2.5:14b | OpenVINO INT4 | Intel Arc GPU | Email decomposition, financial extraction, bill calendar |
| qwen2.5:3b | OpenVINO INT4 | Intel Arc GPU | Fast classification (Pass 1) |
| qwen2.5:32b | OpenVINO INT4 | CPU/RAM | Deep extraction (Pass 3, optional) |
| nomic-embed-text | OpenVINO | Intel Arc GPU | Semantic embeddings (768-dim) |
| whisper-small | OpenVINO | CPU | Speech-to-text transcription |

---

## Prerequisites

- Docker Desktop for Windows (WSL2 backend)
- WSL2 / Ubuntu
- [Ollama for Windows](https://ollama.com) — `winget install Ollama.Ollama`
- Python 3.11+ (for inference server)
- OpenVINO GenAI runtime (for inference server)

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/youruser/openclaw.git
cd openclaw
cp .env.example .env
# Edit .env — fill in all required secrets
```

### 2. Create the data directory (Windows)

```powershell
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\personal
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\property
New-Item -ItemType Directory -Force C:\DataFiles\ReadyToIngest\decision
New-Item -ItemType Directory -Force C:\DataFiles\Processing
New-Item -ItemType Directory -Force C:\DataFiles\Ingested
```

### 3. Pull Ollama models

```bash
ollama pull qwen2.5:14b
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

### 4. Start the core stack

```bash
docker compose --profile core up -d
```

### 5. Start additional profiles as needed

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

### Email Sync (`email-sync`, internal)

Runs five sequential stages after each email poll:

| Stage | What it does |
|-------|-------------|
| **Email sync** | Incremental Gmail (history API) + Outlook (delta query) → `personal.email_message` |
| **Email decomposer** | LLM breaks each email into typed items (calendar_event / payment / observation / task) |
| **Financial processor** | Structured attachment extraction (PDFs, invoices) → `personal.note` |
| **Bill calendar** | Creates/enriches Google Calendar events for financial notes |
| **Appointment updater** | Polls `next_update_at <= now()` → writes all pending events to Google Calendar |

**Calendar routing:**
- Bills → Bills calendar (3 days before due, day-of reminder)
- Family events → Family calendar (Olivia=pink, Elliana=purple, Holiday=green)
- Holidays → Holidays calendar + individual day events in Family calendar
- Everything else → Primary calendar

**Key DB tables:**

| Table | Purpose |
|-------|---------|
| `personal.email_account` | One row per inbox; holds OAuth tokens, `sync_cursor`, `calendar_sync_cursor` |
| `personal.email_message` | Dedup + ingestion state per message |
| `personal.event` | All calendar events; `effective_date` (Brisbane date), `next_update_at`, `gcal_event_id` |
| `personal.calendar_sync_map` | Source→target event ID mapping for bidirectional sync |
| `personal.channel` | Channel registry (inbound + outbound) |
| `personal.channel_rule` | Scheduling + routing rules per channel |
| `personal.financial_domain` | Trusted financial sender domains; `entity_slug=NULL` = multi-entity LLM mode |
| `personal.email_filter` | Block/allow rules (domain, sender, keyword) |

### Ingestor (`ingestor:4001`)

Watches `C:\DataFiles\ReadyToIngest\` and ingests any supported file. Also accepts webhooks from email-sync.

**Webhook endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ingest/email` | Email payload from email-sync |
| `POST` | `/ingest/event` | Calendar event (any source) |
| `POST` | `/ingest/message` | Generic inbound (WhatsApp, SMS, voice) |
| `POST` | `/ingest/observation` | Approved items |
| `GET` | `/scan` | Force immediate scan of ReadyToIngest |

**Multi-pass extraction:**

| Pass | Model | Mode |
|------|-------|------|
| 1 (Quick) | qwen2.5:3b | Inline — concepts, people, orgs, claims |
| 2 (Deep) | qwen2.5:14b | Background thread — full schema |
| 3 (Deeper) | qwen2.5:32b | Background, opt-in (`EXTRACT_DEEPER_PASS=true`) |

---

## Environment variables

```env
# Postgres
POSTGRES_SUPERUSER=geoff
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
AGENT_MODEL=qwen2.5:14b          # email-sync LLM (decomposer, financial extraction)
EMBED_MODEL=nomic-embed-text
EXTRACT_MODEL_QUICK=qwen2.5:3b
EXTRACT_MODEL_DEEP=qwen2.5:14b
EXTRACT_DEEPER_PASS=false

# Poll intervals
EMAIL_POLL_INTERVAL_SECS=300
CALENDAR_POLL_INTERVAL_SECS=900
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
| `Message` | `source`, `source_id`, `from_handle`, `subject`, `received_at`, `preview` |
| `Sender` | `handle`, `name`, `source` |
| `Event` | `event_key`, `title`, `starts_at`, `ends_at`, `effective_date`, `event_type`, `calendar_source`, `gcal_event_id`, `next_update_at` |
| `Bill` | `bill_id`, `payee`, `amount`, `due_date`, `status`, `reference`, `resolved_at` |

### Edge types

| Edge | From → To | Notes |
|------|-----------|-------|
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

---

## Roadmap

### Infrastructure
- [x] Channel + rule architecture — inbound/outbound channels with per-channel scheduling rules
- [x] `next_update_at` materialised on ingest — appointment updater is a single indexed poll
- [x] Email decomposer — LLM breaks emails into typed items (event / payment / task / observation)
- [x] `effective_date` — timezone-correct calendar date on all events (Brisbane local, no UTC drift)
- [x] Separate email/calendar sync cursors — no cursor overwrite between email and calendar loops
- [x] Holiday day expansion — multi-day holidays create individual day events in Family calendar
- [x] Multi-entity domain support — `financial_domain.entity_slug = NULL` triggers per-email LLM classification
- [x] Senders management hub — rescue/block/recategorise senders, learn multi-entity domains
- [ ] Voice notes channel — direct `upsert_event` / `personal.note` write, channel rules handle routing
- [ ] SMS / WhatsApp inbound channel
- [ ] Bank feed ingestion — parse transaction emails, match against open bills
- [ ] Bill auto-resolution — `Bill` node status lifecycle; payment signal matching
- [ ] Adaptive appointment enrichment — `ENRICHES` edge when new docs relate to existing `Event` nodes
- [ ] Temporal reprioritisation — event priority escalation as appointment date approaches
- [ ] Entity correction feedback loop — dashboard UI writes to `extraction_feedback`
- [ ] Azure app registration for Outlook (refresh token flow)
