# FamilyBrain — Self-Hosted Personal AI Stack

FamilyBrain treats events as the atomic unit of life and builds a knowledge graph around them. Every bill, appointment, prescription, and booking is a node that gets richer over time as new information arrives — automatically enriched, contextually assembled, and routed to the right calendar without manual input.

The problem it solves: a household generates hundreds of documents, emails, and appointments a year, each carrying facts that are relevant to something else. A GP referral letter contains the specialist's name and a follow-up date. A prescription email contains a supply duration that determines when the next script is due. A travel booking triggers a passport check, an insurance review, and a pet care reminder. None of this gets connected unless someone connects it manually — and nobody does.

FamilyBrain connects it automatically. Every inbound email, file, or message is decomposed into typed items, stored as graph nodes with structured facts, and cross-referenced against everything already known. Appointments are enriched continuously as new documents arrive. Bills resolve when payments are detected. Reminders are generated when the graph detects something that should exist but doesn't yet. The right information ends up in the right place — calendar, dashboard, or WhatsApp — without manual routing.

It runs entirely on-device. No cloud APIs. No data leaving the machine.

---

## What it does

FamilyBrain continuously ingests your digital life — emails, files, calendar events, messages — classifies and enriches them with LLM extraction, stores structured knowledge in a graph database, and surfaces insights through a dashboard and WhatsApp agent. It supports three operational modes (core, normal, podcast) that can be toggled without restarting the whole stack.

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

Most personal AI systems treat calendars as one data source among many. FamilyBrain inverts this: **the event is the atomic unit of life, and everything else is context that enriches it.**

This means:

| What it looks like | What it is in FamilyBrain |
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

An event in FamilyBrain is more than a calendar entry. It is a **living knowledge node** in the AGE graph with typed facts attached:

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

## Assets as a Living Registry

### The philosophy

Not everything important is an event. Some things are **persistent real-world entities** with an ongoing lifecycle — a property, a vehicle, a medication, a therapy relationship, a company. These are assets. Events are *things that happen*. Assets are *things that exist and generate events*.

The distinction matters because assets have obligations that span years. A property generates insurance renewals, council rates, water rates, and rent reviews for as long as you own it. A medication generates monthly refill reminders and quarterly script renewals for as long as the prescription is active. A therapy relationship generates weekly appointment slots and annual referral renewals for as long as the therapist is engaged.

FamilyBrain models these as `personal.asset` rows with a `rules` JSONB field that defines the recurring obligations. The maintenance job reads the rules and generates `personal.event` rows — the asset is the source of truth, events are projections of it.

### Asset hierarchy — obligations of ownership

Rates, water, insurance, and strata are not independent subscriptions. They exist *because* the property exists. If the property is sold, they go. This is modelled as rules on the parent asset rather than separate rows:

| What it looks like | How it's stored |
|---|---|
| Council rates | Rule on the property asset: `{"event_type":"RATES","recurrence":"interval","recurrence_days":90}` |
| Property insurance | Rule: `{"event_type":"INSURANCE_RENEWAL","recurrence":"annual","auto_create":true}` |
| Vehicle rego | Rule on the vehicle asset |
| ASIC fees | Rule on the company asset |
| SMSF audit deadline | Rule on the SMSF trust asset |

When an asset is marked `sold` or `disposed`, the maintenance job sets `event_gen_enabled=false` and bulk-cancels all future `scheduled` events linked to that asset. History is preserved; only future projections are cancelled.

### Provider model

Many assets have an **ongoing human contact** who communicates via email — a property manager, a therapist, a specialist. These contacts are stored in `asset.facts`:

```json
{
  "property_manager_name": "Jane Smith",
  "property_manager_agency": "Ray White",
  "property_manager_email": "jsmith@raywhite.com.au"
}
```

When an email arrives from that address, it is matched to the asset and the intent is classified: cancellation, reschedule, provider change, or informational. A provider change (new PM, new therapist) updates `asset.facts`, sets `needs_regen=true`, and the maintenance job rebuilds future events with the updated provider details. The email is the trigger; the asset is the source of truth.

### Email → Asset matching and pending assets

Every inbound email is checked against known assets:

1. `facts.contact_email` / `facts.property_manager_email` — exact sender match
2. `facts.provider_domain` — sender domain match
3. AGE graph — sender email → `Person`/`Organisation` node → `HAS_ASSET` edge → `Asset` node
4. `facts.address_pattern` — address string match in email body (for rates notices, etc.)

If a match is found: intent is classified and applied (cancel event, update facts, etc.).

If no match is found but the email looks like a service provider (therapy, medical, insurance, council rates): a new `personal.asset` row is created with `status='pending'` and `source_email_id` pointing to the originating email. Pending assets surface in the dashboard for human review — confirm, edit, or dismiss. Once activated, the maintenance job picks them up on the next run and starts generating events.

### Event generation — rules to events

The maintenance job (`task_generate_events`) runs nightly and processes every active asset:

```
For each asset where event_gen_enabled=true:
  For each rule where auto_create=true:
    horizon = now + rule.horizon_months  (or facts.lease_expiry for RENT_PAYMENT)
    Generate dates from last_event_date up to horizon
    For collision_aware rules: skip dates that overlap a HOLIDAY/LEAVE event
    Insert personal.event rows (skip if already exists for asset+type+date)
  Update asset.events_generated_until
```

**Generation horizons by event type** (overridable per rule via `horizon_months`):

| Event type | Default horizon |
|---|---|
| `BIN_COLLECTION` | 2 months |
| `THERAPY_SESSION` | 3 months |
| `MEDICATION_REFILL`, `MEDICATION_SCRIPT` | 12 months |
| `INSURANCE_RENEWAL`, `REGO`, `RATES`, `WATER` | 12 months |
| `REFERRAL_RENEWAL`, `MEDICAL_REVIEW` | 12 months |
| `RENT_PAYMENT` | until `facts.lease_expiry` (3 months fallback) |

Therapy and medical appointments are `collision_aware: true` — they are skipped on dates that overlap a holiday or leave event in `personal.event`. Other obligations (bins, rates, rego) are not collision-aware.

### Asset notes — what retrieval reads

After generating events, the maintenance job (`task_refresh_asset_notes`) writes a structured prose summary back to `asset.notes`:

```
Asset: Sodium Valproate (Epilim) - Olivia (medication/antiepileptic)
Prescribing Doctor: Dr Kate Riney
Dose: 10ml
Frequency: morning and night
Upcoming events:
  • 14 Jul 2026: Sodium Valproate (Epilim) refill due [MEDICATION_REFILL]
  • 13 Aug 2026: Sodium Valproate (Epilim) refill due [MEDICATION_REFILL]
  • 15 Oct 2026: Sodium Valproate (Epilim) new script needed [MEDICATION_SCRIPT]
```

This is what the wa-agent retrieval layer reads when answering questions about a person or asset — it always reflects the current state without requiring the agent to join across tables at query time.

### Asset lifecycle

```
Email / manual entry
        │
        ▼
Asset created (status=active or pending)
  facts — structured key/value (address, lender, doctor, provider, ...)
  rules — recurring obligation definitions
        │
        ▼
Maintenance job (nightly)
  ├── generate_events    — rules → personal.event rows (up to horizon)
  ├── refresh_asset_notes — write prose summary to asset.notes
  └── asset_graph_sync  — upsert Asset node in AGE, link to Person via HAS_ASSET

        │
        ▼
Appointment updater (polls next_update_at)
  └── Pushes scheduled events to Google Calendar

        │   (inbound email arrives)
        ▼
Email → Asset matcher
  ├── Match found → intent classifier → cancel / update facts / confirm event
  └── No match + service provider → create pending asset for review

        │   (asset sold / disposed)
        ▼
Disposal cascade
  ├── event_gen_enabled = false
  ├── needs_regen = false
  └── Future scheduled events → status = 'cancelled' (history preserved)
```

---

## Routines — Repeating Life as an Asset

### The philosophy

Not all assets are things you own. Some are **patterns of life** — recurring commitments that generate events, have contacts, can be cancelled or overridden by inbound emails, and need collision awareness. These are routines.

A school schedule is a routine. A weekly therapy block is a routine. A fortnightly speech therapy session is a routine. Grandparent pickup every Thursday afternoon is a routine. They are modelled exactly like other assets — a row in `personal.asset` with `asset_type='routine'`, a `rules` JSONB array, a linked `person_id`, and the same event generation pipeline. The distinction from, say, a property is conceptual: routines generate **time-blocking commitments** for a person, not financial obligations.

What routines gain over raw calendar entries:

- They are **source-of-truth** — if a therapist changes, update the asset and future events regenerate
- They are **collision-aware** — a school holiday suppresses the school day, babysitter pickup, and aftercare automatically
- They can be **overridden by email** — a school activity notice supersedes the generated school-day placeholder for that date
- They carry a **provider** — the therapist's name, the school's contact, the class number — so enrichment pipelines know who to match emails against

### Routine examples

| Routine | event_type | recurrence | blocks_person | collision_aware |
|---|---|---|---|---|
| School — Alice, Class 3B | `SCHOOL_DAY` | weekdays | true | suppress on school_holiday |
| School — Ben, Class 6A | `SCHOOL_DAY` | weekdays | true | suppress on school_holiday |
| Monday afterschool care — Alice | `AFTERCARE` | weekly Monday 15:00 | true | suppress on school_holiday |
| Tuesday music class — Ben | `ACTIVITY` | weekly Tuesday 15:30 | true | suppress on school_holiday |
| Wednesday childcare pickup | `PICKUP` | weekly Wednesday 15:00 | false | suppress on school_holiday |
| Thursday grandparent pickup | `PICKUP` | weekly Thursday 15:00 | false | suppress on school_holiday |
| Friday afterschool care — Ben | `AFTERCARE` | weekly Friday 15:00 | true | suppress on school_holiday |
| Fortnightly speech therapy — Alice | `THERAPY_SESSION` | interval/14d, 8:00 | true | holiday_immune (medical priority) |
| Weekly OT — Alice | `THERAPY_SESSION` | weekly Wednesday 8:30 | true | holiday_immune |

### Routine rules JSONB

```jsonc
{
  "name": "School day",
  "event_type": "SCHOOL_DAY",
  "recurrence": "weekdays",
  "auto_create": true,
  "event_label": "Alice - Maplewood Primary Class 3B (9:00-15:00)",
  "start_time": "09:00",
  "suppress_on": ["SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY"],
  "blocks_person": true,
  "horizon_months": 3,
  "collision_aware": true,
  "severity_if_missing": "LOW"
}
```

Therapy and medical routines add `"holiday_immune": true` — they generate regardless of holiday context and are never suppressed. The maintenance job still detects the overlap and surfaces a conflict notification; it just doesn't prevent the appointment from existing.

---

## Event Collision & Precedence

### The three-stage pipeline

Events from different sources compete for the same person's time. The collision model resolves this in three distinct pipeline stages — not as a single field, but as three separate mechanisms:

| Stage | Name | Question | When it runs |
|---|---|---|---|
| **1** | Suppress | Should this event be born at all given what's already on this date? | Inside `task_generate_events`, before insert |
| **2** | Override | Does this incoming email event replace a generated placeholder in the same slot? | `email-sync` decomposer, on slot-key match |
| **3** | Notify | Do two committed events overlap for the same person? | `task_detect_conflicts`, after generate + ingest |

### The precedence hierarchy

Every event carries a `precedence_rank` derived from its `event_type`, and a `provenance` that describes how it arrived. Together these form the points model that determines which event wins when two compete for the same slot.

**Provenance multiplier** — manually created events always outrank email-ingested, which always outrank system-generated:

| Provenance | Source | Effective ranking |
|---|---|---|
| `human` | Manually entered via dashboard or WhatsApp | Highest — never overridden by the system |
| `email` | Extracted from an inbound email | Mid-tier — overrides generated placeholders |
| `rule` | Generated by maintenance job from asset rules | Baseline — can be suppressed or overridden |

**Event type rank** — within the same provenance, higher rank wins when two events occupy the same slot:

| Rank | Event type | Slot class | Blocks person |
|---|---|---|---|
| 100 | `MEDICAL` | appointment | yes |
| 90 | `THERAPY`, `THERAPY_SESSION` | appointment | yes |
| 80 | `HOLIDAY_CARE`, `VACATION_CARE` | daytime_care | yes |
| 70 | `SCHOOL_ACTIVITY` | school_day | yes |
| 60 | `SCHOOL_DAY`, `SCHOOL` | school_day | yes |
| 55 | `CELLO_CLASS`, `ACTIVITY`, `DANCING` | after_school | yes |
| 50 | `AFTERCARE` | after_school | yes |
| 40 | `PICKUP` | after_school | no |
| 30 | `REFERRAL_RENEWAL`, `MEDICAL_REVIEW` | appointment | no |
| 5 | `BIN_NIGHT`, `RENT_PAYMENT`, misc | misc | no |
| 0 | `SCHOOL_HOLIDAY`, `PUBLIC_HOLIDAY`, `HOLIDAY`, `LEAVE` | context | no |

**Key rule:** context events (`rank=0`) are never committed time — they exist only to gate generation. They suppress lower-ranked events at Stage 1 but do not block person time themselves.

### Worked examples

**1 — School holiday knocks out school days and pickups (Stage 1: Suppress)**

A school holiday event (`SCHOOL_HOLIDAY`, rank=0, `blocks_person=false`) lands on a date. The maintenance job checks each routine's `suppress_on` list before generating. School day, babysitter pickup, after school care, and cello class all have `suppress_on: ["SCHOOL_HOLIDAY"]` — none of them are generated for that date. If they already existed as generated placeholders, they are deleted.

The holiday itself is **context**, not a commitment — Olivia has no committed time. A medical appointment for that date will generate cleanly: `holiday_immune: true` skips the suppress gate.

```
SCHOOL_HOLIDAY (context, rank=0)  →  suppresses:  SCHOOL_DAY, PICKUP, AFTERCARE, CELLO_CLASS, ACTIVITY
                                  →  does NOT suppress:  THERAPY_SESSION (holiday_immune)
                                  →  does NOT block person time (free day for appointments)
```

**2 — Swimming carnival overrides the generated school day (Stage 2: Override)**

The school emails "Swimming carnival — Friday 24 July". The email decomposer extracts a `SCHOOL_ACTIVITY` event (rank=70). It computes `slot_key = "{olivia_id}:2026-07-24:school_day"` and finds the generated `SCHOOL_DAY` placeholder in the same slot (rank=60). Since the incoming rank (70) ≥ placeholder rank (60), the placeholder is marked `status='superseded'` and the carnival event is inserted as `status='ingested'`. History preserved; calendar shows the carnival, not the generic school day.

```
Email SCHOOL_ACTIVITY (rank=70, provenance=email)
  slot_key matches:  SCHOOL_DAY placeholder (rank=60, provenance=rule, status=generated)
  result:            placeholder → superseded
                     carnival → inserted as ingested
```

**3 — Excursion email overrides school holiday placeholder (Stage 2: Override)**

A generated `SCHOOL_HOLIDAY` placeholder exists for a date (created from term calendar import, `provenance=rule`). The school then emails "Day trip to museum — still attending on public holiday". The email decomposer extracts a `SCHOOL_ACTIVITY` (rank=70 > 0). Same slot_key match triggers override: the holiday placeholder is superseded, the excursion is inserted. The day is back on the calendar.

This is the critical distinction: **a generated placeholder for a holiday can be overridden**. A manually entered holiday (`provenance=human`) cannot.

**4 — Manual medical appointment outranks everything (Provenance: human)**

A MEDICAL appointment entered manually carries `provenance='human'`. The system never suppresses it (holiday_immune=true by convention for human-entered events), never overrides it, and flags any overlapping committed event as a conflict for review. It is the highest-precedence event in the system.

```
Manual MEDICAL (rank=100, provenance=human)
  → Never suppressed
  → Never overridden
  → If Olivia is booked in holiday care same day:
       MEDICAL (appointment slot) + HOLIDAY_CARE (daytime_care slot)
       → different slot_class → Stage 3 conflict detected
       → both kept; notification surfaced: "heads up — Olivia has two time commitments"
       → suggested_keep = MEDICAL (higher rank)
       → human resolves: keeps both, reschedules care, or dismisses
```

**5 — Medical appointment on a free holiday day (no conflict)**

Same `SCHOOL_HOLIDAY` context event, but no holiday care booked. Olivia has no `blocks_person=true` event for that day. The medical appointment generates cleanly — slot is free. No conflict record. The holiday context event is not a time commitment; it is only a generation gate.

```
SCHOOL_HOLIDAY (context, blocks_person=false)  +  MEDICAL (blocks_person=true)
→ no conflict — SCHOOL_HOLIDAY doesn't block time
→ MEDICAL generates normally (holiday_immune skips suppress gate)
→ calendar shows: medical appointment on a day off school
```

### Collision lifecycle

Detected conflicts live in `personal.conflict` with a full lifecycle:

```
detected → [human reviews dashboard card]
                ├── kept_a / kept_b      — one event cancelled
                ├── both_kept            — acknowledged, both stay
                ├── dismissed            — acknowledged, no action
                └── auto_passed          — auto-resolved because:
                                             - either event ended
                                             - either event superseded/cancelled
                                             - events no longer time-overlap
```

The dashboard surfaces open conflicts as actionable cards with `suggested_keep` as a non-binding hint. The system never auto-picks a winner for active conflicts.

### Slot classes — what competes with what

Two events only compete (Stage 2 override / Stage 3 conflict) if they share a `slot_class`. Different slot classes coexist by design:

| slot_class | Competes with | Does not compete with |
|---|---|---|
| `school_day` | Other school_day events | after_school, appointment, daytime_care |
| `after_school` | Other after_school events | school_day, appointment |
| `appointment` | Other appointment events | school_day, after_school, daytime_care |
| `daytime_care` | Other daytime_care events | appointment, school_day |
| `context` | Nothing (never competes) | — |
| `misc` | Nothing (never competes) | — |

A medical appointment and holiday care on the same day are **different slot classes** — Stage 2 does not supersede either. Instead, Stage 3 detects that both `blocks_person=true` events overlap for the same person across different slot classes and surfaces a conflict for human review.

### Eventual classification

The system does not need to be certain at ingestion time. It needs to be correct by T+2 or T+3 days.

When an email arrives, the decomposer makes its best call — extracts event type, resolves person, assigns slot class, runs Stage 2 if a placeholder matches. If confidence is low, the event is flagged `needs_review=true` rather than rejected. It lands in the calendar in a reasonable provisional state.

Over the following 48–72 hours, context accumulates:

- A follow-up confirmation email arrives from the same sender
- A calendar invite lands with the canonical event title
- A second email from the same thread clarifies the date or person
- Cross-corroboration: a school newsletter confirms the excursion date that was in the original email

A `task_resolve_pending` maintenance pass revisits all `needs_review=true` events that are 24+ hours old. With the accumulated context it can often confirm the original classification, correct the person assignment, or promote the event to `confirmed`. As time passes, the confidence threshold to confirm lowers — by day 3, any corroborating signal is usually enough.

The practical implication: the `needs_review` queue is **not urgent**. Most items self-resolve within a couple of days. The residual — events still flagged after 3 days — represents genuine ambiguity that warrants a human glance, not a failure of the pipeline.

---

## Graph Facts — Storing Knowledge as Properties

FamilyBrain uses Apache AGE (PostgreSQL graph extension) as its knowledge layer. The key design decision is that structured facts extracted from documents are stored as **typed properties on graph nodes** — not just as text chunks for vector search.

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

### Hierarchy-aware retrieval

Flat retrieval — treating every linked row with equal weight — produces noise for person and entity queries. Asking about a child returns as much about the parents as the child. Asking about a trust returns governance notes at the same rank as the property bills it issued. The hierarchy traversal model eliminates this.

When the query names a specific person or entity, `search.py` runs a **pseudo-Dijkstra walk** rooted at that focal node:

```
Focal node
  │
  ├─ DOWN  (cost=3)  ← own records: appointments, medications, school events, invoices, owned assets
  │     └─ DOWN+DOWN (cost=6) ← assets' own events/bills
  │
  ├─ SIDEWAYS (cost=8) ← siblings, partners, co-owners, shared events
  │     └─ SIDEWAYS+DOWN (cost=11) ← sibling's/co-owner's own records
  │
  └─ UP (cost=10) ← parents, trustees, directors, beneficiaries, governance notes
        └─ UP+DOWN (cost=13) ← parent's/trustee's own records
```

Budget (default 30): anything whose accumulated cost exceeds the budget is excluded entirely; everything under budget is included but converted to a `match_score` (high score = low cost = close to focal node), so the LLM context bundle is already sorted by proximity before the reranker sees it.

This is what makes responses feel natural — they lead with what you asked about and taper into broader context, mirroring how a person would actually explain the topic.

**Example walk — "What's coming up for Child1?"**

```
Focal node: Child1 (Person, id=1, relationship="daughter")

  cost  3 → Child1's own appointments, medications, school events    [score 3]
  cost  3 → notes mentioning Child1                                  [score 3]
  cost  8 → shared family events that name Child1                    [score 2]
  cost 11 → sibling (Child2) own records      [8 sideways + 3 down]  [score 2]
  cost 13 → parent family booking note       [10 up + 3 down]        [score 1]

  cost 23 → would be next candidate (another sibling record)
             — still under budget of 30, included at score 1

  cost 33 → governance notes about the family trust (up + up)
             — exceeds budget of 30, excluded entirely
```

The LLM context bundle arrives sorted by score: in aggregate, the retrieval knows far more about Child1 than it does about the parents — which is exactly the right shape for the question that was asked.

**Hierarchy profiles** are independently-tunable per category. Each is a named `HierarchyProfile(budget, down, sideways, up)` object with its own env-var namespace:

| Profile | Category | Default costs |
|---|---|---|
| `FAMILY_HIERARCHY` | People / family tree | budget=30, down=3, sideways=8, up=10 |
| `ENTITY_HIERARCHY` | Trusts, companies, ownership structures | budget=30, down=3, sideways=8, up=10 |
| `FINANCIAL_HIERARCHY` *(future)* | Investment/super fund structures | TBD — different "up" and "sideways" semantics |

Override any profile via env: `FAMILY_HIERARCHY_COST_UP=15`, `ENTITY_HIERARCHY_BUDGET=40`, etc. Profiles never share constants, so tuning one cannot affect another.

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

**Separate sync cursors** — `sync_cursor` (inbox historyId / Outlook deltaLink), `sent_sync_cursor` (Outlook SentItems deltaLink), and `calendar_sync_cursor` (GCal syncToken) are independent columns on `personal.email_account`. They never overwrite each other.

**Sent item ingestion** — both Gmail and Outlook sync sent items alongside received mail. Gmail includes `in:sent` in the initial query and detects the `SENT` label on incremental history events. Outlook runs a separate `SentItems` delta query with its own cursor. Sent emails are stored with `is_sent = true`, formatted as `To: <recipients>` in the knowledge base, and tagged `sent` so they are retrievable as outbound context. This is important when multiple household members' accounts are connected: your side of every email thread is captured even if you sent rather than received it.

**Inter-party forwarding awareness** — when two accounts are connected (e.g. primary and partner), emails sent from one account that arrive in the other's inbox are ingested as received mail on the second account. This is intentional: because sent items are now also ingested from the originating account, both sides of every conversation exist in the knowledge base. Deduplication is keyed on `(account_id, provider_msg_id)` so the same message appears once per account — the received copy carries the recipient's perspective (any annotations, labels, or reply context) while the sent copy carries the full outbound text.

**Three-stage collision pipeline** — generation suppression, email override, and conflict detection are three separate mechanisms, not branches of a single function. Suppress fires at generation time (before insert). Override fires at email ingestion (Stage 2 slot-key match). Conflict detection is a sweep that runs after both (Stage 3). Each stage is independently tunable via per-rule `suppress_on`, `holiday_immune`, and `blocks_person` flags. Context events (`SCHOOL_HOLIDAY`, `PUBLIC_HOLIDAY`) carry `rank=0` and `blocks_person=false` — they gate generation but never commit the person's time, so a medical appointment on a school holiday day is not a conflict.

**Graph facts are never truncated** — `fact_*` properties on graph nodes carry the full extracted value. Display fields (`preview`, `description`) are capped at 500 chars for UI. The distinction is enforced in `build_props()` in `ingestor/src/graph.py`.

**Single-pass classification** — the wa-agent runs one LLM call that returns both graph routing targets (`["personal_graph"]`) and persona selection as a single JSON response, replacing two previously separate calls.

**Two-stage retrieval** — vector search and FTS retrieve 20 candidates; the cross-encoder reranker (NPU, `ms-marco-MiniLM-L-6-v2`) rescores them; the top 5 go to the LLM. This gives reranker-quality context at half the LLM token cost.

**Weighted hierarchy traversal** — when a query names a specific person or entity, retrieval switches from flat FTS/vector to a pseudo-Dijkstra graph walk anchored on that node. Each direction of travel carries a per-hop cost; the walk expands outward until the accumulated cost exceeds the budget. This mirrors how information naturally flows in a family or ownership structure — downward (own records, appointments, owned assets) is cheap and returns a lot; sideways (siblings, co-owners) is moderately expensive; upward (parents, trustees, directors) is expensive and returns only the most governance-relevant items. Results closer to the focal node rank higher in the LLM context bundle.

**Named hierarchy profiles** — each category of data that has its own natural "direction" gets an independently-tunable weighting profile (`FAMILY_HIERARCHY`, `ENTITY_HIERARCHY`, with room for a future `FINANCIAL_HIERARCHY`). Adding a new hierarchy type is a single `_profile_from_env()` call with its own `<NAME>_HIERARCHY_BUDGET / COST_DOWN / COST_SIDEWAYS / COST_UP` env vars — no shared constants to collide on.

**Batched LLM querying** — where a task would require N serial LLM calls (e.g. appointment summaries across multiple time windows), the agent batches records and requests all outputs in a single structured response using delimited blocks (`=== WINDOW: <name> === ... === END ===`), parsed back out in Python. The appointment digest batches 15 events and requests TODAY / 3_DAYS / 1_WEEK / 1_MONTH / 3_MONTHS summaries in one call rather than one call per window per batch. Digest results are saved back into `personal.note` (tagged `digest`) so live queries retrieve the pre-summarised version instead of re-asking the LLM at request time — pushing work into the scheduled maintenance window and keeping per-request latency low.

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
git clone https://github.com/youruser/familybrain.git
cd familybrain
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
| `personal.email_account` | One row per inbox; holds OAuth tokens, `sync_cursor`, `sent_sync_cursor`, `calendar_sync_cursor` |
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
| `Asset` | `ref`, `name`, `asset_type`, `subtype`, `status` |

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
| `HAS_ASSET` | Person → Asset | links person to their assets (medications, therapy, subscriptions) |

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
- [x] Separate email/calendar sync cursors — no cursor overwrite between email, sent, and calendar loops
- [x] Sent item ingestion — Gmail (`SENT` label) and Outlook (`SentItems` delta) ingested alongside received mail; formatted as outbound context in the knowledge base
- [x] Inter-party forwarding awareness — sent items from one connected account appear as received mail on others; both copies retained with `(account_id, provider_msg_id)` dedup
- [x] Collision auto-resolution — past conflicts auto-resolve; holidays, birthdays, and anniversaries excluded from collision detection
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
- [x] Asset registry — `personal.asset` with `rules` JSONB, lifecycle fields (`needs_regen`, `events_generated_until`), `pending` status for email-discovered assets
- [x] Asset hierarchy — rates, water, insurance, strata are rules on the parent asset; disposal cascades to cancel future generated events
- [x] Asset event generation — `task_generate_events` with genkey idempotency `(gen_asset_id, gen_rule_id, occurrence_date)`, per-rule horizons, suppress gate
- [x] Asset notes refresh — `task_refresh_asset_notes` writes structured prose summary to `asset.notes` for retrieval
- [x] Asset graph sync — `task_asset_graph_sync` upserts `Asset` nodes, `HAS_ASSET` edges to `Person`, prunes disposed
- [x] Routine asset type — `asset_type='routine'` for recurring life patterns (school schedule, pickups, therapy); same pipeline as financial assets
- [x] Event provenance — `provenance` column: `rule` (generated) / `email` (ingested) / `human` (manual); generator jurisdiction enforced — only `status='generated' AND provenance='rule'` rows are touched by re-runs
- [x] Event status model — `generated / superseded / ingested / confirmed / scheduled / cancelled / rescheduled / completed`
- [x] Event classification — `event_class_precedence` table; every event carries `slot_class`, `blocks_person`, `precedence_rank` materialised at write time
- [x] Slot keys — `{person_id}:{effective_date}:{slot_class}` — the load-bearing identity for Stage 2 override matching
- [x] Stage 1: Suppress gate — `suppress_on` list per rule; `holiday_immune: true` for medical/therapy; generated placeholders deleted when suppressed
- [x] Stage 2: Override — email decomposer computes slot_key, finds generated placeholder, supersedes on rank ≥ match; `superseded_by_event_id` FK preserves history
- [x] Stage 3: Conflict detection — `task_detect_conflicts` sweeps for overlapping `blocks_person=true` events across different slot_classes for the same person; `personal.conflict` table with full lifecycle
- [x] Conflict auto-resolution — auto-passes stale conflicts when either event ends, is superseded, or no longer overlaps
- [ ] Email → Asset matcher — match inbound emails to assets via contact_email / provider_domain / AGE traversal
- [ ] Email intent classifier — classify matched emails as cancellation / reschedule / provider change / informational
- [ ] Dashboard conflict cards — surface open `personal.conflict` rows as actionable cards with `suggested_keep` hint
- [ ] Pending asset review UI — dashboard card for human review/activation of email-discovered assets
- [ ] Adaptive appointment enrichment — nightly enrich sweep updates `fact_*` from linked documents
- [ ] Temporal reprioritisation — event priority escalation as effective_date approaches
- [ ] Voice notes channel — direct `upsert_event` / `personal.note` write, channel rules handle routing
- [ ] SMS / WhatsApp inbound channel
- [ ] Bank feed ingestion — parse transaction emails, match against open bills
- [ ] Bill auto-resolution — `Bill` node status lifecycle; payment signal matching
- [ ] Entity correction feedback loop — dashboard UI writes to `extraction_feedback`
- [ ] Azure app registration for Outlook (refresh token flow)
- [ ] Graph hydration endpoint — `POST /hydrate` in graph-api resolves `ref` back to full Postgres row
