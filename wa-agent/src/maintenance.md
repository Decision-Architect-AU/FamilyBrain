# maintenance.py — Scheduled maintenance agent

Runs nightly (or on demand via `POST /maintenance`) as a background task inside the wa-agent container. Responsible for everything that happens between inbound emails arriving: generating future events from asset rules, detecting scheduling conflicts, syncing the knowledge graph, and pre-computing appointment digests.

---

## Tasks

| Task | Function | Default order |
|---|---|---|
| `rederive_facts` | Drain the fact re-derivation queue after edge suppressions | 1 |
| `re_embed` | Embed any notes or themes missing pgvector embeddings | 2 |
| `link` | Run concept linker — `ALIAS_OF` / `SIMILAR_TO` edges in AGE (throttled ~daily) | 3 |
| `audit_concepts` | 35B model validates the linker's own edges (throttled ~daily) | 4 |
| `dedup` / `prune` | Merge duplicate / remove orphan Concept nodes (throttled ~daily) | 5 |
| `detect_provider_gaps` | Bridge calendar→availability, sweep into `personal.routine_gap` | 6 |
| `generate_events` | Generate future `personal.event` rows from asset rules; suspend (not delete) on collision | 7 |
| `detect_conflicts` | Sweep for overlapping person-blocking events | 8 |
| `reconcile_ingested` | Resolve ingested events to final status after conflict detection | 9 |
| `refresh_asset_notes` | Write prose summaries back to `asset.notes` for retrieval | 10 |
| `asset_graph_sync` | Upsert `Asset` nodes in AGE, link via `HAS_ASSET` to `Person`, prune disposed | 11 |
| `monitor` | Audit query patterns, update IntentRule hit counts | 12 |
| `tune_weights` | Adjust `__default__` intent rule weights from content mix | 13 |
| `appointment_digest` | Pre-compute appointment summaries for 5 time windows | 14 |
| `routine_context_pack` | Assemble tier-1 context packs for active routines | 15 |
| `notify_provider_conflicts` | Push notifications for open provider gaps | 16 |
| `asset_summary` | Derive `fact_*` + `fact_summary` for active assets | 17 |
| `review_query_flags` | 35B model reviews `/query` fallback activations (throttled ~daily) | 18 |
| `query_flags_report` | Sends owner report of unresolved data gaps (throttled ~daily) | 19 |

`detect_provider_gaps` runs **before** `generate_events` deliberately — `generate_events`'s provider-aware suspension reads `personal.routine_gap`, which only `detect_provider_gaps` populates. The reverse order would mean `generate_events` always sees the previous cycle's gap data, a full cycle stale.

Run a subset:
```
POST /maintenance?tasks=generate_events&tasks=detect_conflicts
```

---

## Event generation (`task_generate_events`)

### Generator jurisdiction — the invariant

The generator may only INSERT, UPDATE, or DELETE rows where `status = 'generated' AND provenance = 'rule'`. Any other row is read-only to it. This is enforced via the UPSERT `WHERE` clause and explicit DELETE guards. Email-sourced and manually created events are never clobbered by re-runs.

### Genkey idempotency

Every generated event carries a deterministic key:

```
(gen_asset_id, gen_rule_id, occurrence_date)
```

with a `UNIQUE INDEX WHERE provenance = 'rule'`. Re-runs UPSERT on this key — never create duplicates, never resurrect a superseded placeholder. `gen_rule_id` is the rule's `name` field.

### Rule JSONB reference

```jsonc
{
  // Identity
  "name": "School day",              // → gen_rule_id; must be unique within asset

  // What to generate
  "event_type": "SCHOOL_DAY",        // drives slot_class, blocks_person, rank via _classify()
  "event_label": "Olivia — Class 1C (9:00–14:40)",  // event title
  "start_time": "09:00",             // AEST time — generates accurate TIMESTAMPTZ (Brisbane, no DST)
  "auto_create": true,               // must be true or rule is skipped

  // Recurrence
  "recurrence": "weekdays",          // interval | weekly | annual | weekdays
  "recurrence_days": 14,             // for interval only
  "recurrence_day": "Monday",        // for weekly only

  // Horizon
  "horizon_months": 3,               // overrides per-type default from _EVENT_HORIZONS

  // Stage 1 — Suppress-or-suspend gate
  "suppress_on": ["SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY"],
  "holiday_immune": false,           // true → skip the gate entirely (therapy/medical)
  "collision_aware": true,           // legacy — maps to suppress_on: [SCHOOL_HOLIDAY, PUBLIC_HOLIDAY, HOLIDAY, LEAVE]
  // Of suppress_on, only SCHOOL_HOLIDAY/PUBLIC_HOLIDAY are checked as a blind
  // "does this context event exist on this date" match (legitimately global —
  // a school holiday suppresses every school-day routine regardless of
  // provider). HOLIDAY/LEAVE-type suppression instead routes through
  // personal.routine_gap — this routine's actual provider (via
  // personal.event_participant) must be the one who's away, not just anyone.

  // Classification overrides (normally derived from event_type)
  "blocks_person": true,
  "slot_class": "school_day",

  // Metadata
  "lead_time_days": 0,
  "severity_if_missing": "LOW"
}
```

### Recurrence types

| Type | Behaviour |
|---|---|
| `interval` | Every N days from `asset.last_event_date` |
| `weekly` | Same weekday each week (`recurrence_day`) |
| `annual` | Same date each year |
| `weekdays` | Monday–Friday, weekends skipped |

### Default horizons

| Event type | Default |
|---|---|
| `BIN_NIGHT` | 2 months |
| `THERAPY_SESSION` | 3 months |
| `RENT_PAYMENT` | Until `facts.lease_expiry` (3-month fallback) |
| `MEDICATION_REFILL`, `MEDICATION_SCRIPT` | 12 months |
| `INSURANCE_RENEWAL`, `REGO`, `RATES`, `WATER` | 12 months |
| `REFERRAL_RENEWAL`, `MEDICAL_REVIEW` | 12 months |

All overridable per rule via `horizon_months`.

### Stage 1 suppress-or-suspend gate

Before inserting each date, the generator checks two independent signals:

1. **Institutional** (`_has_suppress_event`, restricted to `SCHOOL_HOLIDAY`/`PUBLIC_HOLIDAY`) — any live context event of that type covering the date, regardless of who it's about.
2. **Provider-specific** (`_find_routine_gap`) — an open `personal.routine_gap` row for *this asset*, meaning the routine's actual assigned provider (resolved via `personal.event_participant`, `role='provider'`) is unavailable on that date. `routine_gap` is populated by `detect_provider_gaps`, which runs earlier in the same cycle.

Either match suspends the occurrence — **it is no longer deleted**:

- `status` → `'suspended'`
- `suspended_reason` set (`"Suppressed: <institutional event title>"` or `"Provider away: <provider display name>"`)
- `suspended_by_event_id` links to the overriding event — for the provider-specific path, resolved by joining `routine_gap.availability_id` through `personal.calendar_availability_hint` back to the calendar event that produced it

If neither matches and the row was previously suspended, `_reinstate_if_suspended` clears the suspension (`status` → `'generated'`, reason/link cleared) before the normal upsert runs — automatic, no separate sweep needed. The row survives either way; nothing a suspended occurrence carried is ever lost, which is what lets the digest (weekly-digest spec) report *"no scheduled pickup Wednesday — grandparents still on holidays"* instead of just having nothing to say.

`holiday_immune: true` bypasses the gate entirely. Therapy and medical routines always generate regardless of holidays; Stage 3 will detect any genuine clash and surface it as a conflict for review.

**Why the institutional/provider-specific split exists:** the gate used to be a single blind check — "does *any* context event of a suppress_on type exist on this date, for *anyone*" — which correctly suppressed school-day routines on school holidays, but could just as easily suppress an unrelated routine because a completely different family member's leave happened to overlap the same date. Confirmed live and fixed; see the worked example below.

**Outbound routing.** A suspended occurrence is routed to the **Tentative** calendar (`email-sync/appointment_updater.py`) instead of its natural one — removed from the family's main calendars, visible in one place with the reason in the description, reusing the existing reroute-on-calendar-change logic (already "move, never copy").

---

## Conflict detection (`task_detect_conflicts`)

Stage 3 of the three-stage collision pipeline. Runs after `generate_events`.

### What it detects

Two `blocks_person = true` events for the same person that:
- Overlap in time (`tstzrange && tstzrange`)
- Belong to **different** `slot_class` values

Same slot_class means Stage 2 (email override) should have handled it. Different slot_class means genuine concurrent commitment.

```sql
-- core detection query (simplified)
INSERT INTO personal.conflict (person_id, event_a_id, event_b_id, suggested_keep)
SELECT a.person_id,
       LEAST(a.id, b.id),
       GREATEST(a.id, b.id),
       CASE WHEN a.precedence_rank >= b.precedence_rank THEN a.id ELSE b.id END
FROM personal.event a
JOIN personal.event b
  ON a.person_id = b.person_id
 AND a.id < b.id
 AND a.blocks_person AND b.blocks_person
 AND a.slot_class <> b.slot_class
 AND tstzrange(a.starts_at, COALESCE(a.ends_at, a.starts_at + interval '1 hour'))
  && tstzrange(b.starts_at, COALESCE(b.ends_at, b.starts_at + interval '1 hour'))
WHERE a.status IN ('generated','ingested','confirmed')
ON CONFLICT (person_id, event_a_id, event_b_id) DO NOTHING
```

`suggested_keep` is a hint, not an action. The system never auto-resolves active conflicts.

### Auto-resolution

After detection, a second pass auto-resolves stale conflicts where:
- Either event has ended
- Either event is `superseded` or `cancelled`
- The events no longer time-overlap

Resolution value: `auto_passed`.

### Conflict lifecycle

```
detected (resolved_at IS NULL)
    │
    ├── human action (dashboard card)
    │       ├── kept_a      — event B cancelled
    │       ├── kept_b      — event A cancelled
    │       ├── both_kept   — acknowledged, both stay
    │       └── dismissed   — no action taken
    │
    └── auto_passed — stale, auto-resolved by detect_conflicts sweep
```

---

## Event classification (`_classify`)

`_classify(event_type)` returns `(slot_class, blocks_person, rank)` from the in-memory `_EVENT_CLASS` dict.

| Rank | Event type(s) | slot_class | blocks_person |
|---|---|---|---|
| 100 | `MEDICAL` | `appointment` | yes |
| 90 | `THERAPY`, `THERAPY_SESSION` | `appointment` | yes |
| 85 | `NDIS_PLAN_REVIEW` | `appointment` | yes |
| 80 | `HOLIDAY_CARE`, `VACATION_CARE` | `daytime_care` | yes |
| 70 | `SCHOOL_ACTIVITY` | `school_day` | yes |
| 60 | `SCHOOL_DAY`, `SCHOOL` | `school_day` | yes |
| 55 | `CELLO_CLASS`, `ACTIVITY`, `DANCING` | `after_school` | yes |
| 50 | `AFTERCARE` | `after_school` | yes |
| 40 | `PICKUP` | `after_school` | no |
| 30 | `REFERRAL_RENEWAL`, `MEDICAL_REVIEW` | `appointment` | no |
| 5 | `BIN_NIGHT`, `RENT_PAYMENT`, misc | `misc` | no |
| 0 | `SCHOOL_HOLIDAY`, `PUBLIC_HOLIDAY`, `HOLIDAY`, `LEAVE` | `context` | no |

**Adding a new event type:** add it to `_EVENT_CLASS` in `maintenance.py` AND insert a row into `personal.event_class_precedence` in the DB. The two must stay in sync — `_EVENT_CLASS` is the runtime cache; the DB table is the source of truth for dashboard queries and the email decomposer's `slot_classify.py`.

**Slot class coexistence rules:**

| slot_class | Competes with (same class = Stage 2 override territory) | Different class = Stage 3 conflict if both block |
|---|---|---|
| `school_day` | Other `school_day` | `after_school`, `appointment`, `daytime_care` |
| `after_school` | Other `after_school` | `school_day`, `appointment` |
| `appointment` | Other `appointment` | `school_day`, `after_school`, `daytime_care` |
| `daytime_care` | Other `daytime_care` | `appointment` |
| `context` | Nothing — never blocks | — |
| `misc` | Nothing — never blocks | — |

---

## Provenance model

| provenance | Status on arrival | Can generator touch it? |
|---|---|---|
| `rule` | `generated` | Yes — UPSERT on genkey; `suspended` (not deleted) on collision, auto-reinstated when the collision clears |
| `email` | `ingested` | No — read-only |
| `human` | `confirmed` | No — read-only |

Human-entered events (`provenance='human'`) are the highest-precedence events in the system. The Stage 2 email override only supersedes `provenance='rule'` placeholders. `holiday_immune` is effectively always true for human events — they are never suppressed and never overridden.

---

## Asset notes refresh (`task_refresh_asset_notes`)

After event generation, writes a structured prose summary back to `asset.notes` for every active asset. This is what the wa-agent retrieval layer reads when answering questions — always current, no join required at query time.

```
Asset: Sodium Valproate (Epilim) - Olivia (medication)
Prescribing Doctor: Dr Kate Riney
Dose: 10ml
Frequency: morning and night
Upcoming events:
  • 14 Jul 2026: Sodium Valproate (Epilim) refill due [MEDICATION_REFILL]
  • 13 Aug 2026: Sodium Valproate (Epilim) refill due [MEDICATION_REFILL]
  • 15 Oct 2026: Sodium Valproate (Epilim) new script needed [MEDICATION_SCRIPT]
```

Queries `status IN ('generated','scheduled','ingested','confirmed')` — reflects all live events regardless of provenance.

---

## Asset graph sync (`task_asset_graph_sync`)

Upserts `Asset` nodes into the AGE `personal_graph`:

```cypher
MERGE (a:Asset {ref: 'personal.asset:42'})
SET a.name = '...', a.asset_type = '...', a.subtype = '...', a.status = '...'
```

Links each asset to its person:
```cypher
MATCH (p:Person {name: '...'}), (a:Asset {ref: '...'})
MERGE (p)-[:HAS_ASSET]->(a)
```

Prunes `HAS_ASSET` edges for disposed/sold assets (node retained for history; only the edge is removed).

---

## Appointment digest (`task_appointment_digest`)

Pre-computes appointment summaries for five windows and saves them as `personal.note` rows tagged `digest`/`appointments`/`window:<label>`. Live queries retrieve the pre-summarised digest rather than asking the LLM at request time.

| Window | Events included |
|---|---|
| `TODAY` | Today only — full detail (time, person, type, notes) |
| `3_DAYS` | Next 3 days — full detail |
| `1_WEEK` | Next 7 days — full detail |
| `1_MONTH` | Next 30 days — brief summary per event |
| `3_MONTHS` | Next 90 days — high-level only |

Events are batched 15 per LLM call. All five windows are requested in a single structured prompt using `=== WINDOW: <name> === ... === END ===` delimiters, parsed back in Python. Batch results are accumulated across batches before saving — nearest events always appear first, regardless of which batch they land in.

---

## Key DB tables

| Table | Purpose |
|---|---|
| `personal.asset` | Source of truth — `rules` JSONB drives generation; `event_gen_enabled`, `needs_regen`, `events_generated_until`, `last_event_date` |
| `personal.event` | All events across all provenances and statuses |
| `personal.event_class_precedence` | Canonical type→slot_class/blocks_person/rank mapping |
| `personal.conflict` | Open and resolved conflict pairs |
| `personal.event_participant` | Routine → person/asset role links (`provider`/`subject`/`location`), keyed to `routine_asset_id` — a routine's provider is resolved here, not from free text |
| `personal.asset_availability` | `person_id`/`asset_id` unavailable for a date range, `source` = `manual`/`email`/`calendar`/`inferred`. Deduplicated on `(COALESCE(person_id,-1), COALESCE(asset_id,-1), start_date, end_date, source)` — the original bare `ON CONFLICT DO NOTHING` had no constraint to actually conflict against, so re-running the same still-open holiday created a fresh duplicate row every cycle rather than deduplicating. |
| `personal.calendar_availability_hint` | Bridges a calendar event → the `asset_availability` row it produced. Previously dead (schema existed, nothing wrote to it) — now populated by `_sync_calendar_hints_to_availability`, which is what lets `suspended_by_event_id` resolve back to a real source event. |
| `personal.routine_gap` | Open/resolved provider-specific gaps per routine, written by `detect_provider_gaps`; read by `generate_events`'s Stage 1 suspend check |

**`personal.event` columns added by this module:**

| Column | Type | Purpose |
|---|---|---|
| `provenance` | text | `rule` / `email` / `human` |
| `status` | text | `generated` / `superseded` / `ingested` / `confirmed` / `cancelled` / `completed` / `suspended` |
| `slot_key` | text | `{person_id}:{effective_date}:{slot_class}` — Stage 2 match key |
| `slot_class` | text | Derived from event_type via `_classify()` |
| `blocks_person` | bool | Whether this event commits the person's time |
| `precedence_rank` | int | Higher rank wins on Stage 2 override |
| `superseded_by_event_id` | bigint | FK to the event that replaced this placeholder |
| `gen_asset_id` | bigint | Genkey part 1 |
| `gen_rule_id` | text | Genkey part 2 — rule `name` |
| `occurrence_date` | date | Genkey part 3 |
| `suspended_reason` | text | Human-readable — set only when `status='suspended'` |
| `suspended_by_event_id` | bigint | FK to the overriding event, when resolvable |

---

## Worked examples

### Example 1 — School holidays suppress school day and pickups

Asset rules for school, babysitter, and aftercare all have `suppress_on: ["SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY"]`.

A `SCHOOL_HOLIDAY` event exists on 2026-07-14.

```
generate_events for 2026-07-14:
  SCHOOL_DAY         → institutional suppress_on match → SUSPENDED (reason: "Suppressed: School Holidays - Term 3")
  PICKUP (Monday)    → institutional suppress_on match → SUSPENDED
  AFTERCARE (Tue)    → institutional suppress_on match → SUSPENDED
  CELLO_CLASS (Tue)  → institutional suppress_on match → SUSPENDED
  THERAPY_SESSION    → holiday_immune: true → GENERATES normally
```

The holiday is `blocks_person=false` — it doesn't commit Olivia's time. The therapy session generating on a holiday is not a conflict; her time is free. Each suspended row survives with its reason and a link to the holiday event — none are deleted.

### Example 1b — Provider-specific leave suspends only the affected routine

Grandparent ("Nanna", person_id 5) is on leave 2026-08-12 to 2026-08-15. Two routines have Nanna as `event_participant.role='provider'`: Wednesday and Thursday pickup.

```
detect_provider_gaps:
  finds "Nanna Annual Leave" event (person_id=5, multi-day, away-keyword match)
  → asset_availability row (person_id=5, unavailable, 2026-08-12..2026-08-15)
  → calendar_availability_hint row bridges the event back to this availability row
  → sweeps event_participant (role='provider') × asset_availability
  → routine_gap rows for BOTH Wednesday and Thursday pickup routines
    (both have Nanna as provider — correctly caught, not just one)

generate_events for 2026-08-13 (Thursday pickup occurrence):
  institutional check → no match (not a school/public holiday)
  routine_gap check → open gap for this asset_id, date within range → MATCH
  → status = 'suspended'
  → suspended_reason = "Provider away: Nanna"
  → suspended_by_event_id = the "Nanna Annual Leave" event's id (via the hint bridge)

generate_events for 2026-08-20 (the following week's Thursday occurrence):
  routine_gap check → gap_end (08-15) is before this date → no match
  → generates normally, unaffected
```

A *different* family member's unrelated leave on the same dates would produce no `routine_gap` for these routines at all (no `event_participant` row linking them), so it never reaches the suspend check — this is what the institutional/provider-specific split fixes over the old blind "any suppress_on event on this date" behaviour.

When Nanna's leave ends and the gap's `gap_end` passes, the next `generate_events` run finds no matching `routine_gap` for 08-13's future occurrences and lets the normal upsert reinstate them (`_reinstate_if_suspended` runs first, clearing `status`/`suspended_reason`/`suspended_by_event_id`).

### Example 2 — Swimming carnival overrides school day placeholder

Email arrives: *"Swimming carnival — Friday 24 July."*

```
email_decomposer extracts:
  event_type = SCHOOL_ACTIVITY, rank = 70
  effective_date = 2026-07-24
  person resolved: Olivia (id=1)
  slot_key = "1:2026-07-24:school_day"

Stage 2 override:
  placeholder found: SCHOOL_DAY, rank=60, status=generated, same slot_key
  incoming rank 70 ≥ placeholder rank 60 → override
  placeholder: status → superseded, superseded_by_event_id → carnival.id
  carnival: inserted, status=ingested, provenance=email
```

### Example 3 — Manual medical appointment during holiday care

Olivia is booked into holiday care on 2026-07-14 (email-ingested, `HOLIDAY_CARE`, `daytime_care` slot, `blocks_person=true`). A medical appointment is manually entered for the same day (`MEDICAL`, `appointment` slot, `blocks_person=true`).

```
detect_conflicts sweep:
  MEDICAL (appointment, blocks=true)
  HOLIDAY_CARE (daytime_care, blocks=true)
  Same person, overlapping time, DIFFERENT slot_class
  → conflict detected
  → suggested_keep = MEDICAL (rank 100 > 80)
  → dashboard card: "Olivia has two commitments on 14 Jul"
  → human resolves
```

The system surfaces the conflict but takes no action. Both events remain live until a human resolves them.

### Example 4 — Re-run safety after email override

Swimming carnival has superseded the school day placeholder. Maintenance job runs again tonight.

```
generate_events for 2026-07-24:
  UPSERT on genkey (asset_id=59, 'School day', 2026-07-24)
  → row found, but status='superseded' (not 'generated')
  → UPSERT WHERE status='generated' → condition fails → no update
  → placeholder NOT recreated
```

The superseded placeholder stays superseded. The carnival stays in the calendar.
