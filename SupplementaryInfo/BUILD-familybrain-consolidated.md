# Build Doc — FamilyBrain: Assets, Events, and Notification Layer

**For:** Claude Code, operating inside the FamilyBrain repo  
**Supersedes:** `BUILD-collision-queue.md`, `BUILD-notification-layer.md`  
**Scope:** Four interconnected systems:
1. **Asset table** — master record for anything that generates events over its lifetime
2. **Event model extensions** — attendance mode, travel child nodes, dependency links
3. **Notification layer** — unified detection, persistence, and resolution for all alert types
4. **Rule watcher** — maintenance job that generates missing events from asset rules

**Non-goal:** Do not auto-execute resolution actions beyond event creation. Where human action is required (booking, rescheduling), record intent only in v1.

---

## Part 1 — Asset Table

### 1.1 Concept

An asset is any persistent thing in the world that generates calendar events over its lifetime. The asset table is a master record. Events are children of assets. Rules live on the asset and define what events should exist and when.

Examples:
- Car → rego renewal, insurance renewal, service, roadworthy
- Medication → script renewal, reorder alert, GP review
- Property → council rates, insurance renewal, inspection, smoke alarm service
- Subscription → renewal reminder
- Person → NDIS plan review, passport renewal, driver's licence renewal
- Pet → vaccination, vet check, registration renewal
- Device → warranty expiry, AppleCare renewal

### 1.2 Schema

**File: `postgres/migrations/XX_assets.sql`**

```sql
CREATE TABLE IF NOT EXISTS personal.asset (
    id                  serial PRIMARY KEY,
    name                text NOT NULL,
    asset_type          text NOT NULL,
    -- vehicle | medication | property | subscription | person | device | pet

    subtype             text,
    -- vehicle: car | motorcycle | trailer
    -- medication: prescription | OTC | supplement
    -- property: PPR | investment | commercial

    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive', 'disposed', 'sold')),

    person_id           int,
    -- null = household asset (car, property)
    -- set = individual asset (medication, passport)
    -- FK to personal.person or personal.family_member — use existing convention

    -- Key dates (type-agnostic — label comes from rules)
    acquired_date       date,
    next_event_date     date,           -- nearest upcoming generated event
    last_event_date     date,           -- most recent completed event

    -- Event generation control
    event_gen_enabled   boolean NOT NULL DEFAULT true,
    -- Set false to pause generation without deleting asset (e.g. car in storage)

    -- Type-specific fields as disciplined jsonb
    -- See section 1.3 for per-type fact schemas
    facts               jsonb NOT NULL DEFAULT '{}',

    -- Rules that define what events this asset should generate
    -- See section 1.4 for rule schema
    rules               jsonb NOT NULL DEFAULT '[]',

    -- Pointer back to source relational table if asset mirrors an existing row
    -- Format: schema.table:id  e.g. property_deals.deal:42
    ref                 text,

    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Index for rule watcher queries
CREATE INDEX idx_asset_type_status      ON personal.asset (asset_type, status);
CREATE INDEX idx_asset_next_event       ON personal.asset (next_event_date);
CREATE INDEX idx_asset_person           ON personal.asset (person_id);

-- Link events to assets
ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS asset_id int REFERENCES personal.asset(id),
    ADD COLUMN IF NOT EXISTS generated_by_rule text; -- rule name that created this event

-- Grant access
GRANT SELECT, INSERT, UPDATE ON personal.asset TO familybrain_dashboard;
GRANT SELECT ON personal.asset TO familybrain_wa;
GRANT SELECT, INSERT, UPDATE ON personal.asset TO familybrain_agent;
```

### 1.3 Facts Schema Per Asset Type

`facts` is jsonb but disciplined. The ingestor validates keys at write time against these schemas. Unknown keys are permitted but logged.

```python
ASSET_FACT_SCHEMAS = {
    "vehicle": {
        "required": ["make", "model", "year", "rego", "rego_state", "rego_expiry"],
        "optional": ["colour", "vin", "odometer_km", "fuel_type",
                     "insurance_provider", "insurance_expiry", "insurance_policy_no"]
    },
    "medication": {
        "required": ["drug_name", "dose", "frequency", "prescriber"],
        "optional": ["script_number", "pharmacy", "days_supply",
                     "last_filled_date", "repeats_remaining", "pbs_code"]
    },
    "property": {
        "required": ["address", "lot_plan"],
        "optional": ["council", "rates_cycle", "insurance_provider",
                     "insurance_expiry", "strata_manager", "body_corp_levy_cycle"]
    },
    "subscription": {
        "required": ["provider", "plan", "renewal_date", "renewal_period_days"],
        "optional": ["cost", "payment_method", "auto_renews", "account_email"]
    },
    "person": {
        "required": ["full_name"],
        "optional": ["passport_expiry", "passport_number", "drivers_licence_expiry",
                     "drivers_licence_state", "medicare_expiry", "ndis_plan_end",
                     "ndis_plan_type"]
    },
    "device": {
        "required": ["make", "model", "serial_number"],
        "optional": ["purchase_date", "warranty_expiry", "applecare_expiry",
                     "imei", "os_version"]
    },
    "pet": {
        "required": ["name", "species", "breed"],
        "optional": ["dob", "microchip_number", "registration_expiry",
                     "vaccination_due", "vet_name", "desexed"]
    }
}
```

### 1.4 Rules Schema on Asset

`rules` is a jsonb array on the asset row. Each rule defines one recurring event type.

```json
[
  {
    "name": "Rego renewal",
    "event_type": "REGO_RENEWAL",
    "event_label": "Vehicle registration due",
    "trigger_source": "facts.rego_expiry",
    "lead_time_days": 30,
    "recurrence": "annual",
    "recurrence_days": null,
    "auto_create": true,
    "collision_aware": true,
    "attendance_mode": "IN_PERSON",
    "travel_buffer_before_min": null,
    "travel_buffer_after_min": null,
    "severity_if_missing": "HIGH",
    "enabled": true
  },
  {
    "name": "Car service",
    "event_type": "SERVICE",
    "event_label": "Car service due",
    "trigger_source": "facts.next_service_date",
    "lead_time_days": 14,
    "recurrence": "interval",
    "recurrence_days": 180,
    "auto_create": false,
    "collision_aware": true,
    "attendance_mode": "IN_PERSON",
    "travel_buffer_before_min": 15,
    "travel_buffer_after_min": 60,
    "severity_if_missing": "MEDIUM",
    "enabled": true
  }
]
```

**Rule fields:**

| Field | Description |
|---|---|
| `name` | Human label for this rule |
| `event_type` | Code used on generated event nodes |
| `event_label` | Display name on generated events |
| `trigger_source` | Where the base date comes from — `facts.<key>`, `last_event_date`, or `next_event_date` |
| `lead_time_days` | How far ahead to create/flag the event |
| `recurrence` | `annual` \| `interval` \| `calculated` \| `once` |
| `recurrence_days` | For `interval` recurrence — days between occurrences |
| `auto_create` | If true, rule watcher creates event automatically. If false, fires PATTERN_GAP notification |
| `collision_aware` | Whether generated events participate in collision detection |
| `attendance_mode` | `IN_PERSON` \| `ONLINE` — affects travel node generation |
| `travel_buffer_before_min` | Minutes before event for travel node (null = no travel node) |
| `travel_buffer_after_min` | Minutes after event for travel node |
| `severity_if_missing` | Severity of PATTERN_GAP notification when event is missing |
| `enabled` | Toggle individual rules without removing them |

### 1.5 Graph Node

Create an `:Asset` node in AGE for each asset row, linked to its generated event nodes:

```cypher
-- Asset node
CREATE (:Asset {
    ref: 'personal.asset:42',
    name: 'Honda CR-V',
    asset_type: 'vehicle',
    status: 'active',
    fact_rego: 'ABC123',
    fact_rego_state: 'QLD',
    fact_rego_expiry: '2026-12-01'
})

-- Ownership edge
MATCH (p:Person {ref: 'personal.family_member:1'}), (a:Asset {ref: 'personal.asset:42'})
CREATE (p)-[:OWNS]->(a)

-- Event link (created when event is generated)
MATCH (a:Asset {ref: 'personal.asset:42'}), (e:Event {ref: 'personal.event:99'})
CREATE (a)-[:HAS_EVENT]->(e)
```

---

## Part 2 — Event Model Extensions

### 2.1 New Event Node Properties

Add to event node property convention in `ingestor/src/graph.py`:

```python
# Collision awareness — set per label at ingest time
COLLISION_AWARE_LABELS = {
    "Appointment":    True,
    "SchoolEvent":    True,
    "SportingEvent":  True,
    "PropertyEvent":  True,
    "FinancialEvent": True,
    "Travel":         True,    # travel nodes inherit awareness
    "BinNight":       False,
    "SchoolHoliday":  False,
    "PublicHoliday":  False,
    "Reminder":       False,
    "Medication":     False,   # medication events are never collision-aware
    "Script":         False,
}

# Attendance mode — set at ingest or default IN_PERSON
ATTENDANCE_MODES = ("IN_PERSON", "ONLINE", "HYBRID")
# HYBRID means the event runs both modes — set the family member's actual mode
# i.e. is THIS person attending in-person or online
```

Event node properties (additions to existing convention):

```
collision_aware:              bool
attendance_mode:              text  IN_PERSON | ONLINE | HYBRID
location:                     text  (address or venue name)
travel_buffer_before_min:     int | null
travel_buffer_after_min:      int | null
commitment_start:             timestamp  (derived — see 2.2)
commitment_end:               timestamp  (derived — see 2.2)
parent_event_ref:             text | null  (set on travel child nodes)
collision_status:             text | null  (written back by notification layer)
collision_notification_id:    text | null
staleness_suppressed_until:   timestamp | null
```

### 2.2 Commitment Window Derivation

The commitment window is what the collision detector compares — not the raw event window.

```python
def derive_commitment_window(event: dict) -> tuple:
    """
    Returns (commitment_start, commitment_end) for collision comparison.
    Called at ingest time — stored as node properties.
    """
    event_start = parse_timestamp(event["event_start"])
    event_end   = parse_timestamp(event["event_end"]) if event.get("event_end") \
                  else event_start + timedelta(hours=1)  # default 1hr if no end set

    if event.get("attendance_mode") == "ONLINE":
        return event_start, event_end

    before = event.get("travel_buffer_before_min") or 0
    after  = event.get("travel_buffer_after_min")  or 0

    return (
        event_start - timedelta(minutes=before),
        event_end   + timedelta(minutes=after)
    )
```

Store both on the node at ingest:
```python
props["commitment_start"] = commitment_start.isoformat()
props["commitment_end"]   = commitment_end.isoformat()
```

### 2.3 Travel Child Nodes

When an IN_PERSON event has travel buffers set, the ingestor spawns two child Travel nodes using the existing edge creation pattern.

```python
def spawn_travel_nodes(parent_event: dict, conn) -> None:
    """
    Creates TravelTo and TravelFrom nodes linked to parent event.
    Uses existing create_node() and create_edge() helpers.
    Only fires when attendance_mode = IN_PERSON and buffers are set.
    """
    if parent_event.get("attendance_mode") != "IN_PERSON":
        return
    if not (parent_event.get("travel_buffer_before_min") or
            parent_event.get("travel_buffer_after_min")):
        return

    event_start = parse_timestamp(parent_event["event_start"])
    event_end   = parse_timestamp(parent_event["event_end"])
    parent_ref  = parent_event["ref"]
    location    = parent_event.get("location", "")
    before      = parent_event.get("travel_buffer_before_min", 0)
    after       = parent_event.get("travel_buffer_after_min", 0)

    # TravelTo node
    travel_to = create_node("Travel", {
        "name":               f"Travel to: {parent_event['name']}",
        "event_start":        (event_start - timedelta(minutes=before)).isoformat(),
        "event_end":          event_start.isoformat(),
        "location":           location,
        "collision_aware":    True,
        "attendance_mode":    "IN_PERSON",
        "parent_event_ref":   parent_ref,
        "commitment_start":   (event_start - timedelta(minutes=before)).isoformat(),
        "commitment_end":     event_start.isoformat(),
    }, conn)

    # TravelFrom node
    travel_from = create_node("Travel", {
        "name":               f"Travel from: {parent_event['name']}",
        "event_start":        event_end.isoformat(),
        "event_end":          (event_end + timedelta(minutes=after)).isoformat(),
        "location":           location,
        "collision_aware":    True,
        "attendance_mode":    "IN_PERSON",
        "parent_event_ref":   parent_ref,
        "commitment_start":   event_end.isoformat(),
        "commitment_end":     (event_end + timedelta(minutes=after)).isoformat(),
    }, conn)

    # Link using existing edge creation
    create_edge(travel_to["vertex_id"],  "PRECEDES",     parent_event["vertex_id"], conn)
    create_edge(parent_event["vertex_id"], "FOLLOWED_BY", travel_from["vertex_id"], conn)
```

**Travel nodes in notifications:** When a collision involves a Travel node, the notification summary surfaces the parent appointment name, not the travel node name:

```python
def display_name_for_node(node: dict) -> str:
    if node["label"] == "Travel" and node.get("parent_event_ref"):
        parent = fetch_node_by_ref(node["parent_event_ref"])
        return f"Travel to/from '{parent['name']}'"
    return node["name"]
```

**On parent event reschedule:** Travel child nodes must be deleted and respawned. Add to the event update handler:

```python
def on_event_update(vertex_id: int, updated_props: dict, conn) -> None:
    if "event_start" in updated_props or "event_end" in updated_props:
        delete_travel_children(vertex_id, conn)
        spawn_travel_nodes(fetch_node(vertex_id, conn), conn)
```

### 2.4 Dependency Links — Different Pattern, Same Logic

Dependency links use the same edge creation machinery as travel nodes but with different edge types and different behaviour on parent change.

**Edge types:**

| Pattern | Edge type | Behaviour on parent change |
|---|---|---|
| Travel nodes | `PRECEDES` / `FOLLOWED_BY` | Cascade — delete and respawn |
| Dependencies | `DEPENDS_ON` / `REQUIRES` | Notify — fire PATTERN_GAP |

Examples:
- Referral `REQUIRES` Specialist appointment
- Finance approval `DEPENDS_ON` Settlement date
- Building inspection `DEPENDS_ON` Purchase contract
- Conveyancer booking `DEPENDS_ON` Settlement date

**On parent event date change:**

```python
def on_event_date_change(vertex_id: int, new_date: str, conn) -> None:
    # 1. Delete and respawn travel children (derived)
    delete_travel_children(vertex_id, conn)
    spawn_travel_nodes(fetch_node(vertex_id, conn), conn)

    # 2. Check dependency links — notify if dependent timing is now wrong
    dependents = fetch_dependents(vertex_id, conn)
    # MATCH (dep)-[:REQUIRES|DEPENDS_ON]->(parent {vertex_id: vertex_id})
    for dep in dependents:
        if dependent_timing_at_risk(dep, new_date):
            fire_pattern_gap_notification(
                dep, parent_vertex_id=vertex_id, reason="parent_date_changed"
            )
```

```python
def dependent_timing_at_risk(dep: dict, parent_new_date: str) -> bool:
    """
    Returns True if the dependent event's date is now before or
    uncomfortably close to the parent's new date.
    Example: referral on Nov 1, specialist moved to Oct 15 — referral is now after specialist.
    """
    dep_date    = parse_date(dep.get("event_date"))
    parent_date = parse_date(parent_new_date)
    return dep_date is None or dep_date >= parent_date
```

---

## Part 3 — Notification Layer

### 3.1 Core Table

**File: `postgres/migrations/XX_notifications.sql`**

```sql
CREATE TABLE IF NOT EXISTS personal.notifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    type                TEXT NOT NULL CHECK (type IN (
                            'COLLISION',
                            'SYSTEM_HEALTH',
                            'PATTERN_GAP',
                            'STALENESS',
                            'ACTION_REQUIRED'
                        )),
    severity            TEXT NOT NULL DEFAULT 'MEDIUM'
                        CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW')),

    status              TEXT NOT NULL DEFAULT 'DETECTED'
                        CHECK (status IN (
                            'DETECTED', 'TRIAGED', 'PENDING', 'RESOLVED', 'IGNORED'
                        )),

    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}',
    node_refs           JSONB NOT NULL DEFAULT '[]',
    options             JSONB NOT NULL DEFAULT '[]',

    -- Deduplication — one active notification per dedup_key
    dedup_key           TEXT UNIQUE,

    -- Resolution
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,
    resolution_key      TEXT,
    resolution_note     TEXT,

    -- Auto-expiry (SYSTEM_HEALTH self-heal, PENDING reminders)
    expires_at          TIMESTAMPTZ
);

CREATE INDEX idx_notifications_status   ON personal.notifications (status);
CREATE INDEX idx_notifications_type     ON personal.notifications (type);
CREATE INDEX idx_notifications_severity ON personal.notifications (severity);
CREATE INDEX idx_notifications_created  ON personal.notifications (created_at DESC);

GRANT SELECT, INSERT, UPDATE ON personal.notifications TO familybrain_dashboard;
GRANT SELECT ON personal.notifications TO familybrain_wa;
GRANT INSERT ON personal.notifications TO familybrain_agent;
```

### 3.2 Notification Types

#### COLLISION

Detects temporal and resource conflicts between collision-aware event nodes.

**Temporal** — `commitment_start`/`commitment_end` windows overlap AND `requires_who` sets intersect.  
**Resource** — same person needed within `RESOURCE_BUFFER_HOURS = 2`, no temporal overlap.

Note: Because travel child nodes now carry their own `commitment_start`/`commitment_end`, the collision detector treats them as first-class events. No special travel logic needed at detection time — the nodes are already in the flat event set.

**Dedup key:** `COLLISION:{min_vertex_id}:{max_vertex_id}`

**Severity:**
- HIGH — PropertyEvent or FinancialEvent involved
- MEDIUM — Appointment or SportingEvent
- LOW — everything else

**Contextual options:**
- Reschedule for Appointment, SchoolEvent, SportingEvent nodes
- Delegate for PropertyEvent, FinancialEvent nodes
- Acknowledge — handling manually
- IGNORE (always static, always last)

**Graph writeback on resolve:**
```
collision_status: 'RESOLVED' | 'IGNORED'
collision_notification_id: <uuid>
```

---

#### SYSTEM_HEALTH

Infrastructure failures. No graph nodes. Payload only.

| Service | Trigger | Severity |
|---|---|---|
| Email poller | Last successful poll > 2 hours | HIGH |
| n8n workflow | Any workflow in ERROR state | HIGH |
| Ollama | Health check fails | HIGH |
| Scheduled agent | > 2× expected interval since last run | MEDIUM |
| Graph write silence | No new nodes/edges in > 24 hours | MEDIUM |
| WhatsApp bridge | Last message processed > 6 hours (waking hours only) | LOW |

**Dedup key:** `SYSTEM_HEALTH:{service}`

**Auto-resolve:** When health check passes, detector updates open notification to RESOLVED automatically. Only type where auto-resolution is permitted in v1.

**Options:**
- A: Acknowledged — investigating
- IGNORE: Known issue — suppress 24 hours (sets `expires_at`)

---

#### PATTERN_GAP

Detects expected things that aren't there. Two sources:

**1. Graph-based gap rules** (`personal.notification_gap_rules` table):

```sql
CREATE TABLE IF NOT EXISTS personal.notification_gap_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    anchor_label    TEXT NOT NULL,
    anchor_filter   JSONB,
    expected_label  TEXT NOT NULL,
    expected_rel    TEXT,
    window_days     INTEGER NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'MEDIUM'
);
```

Seed rules:
```sql
INSERT INTO personal.notification_gap_rules
    (name, anchor_label, anchor_filter, expected_label, expected_rel, window_days, severity)
VALUES
    ('Dentist appointment gap',
     'Reminder', '{"category": "medical", "name_contains": "dentist"}',
     'Appointment', 'SCHEDULES', 30, 'MEDIUM'),

    ('Specialist referral gap',
     'Appointment', '{"subtype": "specialist"}',
     'Appointment', 'REQUIRES', 60, 'HIGH'),

    ('Property settlement children gap',
     'PropertyEvent', '{"subtype": "contract"}',
     'PropertyEvent', 'DEPENDS_ON', 60, 'HIGH'),

    ('School fee invoice gap',
     'SchoolEvent', '{"subtype": "term_start"}',
     'Invoice', null, 14, 'LOW');
```

**2. Asset rule watcher gaps** — see Part 4. When a rule has `auto_create: false` and the expected event is missing, the rule watcher fires a PATTERN_GAP notification with:

```json
{
  "source": "asset_rule",
  "asset_id": 42,
  "asset_name": "Honda CR-V",
  "rule_name": "Car service",
  "expected_event_type": "SERVICE",
  "trigger_date": "2026-08-15"
}
```

**3. Dependency date change gaps** — fired by `on_event_date_change()` when a dependent node's timing is at risk (see section 2.4).

**Dedup key:** `PATTERN_GAP:{rule_id_or_source}:{anchor_vertex_id_or_asset_id}`

**Options:**
- A: Schedule now — I'll create the event (PENDING)
- B: Not needed this cycle
- IGNORE: Disable this rule

---

#### STALENESS

Nodes overdue for an update.

```python
STALENESS_RULES = {
    "Appointment": {
        "condition": "event_date < now() - interval '1 day' AND outcome IS NULL",
        "threshold_hours": 24,
        "severity": "LOW",
        "title": "Appointment outcome not recorded",
    },
    "PropertyEvent": {
        "condition": "subtype IN ('inspection', 'auction') AND event_date < now() - interval '3 days' AND outcome IS NULL",
        "threshold_hours": 72,
        "severity": "MEDIUM",
        "title": "Property event outcome not recorded",
    },
    "Invoice": {
        "condition": "due_date < now() AND status = 'PENDING'",
        "threshold_hours": 0,
        "severity": "HIGH",
        "title": "Invoice overdue",
    },
    "Asset": {
        "condition": "next_event_date < now() AND event_gen_enabled = true",
        "threshold_hours": 48,
        "severity": "MEDIUM",
        "title": "Asset next event date is past",
    },
}
```

**Dedup key:** `STALENESS:{label}:{vertex_id_or_asset_id}`

**Options:**
- A: Record outcome now (PENDING)
- B: Still active — reset staleness clock (sets `staleness_suppressed_until`)
- IGNORE: Not tracking outcomes for this type

---

#### ACTION_REQUIRED

Pending decisions waiting beyond threshold.

| Source | Condition | Severity |
|---|---|---|
| Curator staging queue | Staged node waiting > 24 hours | MEDIUM |
| Collision resolved as PENDING | No follow-through in 48 hours | HIGH |
| PATTERN_GAP "schedule now" | No event created in 7 days | MEDIUM |
| Asset rule `auto_create: false` flagged | No booking made in lead time window | HIGH |

**Dedup key:** `ACTION_REQUIRED:{source}:{source_id}`

**Options:**
- A: Done — action completed (RESOLVED)
- B: Still working — remind in 24 hours (PENDING, sets `expires_at`)
- IGNORE: No longer relevant

---

### 3.3 Detector Architecture

**File: `ingestor/src/notifications/`**

```
notifications/
  __init__.py
  base_detector.py
  collision_detector.py
  system_health_detector.py
  pattern_gap_detector.py
  staleness_detector.py
  action_required_detector.py
  runner.py
```

**`base_detector.py`**

```python
class BaseDetector:
    def __init__(self, conn):
        self.conn = conn

    def upsert_notification(self, notification: dict) -> bool:
        """
        Insert if dedup_key not already open (status NOT IN RESOLVED, IGNORED).
        Returns True if inserted, False if skipped.
        """
        ...

    def auto_resolve(self, dedup_key: str, note: str = None):
        """Used by SYSTEM_HEALTH on service recovery."""
        ...

    def write_graph_node_facts(self, vertex_id: int, facts: dict):
        """Delegates to set_node_facts() from hydration layer."""
        ...

    def run(self) -> dict:
        """Override in each detector. Returns {detected, skipped, resolved}."""
        raise NotImplementedError
```

**`runner.py`**

```python
def run_all_detectors(conn):
    detectors = [
        CollisionDetector(conn),
        SystemHealthDetector(conn),
        PatternGapDetector(conn),
        StalenessDetector(conn),
        ActionRequiredDetector(conn),
    ]
    results = {}
    for detector in detectors:
        try:
            results[detector.__class__.__name__] = detector.run()
        except Exception as e:
            log_audit(conn, "notification_runner", "ERROR", str(e))
    return results
```

### 3.4 Resolution Endpoint

**File: `graph-api/src/routers/notifications.py`**

```python
@router.get("/notifications")
async def list_notifications(
    status: str = "DETECTED,TRIAGED,PENDING",
    type: str = None,
    severity: str = None,
    limit: int = 50,
    db = Depends(get_db)
): ...

@router.get("/notifications/counts")
async def notification_counts(db = Depends(get_db)):
    # Returns: { "total": 7, "HIGH": 2, "by_type": {"COLLISION": 3, ...} }
    ...

@router.patch("/notifications/{notification_id}/resolve")
async def resolve_notification(
    notification_id: UUID,
    resolution_key: str,
    resolved_by: str,
    resolution_note: str = None,
    db = Depends(get_db)
):
    # 1. Fetch notification
    # 2. Validate resolution_key in options list
    # 3. Determine new status:
    #    IGNORE → IGNORED
    #    "remind later" actions → PENDING + set expires_at
    #    otherwise → RESOLVED
    # 4. Update personal.notifications
    # 5. Dispatch type-specific graph writeback
    # 6. Log to audit_log
    ...
```

Type-specific writeback dispatch:

```python
WRITEBACK_HANDLERS = {
    "COLLISION":        handle_collision_writeback,
    "SYSTEM_HEALTH":    None,   # no graph writeback
    "PATTERN_GAP":      handle_pattern_gap_writeback,
    "STALENESS":        handle_staleness_writeback,
    "ACTION_REQUIRED":  None,
}
```

---

## Part 4 — Rule Watcher (Maintenance Job)

### 4.1 Concept

The rule watcher iterates all active assets, evaluates each rule, and determines whether the expected event exists. If not:
- `auto_create: true` — creates the event in `personal.event` and an event node in the graph
- `auto_create: false` — fires a PATTERN_GAP notification

The rule watcher is also responsible for updating `asset.next_event_date` after any event creation or completion.

### 4.2 File: `ingestor/src/rule_watcher.py`

```python
def run_rule_watcher(conn) -> dict:
    """
    Main entry point. Called by n8n maintenance job.
    Returns summary of actions taken.
    """
    assets = fetch_active_assets(conn)
    results = {"events_created": 0, "gaps_fired": 0, "errors": 0}

    for asset in assets:
        if not asset["event_gen_enabled"]:
            continue
        try:
            process_asset_rules(asset, conn, results)
            update_asset_next_event_date(asset, conn)
        except Exception as e:
            log_audit(conn, "rule_watcher", "ERROR",
                      f"Asset {asset['id']}: {str(e)}")
            results["errors"] += 1

    return results


def process_asset_rules(asset: dict, conn, results: dict) -> None:
    rules = asset.get("rules", [])
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        trigger_date = resolve_trigger_date(asset, rule)
        if trigger_date is None:
            continue

        lead_time = rule.get("lead_time_days", 14)
        target_date = trigger_date - timedelta(days=lead_time)

        if date.today() < target_date:
            continue  # Not yet in lead time window

        # Check if event already exists
        if event_already_exists(asset["id"], rule["event_type"], trigger_date, conn):
            continue

        if rule.get("auto_create", False):
            create_event_from_rule(asset, rule, trigger_date, conn)
            results["events_created"] += 1
        else:
            fire_pattern_gap(asset, rule, trigger_date, conn)
            results["gaps_fired"] += 1


def resolve_trigger_date(asset: dict, rule: dict):
    """
    Resolves the trigger date from asset facts or last/next event date.
    Returns a date object or None if unresolvable.
    """
    source = rule.get("trigger_source", "")
    facts  = asset.get("facts", {})

    if source.startswith("facts."):
        key = source.replace("facts.", "")
        val = facts.get(key)
        return parse_date(val) if val else None

    if source == "last_event_date":
        last = asset.get("last_event_date")
        if not last:
            return None
        recurrence_days = rule.get("recurrence_days")
        if recurrence_days:
            return parse_date(last) + timedelta(days=recurrence_days)
        if rule.get("recurrence") == "annual":
            return parse_date(last) + timedelta(days=365)
        return None

    return None


def event_already_exists(asset_id: int, event_type: str,
                          trigger_date: date, conn) -> bool:
    """
    Returns True if a non-cancelled event of this type already exists
    for this asset within a 14-day window of the trigger date.
    """
    ...


def create_event_from_rule(asset: dict, rule: dict,
                            trigger_date: date, conn) -> None:
    """
    Creates a personal.event row and corresponding graph event node.
    Sets asset_id and generated_by_rule on the event.
    Spawns travel child nodes if rule specifies travel buffers.
    """
    event = {
        "name":               rule["event_label"],
        "event_type":         rule["event_type"],
        "event_date":         trigger_date.isoformat(),
        "asset_id":           asset["id"],
        "generated_by_rule":  rule["name"],
        "status":             "SCHEDULED",
        "collision_aware":    rule.get("collision_aware", True),
        "attendance_mode":    rule.get("attendance_mode", "IN_PERSON"),
        "travel_buffer_before_min": rule.get("travel_buffer_before_min"),
        "travel_buffer_after_min":  rule.get("travel_buffer_after_min"),
    }
    event_id = insert_event_row(event, conn)
    event["ref"] = f"personal.event:{event_id}"

    # Create graph node
    vertex = create_event_node(event, conn)
    event["vertex_id"] = vertex["vertex_id"]

    # Derive and store commitment window
    cs, ce = derive_commitment_window(event)
    set_node_facts(vertex["vertex_id"], {
        "commitment_start": cs.isoformat(),
        "commitment_end":   ce.isoformat(),
    }, conn)

    # Spawn travel children if IN_PERSON with buffers
    spawn_travel_nodes(event, conn)

    # Link asset → event in graph
    asset_vertex = fetch_asset_node(asset["id"], conn)
    if asset_vertex:
        create_edge(asset_vertex["vertex_id"], "HAS_EVENT",
                    vertex["vertex_id"], conn)

    log_audit(conn, "rule_watcher", "CREATE_EVENT",
              f"Asset {asset['id']} rule '{rule['name']}' → event {event_id}")


def update_asset_next_event_date(asset: dict, conn) -> None:
    """
    Updates asset.next_event_date to the nearest upcoming event date
    for this asset_id.
    """
    ...
```

### 4.3 n8n Integration

**Workflow: `Rule Watcher — Scheduled`**

- **Trigger**: Cron daily at 6:00am
- **Step 1**: HTTP POST → `graph-api/run-rule-watcher`
- **Step 2**: Log result summary to audit_log
- **Step 3**: If any HIGH PATTERN_GAP notifications were fired, send WhatsApp summary

Also trigger rule watcher on-demand when:
- A new asset is created
- An asset's `facts` or `rules` are updated
- An event is marked complete (to advance next_event_date)

---

## Part 5 — Dashboard

### 5.1 Notifications Page

**Page:** `dashboard/src/app/notifications/page.tsx`  
**Component:** `dashboard/src/components/NotificationFeed.tsx`

- Header with total open count badge
- Filter bar: All | COLLISION | SYSTEM_HEALTH | PATTERN_GAP | STALENESS | ACTION_REQUIRED + severity toggles
- Cards sorted: HIGH first, then by `created_at DESC`
- Resolved section: collapsed accordion, last 20

**Type colours:**
- COLLISION — red
- SYSTEM_HEALTH — orange
- PATTERN_GAP — blue
- STALENESS — amber
- ACTION_REQUIRED — purple

**Notification card:**
- Type chip + severity badge
- Title (bold) + summary
- Node refs — name, label, date chips
- Options: contextual outlined buttons + IGNORE as muted text link
- Time since detected

### 5.2 Assets Page

**Page:** `dashboard/src/app/assets/page.tsx`

- Asset list grouped by `asset_type`
- Each asset card shows: name, type, subtype, `next_event_date`, status
- Expand to show rules, generated events, open notifications linked to this asset
- Actions: edit facts, toggle `event_gen_enabled`, trigger rule watcher on-demand for single asset

### 5.3 Sidebar Badge

Add "Notifications" to sidebar nav with live count from `/notifications/counts`.  
Badge turns red when any HIGH severity notification is open.

---

## Part 6 — Build Order

Work through in this sequence. Each phase should be independently testable before proceeding.

**Phase 1 — Foundation**
1. Migration: `personal.notifications` table
2. Migration: `personal.notification_gap_rules` table + seed rows
3. Migration: `personal.asset` table
4. Add `asset_id` and `generated_by_rule` to `personal.event`
5. Add `collision_aware` + `COLLISION_AWARE_LABELS` to `ingestor/src/graph.py`
6. Add `attendance_mode`, `travel_buffer_*`, `commitment_start/end` to event node property convention
7. Backfill `collision_aware` on existing event nodes — one-off Cypher migration script

**Phase 2 — Event model**
8. `derive_commitment_window()` in ingestor — store on nodes at write time
9. `spawn_travel_nodes()` — using existing edge creation
10. `on_event_date_change()` handler — delete/respawn travel, check dependency links

**Phase 3 — Detectors**
11. `base_detector.py`
12. `collision_detector.py` — temporal first, resource second, uses `commitment_start/end`
13. `system_health_detector.py`
14. `pattern_gap_detector.py` — graph rules + asset rule source + dependency date change source
15. `staleness_detector.py`
16. `action_required_detector.py`
17. `runner.py`

**Phase 4 — Rule watcher**
18. `rule_watcher.py` — `resolve_trigger_date`, `event_already_exists`, `create_event_from_rule`
19. Wire into n8n daily cron + on-demand trigger

**Phase 5 — API**
20. `graph-api/src/routers/notifications.py` — list, counts, resolve endpoints
21. `graph-api/src/routers/assets.py` — CRUD + rule watcher trigger
22. Wire type-specific writeback handlers

**Phase 6 — Dashboard**
23. `/notifications` page + `NotificationFeed` component
24. `/assets` page
25. Sidebar badge wired to `/notifications/counts`

**Phase 7 — n8n**
26. Notification runner — 15-minute cron + WhatsApp HIGH alerts
27. System health — 5-minute cron (separate, faster cadence)
28. Rule watcher — 6am daily cron

---

## Part 7 — Open Decisions

Resolve before building the affected phase.

| Decision | Affects | Question |
|---|---|---|
| `requires_who` convention | Collision detector (resource type) | Does this property exist on event nodes? If not, add to ingestor and backfill. Without it, resource collision detection cannot distinguish person conflicts. |
| `event_end` default | Commitment window derivation | For events missing `event_end`, default is 1 hour. Confirm or adjust. |
| `outcome` property convention | Staleness detector | Does `outcome` exist on Appointment and PropertyEvent nodes? If not, add to ingestor. |
| Service health endpoints | System health detector | Confirm: Ollama health URL, n8n API endpoint + auth token, email poller heartbeat location (audit_log query or dedicated heartbeat table). |
| DB role naming | `resolved_by` field | Confirm role names for dashboard user and wa-agent — should match existing DB account convention. |
| `collision_aware` backfill | Phase 1 | Write as a standalone Cypher migration script. Do not assume new ingest covers existing nodes. |
| `person_id` for household assets | Asset table | Confirm null = household asset convention is consistent with existing `family_member` modelling. |
| Asset graph node convention | Graph layer | Confirm `fact_*` key prefix for asset node properties is consistent with the hydration build doc convention. |

---

## Part 8 — Ingestor: Asset Recognition and Event Generation

### 8.1 Where This Fits in the Pipeline

The existing ingest pipeline processes incoming content (email, file sync, WhatsApp message, manual chat) through these steps:

```
raw content
  → channel handler (email / file / WhatsApp / chat)
    → content extractor (text, metadata)
      → LLM classification (entity type, extracted fields)
        → graph write (create_node, create_edge, set_node_facts)
          → vector embed
            → audit log
```

Asset recognition and event generation slots in after LLM classification and before graph write:

```
raw content
  → channel handler
    → content extractor
      → LLM classification
        → [NEW] asset classifier          ← is this an asset or event-on-asset?
          → [NEW] asset upsert / match    ← write or find the asset row
            → graph write
              → [NEW] rule trigger        ← immediate rule evaluation for this asset
                → vector embed
                  → audit log
```

Claude Code: before implementing, read the existing classification step and graph write entry points to confirm function names and call signatures. The structure above is the intended insertion order — adapt to the actual code.

### 8.2 Asset Classifier

**File: `ingestor/src/asset_classifier.py`**

Called after LLM classification returns an entity type and extracted fields. Determines whether the content represents:

- `ASSET` — a new or updated persistent thing (car insurance renewal email, script photo, new subscription)
- `ASSET_EVENT` — an event attached to an existing asset (service booking for the car, script pickup, renewal completion)
- `OTHER` — not asset-related, continue normal graph write path

```python
# Asset-type labels that trigger asset classification
ASSET_ENTITY_TYPES = {
    "vehicle", "medication", "script", "prescription",
    "property", "subscription", "device", "pet",
    "insurance", "registration", "renewal", "service_booking",
    "licence", "passport", "ndis_plan", "warranty"
}

# Mapping from LLM entity type to asset_type value
ENTITY_TO_ASSET_TYPE = {
    "vehicle":        "vehicle",
    "medication":     "medication",
    "script":         "medication",
    "prescription":   "medication",
    "property":       "property",
    "subscription":   "subscription",
    "device":         "device",
    "pet":            "pet",
    "insurance":      None,   # determine from facts (vehicle insurance → vehicle asset)
    "registration":   None,   # determine from facts
    "renewal":        None,   # determine from facts
    "service_booking": None,  # ASSET_EVENT — find existing asset
    "licence":        "person",
    "passport":       "person",
    "ndis_plan":      "person",
    "warranty":       "device",
}


def classify_for_asset(classification: dict) -> dict:
    """
    Takes the LLM classification result and returns asset routing decision.

    Args:
        classification: {
            "entity_type": str,
            "extracted_fields": dict,
            "confidence": float,
            "source": "email" | "file" | "whatsapp" | "chat"
        }

    Returns: {
        "route": "ASSET" | "ASSET_EVENT" | "OTHER",
        "asset_type": str | None,
        "asset_subtype": str | None,
        "is_update": bool,       # True if this looks like updating an existing asset
        "event_type": str | None # For ASSET_EVENT route
    }
    """
    entity_type = classification.get("entity_type", "").lower()

    if entity_type not in ASSET_ENTITY_TYPES:
        return {"route": "OTHER"}

    asset_type = ENTITY_TO_ASSET_TYPE.get(entity_type)

    # Ambiguous types — resolve from extracted fields
    if asset_type is None:
        asset_type = resolve_asset_type_from_fields(
            entity_type, classification.get("extracted_fields", {})
        )

    # Service bookings and events on existing assets
    if entity_type in ("service_booking",):
        return {
            "route": "ASSET_EVENT",
            "asset_type": asset_type,
            "asset_subtype": None,
            "is_update": False,
            "event_type": derive_event_type(entity_type, classification)
        }

    # Renewal / update of existing asset
    is_update = entity_type in ("renewal", "insurance", "registration")

    return {
        "route": "ASSET",
        "asset_type": asset_type,
        "asset_subtype": derive_subtype(asset_type, classification),
        "is_update": is_update,
        "event_type": None
    }


def resolve_asset_type_from_fields(entity_type: str, fields: dict) -> str | None:
    """
    For ambiguous entity types (insurance, renewal, registration),
    determine the asset_type from extracted fields.
    e.g. fields containing 'rego', 'vehicle', 'car' → 'vehicle'
    """
    field_text = " ".join(str(v) for v in fields.values()).lower()
    if any(w in field_text for w in ("rego", "vehicle", "car", "motorcycle")):
        return "vehicle"
    if any(w in field_text for w in ("property", "home", "building", "contents")):
        return "property"
    if any(w in field_text for w in ("phone", "laptop", "device", "appliance")):
        return "device"
    if any(w in field_text for w in ("subscription", "plan", "membership")):
        return "subscription"
    return None


def derive_event_type(entity_type: str, classification: dict) -> str:
    mapping = {
        "service_booking": "SERVICE",
        "renewal":         "RENEWAL",
        "registration":    "REGO_RENEWAL",
    }
    return mapping.get(entity_type, "EVENT")


def derive_subtype(asset_type: str, classification: dict) -> str | None:
    fields = classification.get("extracted_fields", {})
    if asset_type == "vehicle":
        return fields.get("vehicle_type", "car").lower()
    if asset_type == "property":
        return fields.get("property_type", "residential").lower()
    if asset_type == "medication":
        return "prescription" if "script" in str(fields).lower() else "OTC"
    return None
```

### 8.3 Asset Upsert

**File: `ingestor/src/asset_writer.py`**

Handles both new asset creation and updating existing asset facts.

```python
def upsert_asset(
    asset_route: dict,
    extracted_fields: dict,
    source: str,
    conn
) -> dict:
    """
    Creates or updates a personal.asset row.
    Returns the asset dict including id.

    asset_route: output from classify_for_asset()
    extracted_fields: LLM-extracted fields from content
    source: channel identifier for audit log
    """
    asset_type = asset_route["asset_type"]
    if asset_type is None:
        return None

    # Build facts dict — validate against schema, warn on unknown keys
    facts = build_asset_facts(asset_type, extracted_fields)

    # Try to find existing asset by name/type/facts match
    existing = find_existing_asset(asset_type, facts, conn)

    if existing and asset_route.get("is_update"):
        # Merge new facts into existing — don't overwrite with nulls
        merged_facts = {**existing["facts"], **{k: v for k, v in facts.items() if v}}
        update_asset(existing["id"], merged_facts, conn)
        asset = {**existing, "facts": merged_facts}
        log_audit(conn, "asset_writer", "UPDATE_ASSET",
                  f"Asset {existing['id']} updated from {source}")
    elif existing:
        # Content is about an existing asset but not flagged as update
        # Return existing without modifying
        asset = existing
    else:
        # Create new asset
        asset_id = insert_asset({
            "name":         derive_asset_name(asset_type, facts, extracted_fields),
            "asset_type":   asset_type,
            "subtype":      asset_route.get("asset_subtype"),
            "status":       "active",
            "facts":        facts,
            "rules":        default_rules_for_type(asset_type),
            "event_gen_enabled": True,
        }, conn)
        asset = fetch_asset(asset_id, conn)
        # Create graph node
        create_asset_graph_node(asset, conn)
        log_audit(conn, "asset_writer", "CREATE_ASSET",
                  f"New {asset_type} asset {asset_id} from {source}")

    return asset


def find_existing_asset(asset_type: str, facts: dict, conn) -> dict | None:
    """
    Attempts to match incoming content to an existing asset row.
    Strategy:
    1. Exact match on unique identifier from facts (rego, script_number, serial_number)
    2. Name similarity match using pg_trgm or vector similarity on asset.name
    3. Return None if no confident match (confidence threshold: 0.85)
    """
    # 1. Unique identifier match
    unique_keys = {
        "vehicle":      "rego",
        "medication":   "script_number",
        "device":       "serial_number",
        "subscription": None,   # no reliable unique key — fall through to name match
        "property":     "lot_plan",
        "person":       None,
    }
    uid_key = unique_keys.get(asset_type)
    if uid_key and facts.get(uid_key):
        result = query_asset_by_fact(asset_type, uid_key, facts[uid_key], conn)
        if result:
            return result

    # 2. Name similarity — use pg_trgm similarity against asset.name
    candidate_name = derive_asset_name(asset_type, facts, {})
    if candidate_name:
        result = query_asset_by_name_similarity(asset_type, candidate_name, conn, threshold=0.85)
        if result:
            return result

    return None


def build_asset_facts(asset_type: str, extracted_fields: dict) -> dict:
    """
    Maps extracted fields to the fact schema for this asset type.
    Logs warnings for unknown keys but does not drop them.
    """
    schema = ASSET_FACT_SCHEMAS.get(asset_type, {})
    required = schema.get("required", [])
    optional = schema.get("optional", [])
    known_keys = set(required + optional)

    facts = {}
    for k, v in extracted_fields.items():
        if v is not None and v != "":
            facts[k] = v
            if k not in known_keys:
                log_audit(None, "asset_writer", "UNKNOWN_FACT_KEY",
                          f"{asset_type}: unknown key '{k}' — storing but not in schema")
    return facts


def default_rules_for_type(asset_type: str) -> list:
    """
    Returns the default rule set for a new asset of this type.
    Rules can be customised after creation via dashboard.
    """
    defaults = {
        "vehicle": [
            {
                "name": "Rego renewal",
                "event_type": "REGO_RENEWAL",
                "event_label": "Vehicle registration due",
                "trigger_source": "facts.rego_expiry",
                "lead_time_days": 30,
                "recurrence": "annual",
                "auto_create": True,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 30,
                "severity_if_missing": "HIGH",
                "enabled": True
            },
            {
                "name": "Insurance renewal",
                "event_type": "INSURANCE_RENEWAL",
                "event_label": "Vehicle insurance renewal due",
                "trigger_source": "facts.insurance_expiry",
                "lead_time_days": 21,
                "recurrence": "annual",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "HIGH",
                "enabled": True
            },
            {
                "name": "Car service",
                "event_type": "SERVICE",
                "event_label": "Car service due",
                "trigger_source": "last_event_date",
                "lead_time_days": 14,
                "recurrence": "interval",
                "recurrence_days": 180,
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 60,
                "severity_if_missing": "MEDIUM",
                "enabled": True
            }
        ],
        "medication": [
            {
                "name": "Script renewal",
                "event_type": "SCRIPT_RENEWAL",
                "event_label": "Script renewal due",
                "trigger_source": "last_event_date",
                "lead_time_days": 7,
                "recurrence": "interval",
                "recurrence_days": None,   # calculated from days_supply
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "IN_PERSON",
                "severity_if_missing": "HIGH",
                "enabled": True
            }
        ],
        "subscription": [
            {
                "name": "Renewal reminder",
                "event_type": "RENEWAL",
                "event_label": "Subscription renewal due",
                "trigger_source": "facts.renewal_date",
                "lead_time_days": 14,
                "recurrence": "interval",
                "recurrence_days": None,   # from facts.renewal_period_days
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "MEDIUM",
                "enabled": True
            }
        ],
        "person": [
            {
                "name": "Passport renewal",
                "event_type": "PASSPORT_RENEWAL",
                "event_label": "Passport expiring",
                "trigger_source": "facts.passport_expiry",
                "lead_time_days": 180,
                "recurrence": "once",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "IN_PERSON",
                "severity_if_missing": "HIGH",
                "enabled": True
            },
            {
                "name": "NDIS plan review",
                "event_type": "NDIS_REVIEW",
                "event_label": "NDIS plan review due",
                "trigger_source": "facts.ndis_plan_end",
                "lead_time_days": 60,
                "recurrence": "once",
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 30,
                "travel_buffer_after_min": 30,
                "severity_if_missing": "HIGH",
                "enabled": True
            }
        ],
        "device": [
            {
                "name": "Warranty expiry",
                "event_type": "WARRANTY_EXPIRY",
                "event_label": "Warranty expiring",
                "trigger_source": "facts.warranty_expiry",
                "lead_time_days": 30,
                "recurrence": "once",
                "auto_create": True,
                "collision_aware": False,
                "attendance_mode": "ONLINE",
                "severity_if_missing": "LOW",
                "enabled": True
            }
        ],
        "pet": [
            {
                "name": "Vaccination due",
                "event_type": "VACCINATION",
                "event_label": "Pet vaccination due",
                "trigger_source": "facts.vaccination_due",
                "lead_time_days": 14,
                "recurrence": "annual",
                "auto_create": False,
                "collision_aware": True,
                "attendance_mode": "IN_PERSON",
                "travel_buffer_before_min": 15,
                "travel_buffer_after_min": 15,
                "severity_if_missing": "MEDIUM",
                "enabled": True
            }
        ],
    }
    return defaults.get(asset_type, [])
```

### 8.4 Asset Matcher (for ASSET_EVENT route)

**File: `ingestor/src/asset_matcher.py`**

When content is classified as `ASSET_EVENT` — a service booking, renewal completion, pickup — find the asset it belongs to before writing the event.

```python
def match_asset_for_event(
    asset_route: dict,
    extracted_fields: dict,
    conn
) -> dict | None:
    """
    Finds the existing asset that an incoming event belongs to.
    Returns asset dict or None if no confident match.

    On None: event is written to graph without asset_id.
    A PATTERN_GAP or ACTION_REQUIRED notification is NOT fired here —
    the rule watcher handles orphaned events on its next run.
    """
    asset_type = asset_route.get("asset_type")
    if asset_type is None:
        # Try to infer asset type from event content
        asset_type = infer_asset_type_from_event(extracted_fields)

    if asset_type is None:
        return None

    # Reuse find_existing_asset — same matching logic
    facts = build_asset_facts(asset_type, extracted_fields)
    return find_existing_asset(asset_type, facts, conn)


def infer_asset_type_from_event(fields: dict) -> str | None:
    """
    Last-resort type inference from event field content.
    """
    text = " ".join(str(v) for v in fields.values()).lower()
    if any(w in text for w in ("car", "vehicle", "rego", "service", "mechanic")):
        return "vehicle"
    if any(w in text for w in ("script", "prescription", "medication", "pharmacy")):
        return "medication"
    if any(w in text for w in ("subscription", "renewal", "plan", "membership")):
        return "subscription"
    return None
```

### 8.5 Immediate Rule Trigger

**File: `ingestor/src/asset_writer.py`** (addition)

After any asset create or update, trigger rule evaluation immediately for that asset without waiting for the daily cron.

```python
def trigger_rules_for_asset(asset: dict, conn) -> dict:
    """
    Runs rule_watcher.process_asset_rules() for a single asset.
    Called inline after upsert_asset() or after matched event write.
    Returns rule watcher result summary for audit log.
    """
    from ingestor.src.rule_watcher import process_asset_rules
    results = {"events_created": 0, "gaps_fired": 0, "errors": 0}
    try:
        process_asset_rules(asset, conn, results)
        update_asset_next_event_date(asset, conn)
    except Exception as e:
        log_audit(conn, "asset_writer", "RULE_TRIGGER_ERROR",
                  f"Asset {asset['id']}: {str(e)}")
        results["errors"] += 1
    return results
```

### 8.6 Updated Ingest Pipeline Entry Point

**File: `ingestor/src/pipeline.py`** (or equivalent — read actual file name first)

Add asset routing after classification, before graph write:

```python
def process_content(raw_content: str, source: str, metadata: dict, conn) -> dict:
    """
    Main ingest pipeline. Claude Code: adapt to actual function name and signature.
    """

    # --- Existing steps ---
    extracted = extract_content(raw_content, metadata)
    classification = classify_entity(extracted, source)

    # --- New: asset routing ---
    asset_route = classify_for_asset(classification)
    asset = None

    if asset_route["route"] == "ASSET":
        asset = upsert_asset(asset_route, classification["extracted_fields"], source, conn)
        if asset:
            rule_results = trigger_rules_for_asset(asset, conn)
            log_audit(conn, "pipeline", "ASSET_RULES_TRIGGERED",
                      f"Asset {asset['id']}: {rule_results}")

    elif asset_route["route"] == "ASSET_EVENT":
        asset = match_asset_for_event(asset_route, classification["extracted_fields"], conn)
        # asset may be None — event still written, just without asset_id

    # --- Existing: graph write ---
    node = write_to_graph(classification, asset_id=asset["id"] if asset else None, conn=conn)

    # If event matched to asset, update asset last/next event dates
    if asset and asset_route["route"] == "ASSET_EVENT":
        update_asset_event_dates(asset["id"], node, conn)
        # Re-evaluate rules — new event may satisfy an outstanding gap
        trigger_rules_for_asset(fetch_asset(asset["id"], conn), conn)

    # --- Existing steps ---
    embed_node(node, conn)
    log_audit(conn, "pipeline", "INGEST_COMPLETE",
              f"source={source} entity={classification['entity_type']}"
              + (f" asset={asset['id']}" if asset else ""))

    return {"node": node, "asset": asset, "classification": classification}
```

### 8.7 Channel-Specific Notes

**Email and file sync** — primary channels, already feeding the pipeline. No channel-specific changes needed — asset routing plugs into the existing classification output.

**WhatsApp** — messages arrive via n8n → wa-agent. The wa-agent should pass content through the same `process_content()` entry point with `source="whatsapp"`. If the wa-agent currently has its own write path that bypasses the main pipeline, that path needs to be updated to route through `process_content()` or call `classify_for_asset()` + `upsert_asset()` directly before its graph write.

**Manual chat (dashboard)** — user types "add my new car, Honda CR-V, QLD rego ABC123, expires Dec 2026." This arrives as a structured or semi-structured message. Same pipeline entry with `source="chat"`. The LLM classification step should handle free-form asset descriptions confidently given the content richness.

**Structured manual entry (dashboard asset form)** — when a user creates an asset directly via the dashboard UI, bypass the classification step entirely and call `upsert_asset()` directly with the form fields pre-mapped to the asset type schema. Same `trigger_rules_for_asset()` call on completion.

### 8.8 Addition to Build Order

Insert after Phase 1 in the existing build order, before Phase 2:

**Phase 1b — Ingestor asset layer**
- Read existing `pipeline.py` (or equivalent), `graph.py`, classification step — confirm function names and signatures before writing any new code
- `ingestor/src/asset_classifier.py` — `classify_for_asset()`, `resolve_asset_type_from_fields()`
- `ingestor/src/asset_writer.py` — `upsert_asset()`, `find_existing_asset()`, `build_asset_facts()`, `default_rules_for_type()`, `trigger_rules_for_asset()`
- `ingestor/src/asset_matcher.py` — `match_asset_for_event()`, `infer_asset_type_from_event()`
- Update pipeline entry point — insert asset routing after classification
- Update wa-agent write path if it bypasses main pipeline
- Seed 2-3 real assets manually via dashboard to validate rule generation before wiring ingestor

### 8.9 Additional Open Decisions

Add to Part 7:

| Decision | Affects | Question |
|---|---|---|
| Pipeline entry point file name | Phase 1b | What is the actual file and function name for the main ingest pipeline? Read before writing. |
| wa-agent write path | Phase 1b | Does wa-agent call the main pipeline or write to graph directly? If direct, needs updating. |
| Classification output schema | Asset classifier | What fields does the existing LLM classification step return? `entity_type` and `extracted_fields` are assumed — confirm actual keys. |
| Asset name derivation | Asset upsert | How should asset name be derived when not explicit in content? e.g. "Honda CR-V (QLD ABC123)" as a formula per type. |
| pg_trgm availability | Asset matcher | Is `pg_trgm` extension enabled in the Postgres instance? Required for name similarity matching. If not, use vector similarity against asset name embeddings instead. |

---

## Part 9 — Build Scheduler (Claude Code Session Plan)

**Critical instruction for every session:**
> Read `BUILD-familybrain-consolidated.md` in full before writing any code.
> Read all existing files in scope for this session before modifying them.
> Implement this session only. Stop when the session checklist is complete.
> Do not proceed to the next session.

---

### Session 1 — Database Migrations

**Scope:** Create all new tables. Nothing else.

**Files to create:**
- `postgres/migrations/XX_notifications.sql`
- `postgres/migrations/XX_notification_gap_rules.sql`
- `postgres/migrations/XX_assets.sql`

**Files to modify:**
- `postgres/migrations/XX_events.sql` (or wherever `personal.event` is defined) — add `asset_id` and `generated_by_rule` columns

**Steps:**
1. Read existing migration files to confirm naming convention and sequence numbering
2. Read `personal.event` table definition — confirm column names before adding to it
3. Write migrations in order: notifications → gap_rules → assets → event additions
4. Run migrations against local DB
5. Confirm all tables exist with correct columns and indexes
6. Confirm grants match existing DB role convention

**Stop when:** All tables exist. No application code written.

**Verify with:**
```sql
\dt personal.*
\d personal.notifications
\d personal.asset
SELECT column_name FROM information_schema.columns
  WHERE table_schema = 'personal' AND table_name = 'event'
  ORDER BY ordinal_position;
```

---

### Session 2 — Graph Property Additions and Backfill

**Scope:** Update ingestor to write new event node properties. Backfill existing nodes.

**Files to read first:**
- `ingestor/src/graph.py` — confirm `create_node()`, `set_node_facts()`, `build_props()` signatures
- Existing event node creation — confirm which properties are currently written

**Files to modify:**
- `ingestor/src/graph.py` — add `COLLISION_AWARE_LABELS`, `ATTENDANCE_MODES`, new properties to event node write

**Files to create:**
- `postgres/migrations/backfill_collision_aware.cypher` — one-off Cypher script to set `collision_aware` on existing nodes by label

**Steps:**
1. Read `graph.py` in full before touching it
2. Add `COLLISION_AWARE_LABELS` dict
3. Add `collision_aware`, `attendance_mode`, `travel_buffer_before_min`, `travel_buffer_after_min`, `commitment_start`, `commitment_end` to event node property writes
4. Add `derive_commitment_window()` function
5. Write backfill Cypher script — set `collision_aware` per label using `COLLISION_AWARE_LABELS`
6. Run backfill against local graph
7. Verify a sample of existing event nodes have correct properties

**Stop when:** New properties are written on new nodes. Backfill has run. No detector code written.

**Verify with:**
```cypher
MATCH (e) WHERE e.collision_aware IS NOT NULL
RETURN label(e), e.collision_aware, count(*) GROUP BY label(e), e.collision_aware
```

---

### Session 3 — Asset Classifier and Upsert

**Scope:** Asset recognition layer in the ingestor. No pipeline wiring yet.

**Files to read first:**
- `ingestor/src/graph.py` — `create_node()`, `create_edge()` signatures
- Existing classification step — confirm output schema (`entity_type`, `extracted_fields`, etc.)
- `ingestor/src/` directory listing — understand existing module structure before adding files

**Files to create:**
- `ingestor/src/asset_classifier.py`
- `ingestor/src/asset_writer.py`
- `ingestor/src/asset_matcher.py`

**Steps:**
1. Read existing ingestor modules to understand import patterns and shared utilities
2. Implement `asset_classifier.py` — `classify_for_asset()` and helpers
3. Implement `asset_writer.py` — `upsert_asset()`, `find_existing_asset()`, `build_asset_facts()`, `default_rules_for_type()`
4. Implement `asset_matcher.py` — `match_asset_for_event()`, `infer_asset_type_from_event()`
5. Check `pg_trgm` availability — if not enabled, switch `find_existing_asset()` to vector similarity
6. Write a standalone test script that creates one asset of each type manually and verifies the DB row and graph node

**Stop when:** Asset upsert works end-to-end in isolation. Pipeline not yet wired.

**Verify with:**
```python
# Run test script directly
python ingestor/src/test_asset_upsert.py
# Confirm rows in personal.asset
# Confirm :Asset nodes in AGE with correct properties
```

---

### Session 4 — Rule Watcher

**Scope:** Rule evaluation and event generation from asset rules.

**Files to read first:**
- `ingestor/src/asset_writer.py` (Session 3 output)
- `ingestor/src/graph.py` — event node creation, `spawn_travel_nodes()` does not exist yet — implement it here
- `postgres/migrations/XX_assets.sql` — confirm asset table structure

**Files to create:**
- `ingestor/src/rule_watcher.py`

**Files to modify:**
- `ingestor/src/asset_writer.py` — add `trigger_rules_for_asset()`
- `ingestor/src/graph.py` — add `spawn_travel_nodes()`, `on_event_date_change()`

**Steps:**
1. Implement `spawn_travel_nodes()` in `graph.py` using existing `create_node()` and `create_edge()`
2. Implement `on_event_date_change()` in `graph.py`
3. Implement `rule_watcher.py` — `run_rule_watcher()`, `process_asset_rules()`, `resolve_trigger_date()`, `event_already_exists()`, `create_event_from_rule()`, `update_asset_next_event_date()`
4. Add `trigger_rules_for_asset()` to `asset_writer.py`
5. Seed the test assets from Session 3 with rules if not already present
6. Run rule watcher manually against seeded assets
7. Verify events created in `personal.event` and graph
8. Verify travel child nodes created for IN_PERSON events with buffers

**Stop when:** Rule watcher runs end-to-end on seeded assets and generates correct events.

**Verify with:**
```sql
SELECT e.name, e.event_type, e.asset_id, e.generated_by_rule
FROM personal.event e
WHERE e.asset_id IS NOT NULL
ORDER BY e.created_at DESC LIMIT 20;
```
```cypher
MATCH (a:Asset)-[:HAS_EVENT]->(e)
RETURN a.name, e.name, e.event_type LIMIT 20
```

---

### Session 5 — Notification Detectors

**Scope:** All five detectors. Build and test each independently before wiring into runner.

**Files to read first:**
- `ingestor/src/graph.py` — confirm `set_node_facts()` signature
- `personal.notifications` table (Session 1 output)
- `personal.notification_gap_rules` table and seed data (Session 1 output)

**Files to create:**
- `ingestor/src/notifications/__init__.py`
- `ingestor/src/notifications/base_detector.py`
- `ingestor/src/notifications/collision_detector.py`
- `ingestor/src/notifications/system_health_detector.py`
- `ingestor/src/notifications/pattern_gap_detector.py`
- `ingestor/src/notifications/staleness_detector.py`
- `ingestor/src/notifications/action_required_detector.py`
- `ingestor/src/notifications/runner.py`

**Build order within session — do not skip ahead:**

**5a. Base detector**
- Implement `base_detector.py` — `upsert_notification()`, `auto_resolve()`, `write_graph_node_facts()`
- Test: manually insert a notification row, verify dedup logic rejects a duplicate

**5b. Collision detector**
- Temporal detection first — overlapping `commitment_start`/`commitment_end` windows
- Test: create two overlapping IN_PERSON events, run detector, verify notification row
- Resource detection second — same person, within buffer
- Test: create two close events with same `requires_who`, verify notification

**5c. System health detector**
- Implement all service checks
- Test: set a fake `last_poll` timestamp to 3 hours ago, verify HIGH notification fires
- Test: restore timestamp, verify auto-resolve fires

**5d. Pattern gap detector**
- Graph-based rules first (using `notification_gap_rules` seed data)
- Asset rule source second (reads from `personal.asset` rules where `auto_create: false`)
- Dependency date change source third
- Test each source independently

**5e. Staleness detector**
- Implement per `STALENESS_RULES`
- Test: create an Appointment with `event_date` yesterday and no `outcome`, verify LOW notification

**5f. Action required detector**
- Implement all three sources
- Test: leave a PATTERN_GAP in PENDING status for 48+ hours (mock timestamp), verify escalation

**5g. Runner**
- Wire all detectors into `runner.py`
- Run all detectors in sequence
- Verify results dict returned correctly, errors in one detector do not crash others

**Stop when:** All five detectors run independently and via runner. Real notifications visible in `personal.notifications`.

**Verify with:**
```sql
SELECT type, severity, status, title, created_at
FROM personal.notifications
ORDER BY created_at DESC LIMIT 20;
```

---

### Session 6 — Resolution Endpoint and Graph Writeback

**Scope:** API endpoints for listing and resolving notifications. Type-specific writebacks.

**Files to read first:**
- `graph-api/src/routers/` — confirm existing router structure and patterns
- `graph-api/src/` — confirm `get_db()` dependency, auth patterns, response models
- `ingestor/src/graph.py` — `set_node_facts()` for graph writeback

**Files to create:**
- `graph-api/src/routers/notifications.py`
- `graph-api/src/routers/assets.py`

**Files to modify:**
- `graph-api/src/main.py` (or router registration file) — register new routers

**Steps:**
1. Read existing router files to match patterns exactly before writing new ones
2. Implement `notifications.py`:
   - `GET /notifications` with status/type/severity filters
   - `GET /notifications/counts` — badge counts by type and severity
   - `PATCH /notifications/{id}/resolve` with type-specific writeback dispatch
3. Implement `assets.py`:
   - `GET /assets` — list with type grouping
   - `GET /assets/{id}` — detail with rules and linked events
   - `POST /assets` — manual create (dashboard form path)
   - `PATCH /assets/{id}` — update facts/rules
   - `POST /assets/{id}/run-rules` — on-demand rule trigger
4. Test each endpoint with curl or existing test harness
5. Verify COLLISION resolve writes `collision_status` back to graph nodes
6. Verify STALENESS option B writes `staleness_suppressed_until` to graph node

**Stop when:** All endpoints return correct data. Resolve writes back to graph correctly.

**Verify with:**
```bash
curl http://localhost:8000/notifications?status=DETECTED
curl -X PATCH http://localhost:8000/notifications/{id}/resolve \
  -d '{"resolution_key": "IGNORE", "resolved_by": "dashboard"}'
# Verify status updated in DB and graph node property set
```

---

### Session 7 — Dashboard

**Scope:** Notifications page, Assets page, sidebar badge.

**Files to read first:**
- `dashboard/src/app/` — confirm existing page structure and routing conventions
- `dashboard/src/components/` — confirm existing component patterns, styling approach
- `dashboard/src/` — confirm API client patterns, how existing pages fetch data

**Files to create:**
- `dashboard/src/app/notifications/page.tsx`
- `dashboard/src/components/NotificationFeed.tsx`
- `dashboard/src/components/NotificationCard.tsx`
- `dashboard/src/app/assets/page.tsx`
- `dashboard/src/components/AssetList.tsx`
- `dashboard/src/components/AssetCard.tsx`

**Files to modify:**
- Sidebar nav component — add Notifications link with live badge count
- API client — add notification and asset fetch functions

**Steps:**
1. Read existing page and component files before writing any new ones — match patterns exactly
2. Implement notification count fetch — poll `/notifications/counts` every 60 seconds
3. Implement sidebar badge — red when any HIGH open
4. Implement `NotificationCard` — type chip, severity badge, summary, node refs, options buttons
5. Implement `NotificationFeed` — filter bar, card list, resolved accordion
6. Implement `/notifications` page
7. Implement `AssetCard` — name, type, next_event_date, status, expand for rules and events
8. Implement `AssetList` — grouped by asset_type
9. Implement `/assets` page

**Stop when:** Both pages render correctly with real data. Sidebar badge updates.

**Do not:** Implement new API calls not defined in Session 6. Do not add features beyond what is in the build doc.

---

### Session 8 — n8n Workflows and WhatsApp Path

**Scope:** Scheduled n8n workflows. WhatsApp pipeline wiring.

**Files to read first:**
- Existing n8n workflow exports in repo (if present) — confirm workflow structure
- `ingestor/src/` — confirm HTTP endpoint that n8n calls for pipeline trigger
- wa-agent entry point — confirm whether it calls main pipeline or writes directly to graph

**Workflows to create:**

**8a. Notification Runner — 15-minute cron**
- Trigger: Schedule every 15 minutes
- Step 1: HTTP POST → `graph-api/run-notifications`
- Step 2: Filter results for new HIGH severity notifications
- Step 3: For each HIGH — send WhatsApp message to owner number
- Step 4: Log run to audit_log

**8b. System Health — 5-minute cron (separate workflow)**
- Trigger: Schedule every 5 minutes
- Step 1: HTTP POST → `graph-api/run-system-health`
- Step 2: Only send WhatsApp on transition to HIGH (not on every check)
- Uses `expires_at` field to suppress repeat alerts

**8c. Rule Watcher — 6am daily**
- Trigger: Schedule daily 6:00am
- Step 1: HTTP POST → `graph-api/run-rule-watcher`
- Step 2: Log result summary to audit_log
- Step 3: If HIGH PATTERN_GAP notifications fired — send WhatsApp summary (one message, not one per gap)

**8d. WhatsApp pipeline wiring**
- Read wa-agent entry point before modifying anything
- If wa-agent writes directly to graph: add `classify_for_asset()` + `upsert_asset()` call before graph write, OR refactor to call main `process_content()` pipeline
- If wa-agent already calls main pipeline: confirm `source="whatsapp"` is passed through correctly
- Test: send a WhatsApp message describing a new asset ("just got car insurance renewal, RACQ, expires March 2027"), verify asset upsert fires and rule watcher triggers

**Stop when:** All three scheduled workflows run without error. WhatsApp asset recognition works end-to-end.

**Final verify:**
```sql
-- Confirm full loop: WhatsApp message → asset → event → notification
SELECT a.name, a.asset_type, e.name as event_name, n.title as notification
FROM personal.asset a
LEFT JOIN personal.event e ON e.asset_id = a.id
LEFT JOIN personal.notifications n ON n.payload->>'asset_id' = a.id::text
ORDER BY a.created_at DESC LIMIT 10;
```

---

### Session Handoff Checklist

Before ending each session, confirm:
- [ ] All files written are syntactically valid (no unterminated blocks, no missing imports)
- [ ] No placeholder functions left unimplemented (`pass`, `...`, `TODO` without a note)
- [ ] Verify query run and result confirmed in session notes
- [ ] No code written that belongs to a later session
- [ ] Audit log entries appear for actions taken in this session

### If Context Gets Long Mid-Session

If the session is running long and context feels compressed:
1. Stop writing new code
2. Summarise what has been completed and what remains in the session
3. Save that summary as a comment at the top of the last file written
4. Start a new Claude Code session, paste the summary, continue from there
5. Do not try to finish the session in a degraded context window
