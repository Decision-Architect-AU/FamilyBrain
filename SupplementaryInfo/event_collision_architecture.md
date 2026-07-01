# Event Scheduling & Collision Architecture — Implementation Spec

> Handoff spec for Claude Code. Target system: FamilyBrain, `personal` schema,
> Postgres + AGE. Replaces the blunt binary suppress/don't-suppress collision model
> with a three-stage pipeline. Relational by design — generation, supersession, and
> conflict lifecycle are transactional/operational work and stay in Postgres; the AGE
> graph is not touched by this feature except optionally (see §11).

---

## 1. Core principle — three stages, not three enum values

`override`, `suppress`, `notify` are **not** three values of one `on_collision` field.
They fire at three different pipeline stages and must be implemented separately:

| Behaviour | Stage | Question it answers | Where it runs |
|---|---|---|---|
| **Suppress** | Generation gate | "Given context on this date, should this event be born at all?" | inside `task_generate_events`, before insert |
| **Override** | Reconciliation (matching) | "Does this incoming real event replace a placeholder I generated?" | `email-sync` ingestion, on slot-key match |
| **Notify** | Detection (sweep) | "Do two committed events overlap for the same person?" | conflict sweep, after generate + ingest |

A single rule can configure inputs to all three, but the **logic lives in three
places**. Do not branch a single function on an `on_collision` enum.

---

## 2. Event status state machine + generator jurisdiction

```
                 task_generate_events
                        │  upsert on (asset_id, rule_id, occurrence_date)
                        ▼
                   [ generated ]  provenance=rule   ◄── ONLY rows the generator may touch
                        │
        ┌───────────────┼────────────────────────┐
        │ email matches │                         │ date passes
        │ slot key      │                         ▼
        ▼               │                     [ past ]
  [ superseded ]        │
  superseded_by_event_id│
                        ▼
                  (placeholder retired, history preserved)

  email-sourced or human-touched events:
     [ ingested ] / [ confirmed ] / [ manually_edited ]   provenance ∈ {email, human}
        └── OUT OF GENERATOR JURISDICTION — never recreated, never clobbered
```

**Invariant (the thing that makes regeneration safe):**
> `task_generate_events` may only INSERT, UPDATE, or skip rows where
> `status = 'generated' AND provenance = 'rule'`. Any other row is read-only to it.

Each generated row carries a deterministic **generation key**
`(asset_id, rule_id, occurrence_date)` with a UNIQUE constraint. Re-runs UPSERT on
this key — never duplicate, never resurrect a superseded placeholder.

---

## 3. Materialised collision fields (write-time, not query-time)

At generation and at ingestion, compute and store the collision-relevant fields
directly on the event row, so detection is a pure self-join over `personal.event`
columns (mirrors the existing `derive_commitment_window` pattern):

- `slot_key TEXT` — `{person_id}:{effective_date}:{slot_class}` (see §4)
- `blocks_person BOOLEAN` — does this event commit a person's time?
- `precedence_rank INT` — materialised from event class (see §9), NOT hand-set
- `commitment_start TIMESTAMPTZ`, `commitment_end TIMESTAMPTZ` — already exist

Detection never recomputes these; it reads them.

---

## 4. Slot key (the load-bearing override match)

A **slot** is a real-world block of a person's day that at most one committed event
should occupy. `slot_class` groups event types that compete for the same slot:

| slot_class | event types that map to it |
|---|---|
| `school_day` | SCHOOL, SCHOOL_ACTIVITY, PUPIL_FREE |
| `after_school` | AFTERCARE, PICKUP, ACTIVITY |
| `daytime_care` | HOLIDAY_CARE, VACATION_CARE |
| `appointment` | MEDICAL, THERAPY (these may legitimately collide with care → notify) |

`slot_key = '{person_id}:{effective_date}:{slot_class}'`

**Override rule:** an incoming event whose `slot_key` matches an existing
`status='generated'` placeholder, AND whose `precedence_rank` ≥ placeholder's,
supersedes it (set placeholder `status='superseded'`,
`superseded_by_event_id = incoming.id`) and inserts the incoming as committed.

**Guard against silent failure:** slot-key matching is fuzzy at the edges (LLM date
or person extraction can be wrong). Require a match **confidence ≥ threshold**
(default 0.8). Below threshold: insert the incoming event AND emit a
`needs_review` flag rather than auto-superseding — never drop a placeholder on a
guess. (Reuses the existing review-queue mechanism.)

Note: `appointment`-class events do NOT share a slot with `daytime_care` — they
are different slot_classes, so a medical appt + holiday care on the same day does
not override; it falls through to §6 notify. This is the "person + time, not type
vs type" insight encoded structurally.

---

## 5. Schema changes (DDL)

```sql
-- personal.event additions
ALTER TABLE personal.event
  ADD COLUMN IF NOT EXISTS provenance         TEXT NOT NULL DEFAULT 'rule',  -- rule|email|human
  ADD COLUMN IF NOT EXISTS status             TEXT NOT NULL DEFAULT 'generated',
  ADD COLUMN IF NOT EXISTS slot_key           TEXT,
  ADD COLUMN IF NOT EXISTS slot_class         TEXT,
  ADD COLUMN IF NOT EXISTS blocks_person      BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS precedence_rank    INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS superseded_by_event_id BIGINT
        REFERENCES personal.event(id),
  ADD COLUMN IF NOT EXISTS gen_asset_id       BIGINT,   -- generation key part
  ADD COLUMN IF NOT EXISTS gen_rule_id        TEXT,     -- generation key part
  ADD COLUMN IF NOT EXISTS occurrence_date    DATE;     -- generation key part

-- idempotent generation key (only meaningful for provenance='rule')
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_genkey
  ON personal.event (gen_asset_id, gen_rule_id, occurrence_date)
  WHERE provenance = 'rule';

-- fast collision detection
CREATE INDEX IF NOT EXISTS ix_event_slot     ON personal.event (slot_key);
CREATE INDEX IF NOT EXISTS ix_event_block    ON personal.event (person_id, blocks_person, commitment_start)
  WHERE blocks_person = true AND status IN ('generated','ingested','confirmed');

-- precedence: one declared ordering over event classes (§9)
CREATE TABLE IF NOT EXISTS personal.event_class_precedence (
  event_type   TEXT PRIMARY KEY,
  slot_class   TEXT NOT NULL,
  blocks_person BOOLEAN NOT NULL,
  rank         INT NOT NULL          -- higher wins
);

-- conflicts (notify), with lifecycle
CREATE TABLE IF NOT EXISTS personal.conflict (
  id            BIGSERIAL PRIMARY KEY,
  person_id     BIGINT NOT NULL,
  event_a_id    BIGINT NOT NULL REFERENCES personal.event(id),
  event_b_id    BIGINT NOT NULL REFERENCES personal.event(id),
  detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ,
  resolution    TEXT,                -- kept_a | kept_b | both_kept | auto_passed | dismissed
  suggested_keep BIGINT,             -- precedence hint, NOT an action
  -- unordered-pair identity to dedup
  CONSTRAINT uq_conflict_pair UNIQUE (person_id, event_a_id, event_b_id)
);
-- enforce a<b ordering in app code so the pair is canonical before insert
```

Rule JSONB additions (per rule in `personal.asset.rules`):

```jsonc
{
  "type": "weekly",
  "blocks_person": true,            // does this rule create time-committing events?
  "holiday_immune": false,          // generate regardless of holiday context (medical/therapy = true)
  "slot_class": "school_day",       // optional override; else derived from event_type
  "suppress_on": ["SCHOOL_HOLIDAY","PUBLIC_HOLIDAY","LEAVE"]  // context types that gate generation
}
```

---

## 6. Stage algorithms

### Stage 1 — Generate (`task_generate_events`, periodic)

```
for asset in personal.asset:
  for rule in asset.rules:
    for occurrence_date in expand(rule, horizon):
      genkey = (asset.id, rule.id, occurrence_date)

      # SUPPRESS gate: context events on this date that gate this rule
      if not rule.holiday_immune:
        if exists committed/context event on occurrence_date for asset.person
           with event_type in rule.suppress_on:
              skip   # do not generate; if a generated row exists for genkey, delete it
              continue

      cls   = classify(rule.event_type)            # → slot_class, blocks_person, rank
      event = build_event(rule, occurrence_date, cls)
      event.slot_key = f"{asset.person_id}:{occurrence_date}:{cls.slot_class}"

      UPSERT personal.event ON CONFLICT (genkey) WHERE provenance='rule'
        DO UPDATE SET (display fields, slot_key, blocks_person, precedence_rank)
        -- never overwrites status if already superseded; guard in WHERE:
        WHERE personal.event.status = 'generated'
```

Generator jurisdiction (§2) is enforced in the UPSERT `WHERE status='generated'`.

### Stage 2 — Ingest / Override (`email-sync` decomposer)

```
ev   = llm_decompose(email)            # existing
pid  = resolve_person(ev.person_name)  # §10 — may fail → flag, skip collision logic
cls  = classify(ev.event_type)
ev.slot_key = f"{pid}:{ev.effective_date}:{cls.slot_class}"
ev.provenance = 'email'; ev.status = 'ingested'

# OVERRIDE: does this replace a generated placeholder in the same slot?
placeholder = SELECT * FROM personal.event
   WHERE slot_key = ev.slot_key AND status='generated' AND provenance='rule'
   LIMIT 1

if placeholder and match_confidence(ev, placeholder) >= 0.8:
   if cls.rank >= placeholder.precedence_rank:
       insert ev (committed)
       UPDATE placeholder SET status='superseded', superseded_by_event_id = ev.id
   else:
       insert ev; flag needs_review   # incoming outranked by placeholder — unusual
elif placeholder:                       # matched slot but low confidence
   insert ev; flag needs_review        # do NOT supersede on a guess
else:
   insert ev                           # net-new committed event
```

### Stage 3 — Detect / Notify (conflict sweep, after Stage 1 + 2)

```
# pairs of committed, person-blocking events that overlap in time
candidates = SELECT a.id, b.id, a.person_id
  FROM personal.event a JOIN personal.event b
    ON a.person_id = b.person_id
   AND a.id < b.id
   AND a.blocks_person AND b.blocks_person
   AND a.status IN ('generated','ingested','confirmed')
   AND b.status IN ('generated','ingested','confirmed')
   AND tstzrange(a.commitment_start,a.commitment_end)
       && tstzrange(b.commitment_start,b.commitment_end)
   AND a.slot_class <> b.slot_class      -- same slot_class would have been an override, not a conflict

for (a,b,pid) in candidates:
   INSERT INTO personal.conflict (person_id, event_a_id, event_b_id, suggested_keep)
     VALUES (pid, a, b, higher_rank(a,b))
     ON CONFLICT (person_id, event_a_id, event_b_id) DO NOTHING

# AUTO-RESOLVE pass (idempotent, every sweep):
UPDATE personal.conflict SET resolved_at=now(), resolution='auto_passed'
  WHERE resolved_at IS NULL
    AND (earlier of the two events has ended
         OR either event is now status='superseded'/deleted
         OR events no longer time-overlap)   -- recompute on any event mutation
```

Notify never auto-picks a winner. `suggested_keep` is a dashboard hint only.

---

## 7. Worked scenarios (acceptance tests)

| Scenario | Expected behaviour |
|---|---|
| School holiday, Olivia free | Stage 1 suppress: SCHOOL/PICKUP/AFTERCARE not generated (suppress_on match, not holiday_immune) |
| School holiday, Olivia in holiday care | HOLIDAY_CARE rule is `holiday_immune` (or not in suppress_on) → generates normally |
| Medical appt + holiday care same day | Different slot_class (`appointment` vs `daytime_care`), both `blocks_person` → Stage 3 conflict record, both kept |
| School emails "swimming carnival Fri" | Stage 2 override: same slot_key as SCHOOL placeholder, SCHOOL_ACTIVITY rank ≥ SCHOOL → placeholder superseded, carnival inserted |
| Medical appt on school holiday (no care) | No `blocks_person` context committing the slot → no suppression, no conflict; appt slots in cleanly |
| New speech therapist email | Asset updated; Stage 1 re-run upserts future `generated` rows on genkey with new name; superseded/ingested rows untouched |
| Generator re-runs after override | Superseded placeholder NOT recreated (genkey upsert guarded by `status='generated'`) |
| Low-confidence carnival match | Inserted + `needs_review`; SCHOOL placeholder left intact until human confirms |

---

## 8. Person resolution (foundation — collision is meaningless without it)

Collision keys on `person_id`. Rule-generated events reference a person asset
directly (clean). Email-extracted events yield a **name string** that must resolve
to the same `person_id` via the existing alias mechanism (`CHILD1_NAMES` /
`CHILD2_NAMES` / `PARTNER_NAMES`).

- Resolve at ingestion (Stage 2), before slot_key computation.
- On resolution failure: insert the event with `person_id = NULL`, set
  `needs_review`, and **exclude it from collision detection** (don't treat
  unknown-person as no-collision — that's a silent blind spot). Surface unresolved
  events in the dashboard.

---

## 9. Precedence model

One declared ordering over event classes in `personal.event_class_precedence`.
Materialise `rank`, `slot_class`, `blocks_person` onto each event at write time.
Default seed (higher wins):

```
MEDICAL          appointment   blocks=true   rank=100
THERAPY          appointment   blocks=true   rank=90
HOLIDAY_CARE     daytime_care  blocks=true   rank=80
SCHOOL_ACTIVITY  school_day    blocks=true   rank=70
SCHOOL           school_day    blocks=true   rank=60
AFTERCARE        after_school  blocks=true   rank=50
PICKUP           after_school  blocks=false  rank=40
SCHOOL_HOLIDAY   context       blocks=false  rank=0    -- context only, never commits
PUBLIC_HOLIDAY   context       blocks=false  rank=0
```

Total order is sufficient for these cases. If two incomparable types ever need to
coexist without a clear winner, upgrade to pairwise precedence edges — do not hack
the integers.

---

## 10. Conflict lifecycle rules

- Conflict identity = unordered pair `(person_id, min(a,b), max(a,b))`; canonicalise
  in app code before insert; UNIQUE constraint dedups.
- Recompute on **any event mutation** (move/delete/supersede), not only at sweep —
  a conflict is a function of two live events.
- Auto-resolve when: earlier event has ended, either side superseded/deleted, or
  overlap no longer holds.
- Dashboard shows unresolved conflicts as actionable cards with `suggested_keep`
  as a non-binding hint; human action writes `resolution`.

---

## 11. Graph (optional, deferred)

This feature is fully relational. SUPERSEDES and CONFLICTS_WITH are *not* written to
AGE in v1. Mirror them as edges only if/when a traversal query needs them (e.g.
"show the full supersession history of this slot" or hierarchy-walk reasoning over
conflicts). Keep generation + lifecycle in Postgres — it's ACID, dashboard-queried,
and stateful, which is where it belongs.

---

## 12. Build order (suggested)

1. Schema (§5) + precedence seed (§9).
2. `classify(event_type)` helper → (slot_class, blocks_person, rank).
3. Stage 1 jurisdiction + genkey idempotency (fixes the regeneration bug first).
4. Stage 1 suppress gate.
5. Stage 2 slot_key + override match + confidence gate.
6. Stage 3 detection sweep + auto-resolve.
7. Person resolution hardening (§8).
8. Dashboard conflict cards.
9. Acceptance tests from §7 — these are the regression suite.
