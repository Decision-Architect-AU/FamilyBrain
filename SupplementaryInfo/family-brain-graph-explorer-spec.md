# Family Brain — AGE Graph Explorer
## Product Specification v1.0

---

## Overview

A self-hosted browser-based graph visualisation and editing tool for the Family Brain system. It connects to the existing FastAPI MCP server and provides a Neo4j Browser–style interface for exploring, editing, and annotating the Apache AGE knowledge graph. All data reads and writes go through the existing FastAPI/AGE layer — the explorer is a pure frontend with no direct database access.

---

## Goals

- Provide a usable, dark-themed graph canvas that replaces AGE Viewer for day-to-day inspection and debugging of the Family Brain knowledge graph
- Enable full node and edge CRUD without writing Cypher manually
- Support metadata inspection and editing on any node or relationship
- Make graph structure navigable through layout algorithms, label filters, and display name options
- Ship as a single self-contained HTML file (or small React app) that can be opened directly from the filesystem or served by the existing FastAPI instance on a local port

---

## Non-Goals

- No authentication layer — runs on LAN only, trusts the FastAPI server's access controls
- Not a reporting or analytics tool — use Grafana for time-series views
- No multi-user collaboration features
- No graph export to Neo4j or other external systems
- No mobile/touch optimisation in v1

---

## Architecture

```
Browser (Graph Explorer)
        │
        │  REST/JSON
        ▼
FastAPI MCP Server  ←──→  PostgreSQL + Apache AGE
```

The explorer calls FastAPI endpoints. FastAPI translates to AGE Cypher queries. All agtype serialisation/deserialisation stays in FastAPI — the explorer receives plain JSON (nodes as `{id, labels, properties}`, edges as `{id, type, startNode, endNode, properties}`).

---

## FastAPI Endpoints Required

The explorer depends on the following endpoints. These should be implemented in FastAPI before or alongside the frontend.

### Graph Query

```
POST /graph/query
Body: { "cypher": "MATCH (n)-[r]-(m) RETURN n, r, m LIMIT 100" }
Response: { "nodes": [...], "edges": [...] }
```

Node shape:
```json
{
  "id": "3.1",
  "labels": ["Person"],
  "properties": {
    "name": "Glenn",
    "dob": "1980-01-01"
  }
}
```

Edge shape:
```json
{
  "id": "5.2",
  "type": "KNOWS",
  "startNode": "3.1",
  "endNode": "3.4",
  "properties": {
    "since": "2010"
  }
}
```

### Node CRUD

```
POST   /graph/nodes              — create node
GET    /graph/nodes/{id}         — fetch single node with all properties
PATCH  /graph/nodes/{id}         — update properties (merge, not replace)
DELETE /graph/nodes/{id}         — delete node (reject if has edges unless force=true)
```

### Edge CRUD

```
POST   /graph/edges              — create edge
GET    /graph/edges/{id}         — fetch single edge with all properties
PATCH  /graph/edges/{id}         — update properties
DELETE /graph/edges/{id}         — delete edge
```

### Schema Introspection

```
GET /graph/labels                — list all node labels in graph
GET /graph/relationship-types    — list all edge types in graph
GET /graph/schema/{label}        — known property keys for a label (sampled from existing nodes)
```

---

## UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  TOOLBAR: [Query Bar_______________________] [Run] [Clear]      │
│           [Layout ▾] [Display ▾] [Filter ▾] [+ Node] [+ Edge]  │
├─────────────────────────────────────────┬───────────────────────┤
│                                         │                       │
│           GRAPH CANVAS                  │   DETAIL PANEL        │
│        (Cytoscape.js / vis.js)          │   (node/edge info)    │
│                                         │                       │
│                                         │                       │
│                                         │                       │
├─────────────────────────────────────────┴───────────────────────┤
│  STATUS BAR: node count | edge count | last query time          │
└─────────────────────────────────────────────────────────────────┘
```

**Colour scheme:** Dark background (#1a1a2e or similar), coloured nodes by label, white/light-grey text. Mirrors Neo4j Browser aesthetic.

---

## Features

### 1. Graph Canvas

**Library:** Cytoscape.js (preferred — better layout algorithm support than vis.js)

- Pan and zoom (mouse wheel, trackpad)
- Drag nodes to reposition
- Click node → highlight + open Detail Panel
- Click edge → highlight + open Detail Panel
- Multi-select via shift-click or drag-select box
- Double-click canvas → open Add Node dialog
- Right-click node → context menu (Edit, Add Edge, Delete, Expand Neighbours)
- Right-click edge → context menu (Edit, Delete)
- Node size scales with degree (connection count) — toggle on/off
- Nodes coloured by label; legend in bottom-left corner

---

### 2. Query Bar

- Accepts openCypher (passed directly to `POST /graph/query`)
- Dropdown history of last 20 queries (persisted in localStorage)
- Preset query buttons:
  - "Show All" — `MATCH (n)-[r]-(m) RETURN n, r, m LIMIT 100`
  - "Show Persons" — `MATCH (n:Person) RETURN n`
  - "Show NDIS" — `MATCH (n:NDISPlan)-[r]-(m) RETURN n, r, m`
  - "Show Properties" — `MATCH (n:Property)-[r]-(m) RETURN n, r, m`
  - "Show Vehicles" — `MATCH (n:Vehicle)-[r]-(m) RETURN n, r, m`
- Keyboard shortcut: Ctrl+Enter to run
- Error display inline below the bar (red, non-modal)

---

### 3. Layout Algorithms

Dropdown selector. Applies Cytoscape.js layout to currently displayed graph.

| Layout | Best For |
|--------|----------|
| **Force-directed (cose)** | Default. General exploration. |
| **Hierarchical (dagre)** | Trees and parent-child relationships. |
| **Concentric** | Showing nodes grouped by degree or label. |
| **Grid** | Quick overview of many disconnected nodes. |
| **Breadth-first** | Expanding from a single root node. |
| **Circle** | Small graphs, easy to see all connections. |

"Re-run Layout" button to re-apply current algorithm after manual node moves.

---

### 4. Display Options

Dropdown with toggles:

**Node label display** (radio — choose one):
- Display Name (`name` property — fallback to `title`, then `id`)
- Node ID (AGE internal id)
- Label only (e.g. "Person")
- Label + Name (e.g. "Person: Glenn")

**Edge label display** (radio — choose one):
- Relationship type (e.g. "OWNS")
- Hide edge labels
- Edge ID

**Other toggles:**
- Show/hide orphan nodes (nodes with no edges in current result)
- Show/hide edge direction arrows
- Scale node size by degree
- Show property count badge on nodes

---

### 5. Filter Panel

Sidebar or dropdown panel. Filters apply to the currently loaded graph result — do not re-query.

**By Label (checkboxes):**
- Dynamically populated from labels present in current graph
- Uncheck a label to hide all nodes of that type (and their edges)

**By Relationship Type (checkboxes):**
- Dynamically populated from edge types in current graph
- Uncheck to hide edges of that type

**By Property Value:**
- Property key (text input, autocompleted from schema)
- Operator: equals / contains / starts with / greater than / less than
- Value (text input)
- "Add Filter" button — multiple filters stack with AND logic
- Active filter pills shown above canvas with × to remove

**Search:**
- Text search box — highlights nodes whose properties contain the search string
- Matched nodes pulse or glow; non-matched nodes dim

---

### 6. Detail Panel (right sidebar)

Opens on node or edge click. Closes on canvas click or × button.

**Node view:**

```
┌─────────────────────────────┐
│  ● Person                   │  ← label badge (coloured)
│  Glenn                      │  ← display name
│  ID: 3.1                    │
├─────────────────────────────┤
│  PROPERTIES                 │
│  name        Glenn      [✎] │
│  dob         1980-01-01 [✎] │
│  email       g@x.com    [✎] │
│                    [+ Add]  │
├─────────────────────────────┤
│  RELATIONSHIPS (4)          │
│  → OWNS  Property:42        │
│  → PARENT_OF  Person:7      │
│  ← TREATED_BY  HealthPr:12  │
│                [Expand All] │
├─────────────────────────────┤
│  [Edit Label]  [Delete Node]│
└─────────────────────────────┘
```

**Edge view:**

```
┌─────────────────────────────┐
│  ──── OWNS ────             │
│  ID: 5.2                    │
│  Glenn → Acacia St Property │
├─────────────────────────────┤
│  PROPERTIES                 │
│  since       2019       [✎] │
│  share       100%       [✎] │
│                    [+ Add]  │
├─────────────────────────────┤
│  [Delete Edge]              │
└─────────────────────────────┘
```

Inline property editing: click ✎ → field becomes input → Enter to save → PATCH to API → panel refreshes.

---

### 7. Add Node Dialog

Triggered by toolbar "+ Node" button or double-click on canvas.

```
Label(s):    [Person          ] [+ Add Label]
Properties:
  Key        Value
  [name    ] [Glenn          ]
  [dob     ] [1980-01-01     ]
  [+ Add Property]

             [Cancel] [Create Node]
```

- Label dropdown autocompletes from `GET /graph/labels`
- Property keys autocomplete from `GET /graph/schema/{label}` once a label is selected
- On create: `POST /graph/nodes` → node added to canvas at centre
- New node auto-selected, Detail Panel opens

---

### 8. Add Edge Dialog

Triggered by toolbar "+ Edge" button or right-click → "Add Edge From Here".

```
From Node:   [3.1 — Glenn (Person)      ▾]
Type:        [OWNS                       ]  ← free text or dropdown
To Node:     [42 — Acacia St (Property) ▾]

Properties:
  Key        Value
  [since   ] [2019           ]
  [+ Add Property]

             [Cancel] [Create Edge]
```

- From Node: pre-filled if triggered from right-click; otherwise searchable dropdown of all nodes in current canvas
- Type: free text (creates new type) or dropdown of existing types from `GET /graph/relationship-types`
- On create: `POST /graph/edges` → edge added to canvas

---

### 9. Edit Node Metadata (Full Edit Mode)

Available from Detail Panel → "Edit Label" or via right-click → "Edit Node".

Opens a modal for more complex edits:

- Add/remove labels (AGE supports single label per node in v1.5+; expose as single select with note)
- Bulk property editor — table view of all key/value pairs, all editable simultaneously
- Property type hints: string / number / boolean / date — displayed as badges, not enforced
- "Save All" sends single `PATCH /graph/nodes/{id}` with full property map
- "Delete Node" with confirmation dialog; warns if node has edges ("This node has 4 relationships. Delete anyway?")

---

### 10. DB Tools Panel

Accessible from a "Tools" tab in the sidebar or a dedicated drawer.

#### Schema Inspector
- Table of all labels → node count
- Table of all relationship types → edge count
- Expandable rows showing sampled property keys for each label

#### Node Merge Tool
- Select two nodes from dropdowns
- Preview what properties each has
- Choose which properties to keep (side-by-side comparison)
- Creates merged node, re-points all edges, deletes originals
- Generates Cypher preview before executing

#### Batch Property Setter
- Select a label
- Choose a property key
- Set value for all nodes of that label that are missing that property
- Dry-run preview (shows count) before confirm

#### Cypher Console
- Full raw Cypher input (multi-line textarea)
- Send to `POST /graph/query`
- Results shown as JSON tree (not visualised — for raw inspection)
- Useful for complex writes not exposed in UI

---

## Technical Stack

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Graph rendering | Cytoscape.js | Best layout algorithm range; well-maintained |
| Layout extensions | cytoscape-dagre, cytoscape-cose-bilkent | Hierarchical + improved force-directed |
| UI framework | React (Vite) | Component model suits panel/modal architecture |
| Styling | Tailwind CSS | Fast dark-mode theming |
| State | Zustand | Lightweight; avoids Redux complexity for this scope |
| HTTP client | fetch / axios | Calls existing FastAPI endpoints |
| Build output | Single dist/ folder | Served by FastAPI as static files on `/explorer` |

---

## Natural Language Graph Ingestor

A dedicated panel in the explorer (and a corresponding FastAPI pipeline) that accepts free-form text and uses the local LLM (qwen2.5:32b via Ollama) to extract entities and relationships, then presents them for review before writing to AGE.

---

### Purpose

Allows a user to paste or type any natural language text — a paragraph about a provider, a conversation transcript, a WhatsApp message, a document excerpt — and have the system propose the nodes and edges that should be created or updated in the graph. The user reviews the proposals, edits them if needed, and approves the write. Nothing is written without explicit confirmation.

---

### Pipeline Overview

```
User pastes text
      │
      ▼
POST /ingest/extract   (FastAPI)
      │
      ├── Sends text + schema context to qwen2.5:32b via Ollama
      │
      ▼
LLM returns structured JSON proposal
      │
      ▼
FastAPI validates proposal against schema
      │
      ▼
Browser renders Review Panel
      │
  User edits / approves / rejects individual items
      │
      ▼
POST /ingest/commit    (FastAPI)
      │
      ▼
FastAPI writes to AGE via Cypher
      │
      ▼
Newly created nodes/edges highlighted on canvas
```

---

### LLM Prompt Design

The FastAPI `/ingest/extract` endpoint constructs a prompt with three parts:

**1. System context — graph schema**

Injected automatically. Describes the 8 domain labels, their key properties, and common relationship types. Kept under ~800 tokens. Example excerpt:

```
You are a knowledge graph extraction assistant for a family administration system.

Known node labels and their key properties:
- Person: name, dob, email, phone
- HealthPractitioner: name, specialty, provider_number, clinic
- NDISProvider: name, abn, service_type
- Property: address, suburb, state
- Vehicle: make, model, year, rego, state
- InsurancePolicy: policy_number, insurer, type, premium, renewal_date
- RecurringPayment: name, amount, frequency, next_due, entity
- Medication: name, dose, frequency, prescriber
... (all 8 domains)

Known relationship types:
- (Person)-[:TREATED_BY]->(HealthPractitioner)
- (Person)-[:PRESCRIBED]->(Medication)
- (NDISPlan)-[:FUNDED_BY]->(NDISProvider)
- (Person)-[:OWNS]->(Property)
- (Person)-[:DRIVES]->(Vehicle)
- (Vehicle)-[:INSURED_BY]->(InsurancePolicy)
- (Person)-[:PAYS]->(RecurringPayment)
... (full list)

Rules:
- Only use labels and relationship types from the lists above
- If a label doesn't fit, use the closest match and flag it
- Extract only facts explicitly stated or clearly implied — do not infer
- If an entity likely already exists in the graph, set "match_on" to the properties to use for MERGE instead of CREATE
- Return ONLY valid JSON, no explanation, no markdown
```

**2. User text**

Passed as-is from the ingestor input box.

**3. Output format instruction**

```
Return a JSON object with this exact shape:

{
  "nodes": [
    {
      "id": "temp_1",
      "label": "HealthPractitioner",
      "properties": {
        "name": "Dr Sarah Chen",
        "specialty": "Paediatrician",
        "provider_number": "1234567A"
      },
      "match_on": ["name"],
      "confidence": "high",
      "note": ""
    }
  ],
  "edges": [
    {
      "id": "temp_e1",
      "type": "TREATED_BY",
      "from": "temp_2",
      "to": "temp_1",
      "properties": {
        "since": "2023"
      },
      "confidence": "medium",
      "note": "Implied by 'sees Dr Chen regularly'"
    }
  ]
}

Confidence levels: "high" (explicitly stated), "medium" (clearly implied), "low" (inferred or uncertain).
temp IDs are arbitrary strings used to link nodes to edges within this response only.
```

---

### Review Panel UI

Appears below or beside the ingestor text area after extraction completes. Shown before any data is written.

```
┌─────────────────────────────────────────────────────────────────┐
│  NATURAL LANGUAGE INGESTOR                                      │
├─────────────────────────────────────────────────────────────────┤
│  Paste text here...                                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Olivia had an appointment with Dr Sarah Chen at         │   │
│  │ Greenslopes Paediatrics on 14 June. She was prescribed  │   │
│  │ a new medication, Melatonin 2mg, to be taken nightly.   │   │
│  │ The out-of-pocket cost was $95.                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│  Context hint: [NDIS / Health / Finance / Auto-detect ▾]       │
│                                              [Extract →]        │
├─────────────────────────────────────────────────────────────────┤
│  PROPOSED NODES                                                 │
│                                                                 │
│  ✅ Person — Olivia                    [high] [Edit] [✕]       │
│     name: Olivia                                                │
│     → MERGE on: name                                           │
│                                                                 │
│  ✅ HealthPractitioner — Dr Sarah Chen [high] [Edit] [✕]       │
│     name: Dr Sarah Chen                                         │
│     clinic: Greenslopes Paediatrics                             │
│     → MERGE on: name                                           │
│                                                                 │
│  ✅ Appointment                        [high] [Edit] [✕]       │
│     date: 2024-06-14                                            │
│     cost: 95                                                    │
│     → CREATE                                                    │
│                                                                 │
│  ✅ Medication — Melatonin 2mg         [high] [Edit] [✕]       │
│     name: Melatonin                                             │
│     dose: 2mg                                                   │
│     frequency: nightly                                          │
│     → MERGE on: name, dose                                     │
│                                                                 │
│  PROPOSED EDGES                                                 │
│                                                                 │
│  ✅ Olivia -[TREATED_BY]→ Dr Sarah Chen      [high] [Edit] [✕] │
│  ✅ Olivia -[HAD_APPOINTMENT]→ Appointment   [high] [Edit] [✕] │
│  ✅ Appointment -[WITH]→ Dr Sarah Chen       [high] [Edit] [✕] │
│  ✅ Olivia -[PRESCRIBED]→ Melatonin 2mg      [high] [Edit] [✕] │
│                                                                 │
│  ⚠️  Low-confidence items hidden — [Show 0]                    │
│                                                                 │
│            [Reject All]  [Select All]  [Commit Selected →]     │
└─────────────────────────────────────────────────────────────────┘
```

**Review Panel behaviour:**
- Each proposed node and edge is independently toggled (checkbox)
- All items default to checked
- Low-confidence items (`"confidence": "low"`) default to unchecked and are collapsed behind "Show N"
- Clicking Edit on a node opens an inline mini-editor for its properties and label
- Clicking Edit on an edge allows changing the relationship type and properties
- `match_on` fields shown as a small badge ("MERGE on: name") — user can toggle to CREATE if they want a new node instead
- "Commit Selected" sends only the checked items to `/ingest/commit`
- On success, the canvas reloads and highlights the newly written nodes/edges in a flash colour

---

### Ingest Context Hint

A dropdown that prepends domain-specific context to the LLM prompt, sharpening extraction for a known domain:

| Hint | Extra context injected |
|------|----------------------|
| Auto-detect | No extra context; LLM decides |
| Health | Prioritise HealthPractitioner, Appointment, Medication, Dispense labels |
| NDIS | Prioritise NDISPlan, NDISProvider, NDISServiceDelivery, NDISReceipt |
| Finance | Prioritise Bill, BAS, RecurringPayment, LoanFacility, Distribution |
| Property | Prioritise Property, Trust, OwnershipStatement |
| Insurance | Prioritise InsurancePolicy, InsuranceClaim |
| Travel | Prioritise Trip, Flight, Accommodation, CarRental |
| Vehicle | Prioritise Vehicle, InsurancePolicy |
| Family | Prioritise Person, School, Activity, Term |

---

### FastAPI Endpoints Required

```
POST /ingest/extract
Body:  { "text": "...", "context_hint": "health" }
Response: {
  "nodes": [...],
  "edges": [...],
  "raw_llm_response": "...",   ← for debugging
  "model": "qwen2.5:32b",
  "duration_ms": 1840
}

POST /ingest/commit
Body: {
  "nodes": [...],   ← subset user approved, with any edits applied
  "edges": [...]
}
Response: {
  "created_nodes": [{ "temp_id": "temp_1", "age_id": "3.47" }],
  "merged_nodes":  [{ "temp_id": "temp_2", "age_id": "3.1"  }],
  "created_edges": [{ "temp_id": "temp_e1", "age_id": "5.23" }],
  "errors": []
}
```

**Commit logic in FastAPI:**
- For each node: if `match_on` is set → `MERGE (n:Label {key: value}) ON CREATE SET ... ON MATCH SET ...`; otherwise → `CREATE`
- For each edge: resolve `from`/`to` temp IDs to AGE IDs using the commit response map, then `MERGE` or `CREATE` the edge
- All writes in a single transaction — if any fail, roll back all and return errors
- Return the `age_id` mapping so the frontend can highlight the right nodes on canvas

---

### Ingest History

A scrollable log below the ingestor panel showing past ingestion runs:

```
14 Jun 2026 09:42  "Olivia had an appointment with Dr Sarah..."
                   4 nodes · 4 edges · committed
                   [View on Canvas]  [Re-extract]

13 Jun 2026 18:11  "Paid $1,020 to Greenslopes..."
                   2 nodes · 1 edge · 1 rejected
                   [View on Canvas]  [Re-extract]
```

- Stored in localStorage (no backend persistence needed in v1)
- "View on Canvas" runs a query to show just those nodes
- "Re-extract" re-runs the extraction on the same original text

---

### File Structure Additions

```
graph-explorer/
└── src/
    └── components/
        ├── IngestorPanel.jsx       ← text input, context hint, Extract button
        ├── IngestReviewPanel.jsx   ← proposed nodes/edges review list
        ├── IngestHistory.jsx       ← past runs log
        └── IngestNodeEditor.jsx    ← inline property editor within review
```

---

## Response Quality Lab

A fourth tab in the explorer for improving response quality over time — completely out of band from the live messaging flow. The WhatsApp pipeline is unchanged: queries are answered immediately with no human in the loop. This tool exists to review what was sent after the fact, flag responses that weren't good enough, write better versions, and build up a prompt improvement and example dataset that feeds back into the system.

The core loop is: **observe → flag → improve → apply**.

---

### Live Pipeline (Unchanged)

```
WhatsApp query
      │
      ▼
n8n: intent classify (phi3.5-mini / NPU)
      │
      ▼
MCP: get_{domain}_context()
      │
      ▼
qwen2.5:32b → response sent immediately to WhatsApp
      │
      ▼ (logged to interaction_log — no other change)
```

The only addition to the existing pipeline is logging every interaction to an `interaction_log` table. That's a single non-blocking write after send. Nothing else changes.

---

### What Gets Logged (Automatically)

Every query–response cycle writes one record:

```sql
CREATE TABLE interaction_log (
  id                TEXT PRIMARY KEY,
  sender_id         TEXT NOT NULL,         -- AGE Person node id
  sender_number     TEXT NOT NULL,
  query_text        TEXT NOT NULL,
  intent            TEXT NOT NULL,         -- classified domain
  context_nodes     JSONB,                 -- AGE node ids used for context
  context_snapshot  JSONB,                 -- actual property values sent to model
  prompt_version    TEXT NOT NULL,         -- prompt template id/hash
  response_text     TEXT NOT NULL,         -- what was sent to WhatsApp
  model             TEXT NOT NULL,
  latency_ms        INTEGER,
  logged_at         TIMESTAMPTZ DEFAULT NOW(),
  -- quality fields (null until reviewed in lab)
  quality_flag      TEXT,    -- 'good' | 'wrong_data' | 'bad_format' | 'missing_context' | 'hallucinated' | 'too_long' | 'other'
  flag_note         TEXT,
  ideal_response    TEXT,    -- human-written better version
  reviewed_at       TIMESTAMPTZ,
  added_to_examples BOOLEAN DEFAULT FALSE
);
```

---

### UI Layout — Response Quality Lab Tab

```
┌─────────────────────────────────────────────────────────────────┐
│  RESPONSE QUALITY LAB                                           │
│  [Domain: All ▾] [Flag: All ▾] [Date range ▾] [Search ______] │
├─────────────────────────────────────────────────────────────────┤
│  INTERACTION LOG                    │  DETAIL PANEL             │
│                                     │                           │
│  14 Jun 09:42  NDIS  Olivia         │  QUERY                   │
│  "How much NDIS budget left?"       │  "How much is left in    │
│  ✅ good                            │   the NDIS core budget?" │
│                                     │  Olivia · 14 Jun 09:42   │
│  14 Jun 09:31  Property  Glenn      │  NDIS domain             │
│  "Did Maple St rent come in?"       │                           │
│  ⚠️ wrong_data                      │  RESPONSE SENT           │
│                                     │  ┌───────────────────┐   │
│  14 Jun 09:15  Health  Glenn        │  │ 💰 NDIS Core      │   │
│  "Any scripts due soon?"            │  │ Allocated: $24,500│   │
│  — unflagged                        │  │ Spent: $18,240    │   │
│                                     │  │ Remaining: $6,260 │   │
│  13 Jun 18:11  Finance  Glenn       │  │ ⚠️ Burn rate over │   │
│  "BAS status?"                      │  └───────────────────┘   │
│  — unflagged                        │  Latency: 2.3s           │
│                                     │                           │
│  [Load more]                        │  GRAPH CONTEXT USED      │
│                                     │  NDISPlan · 3 categories │
│                                     │  12 service deliveries   │
│                                     │  [View on Canvas]        │
│                                     │                           │
│                                     ├───────────────────────────┤
│                                     │  QUALITY FLAG            │
│                                     │  ○ good  ○ wrong_data    │
│                                     │  ○ bad_format  ○ too_long│
│                                     │  ○ missing_context       │
│                                     │  ○ hallucinated  ○ other │
│                                     │  Note: [____________]    │
│                                     │                           │
│                                     │  IDEAL RESPONSE          │
│                                     │  ┌───────────────────┐   │
│                                     │  │ (write better ver)│   │
│                                     │  └───────────────────┘   │
│                                     │  [Copy from sent]        │
│                                     │  [Add to Examples] [Save]│
└─────────────────────────────────────────────────────────────────┘
```

---

### Interaction Log Panel (left)

- Chronological list of all logged interactions, newest first
- Each row: timestamp, domain badge, sender name, query excerpt, flag status
- Flag status: ✅ good / ⚠️ flagged with type / — unflagged
- Filters: domain, flag type, date range, free text search across query and response text
- Flagged rows subtly highlighted; pagination / infinite scroll

---

### Detail Panel (right)

**Header:** query text, sender, timestamp, domain badge

**Response Sent:** the exact text delivered to WhatsApp, displayed in a message bubble. Read-only.

**Latency:** time from query received to response sent.

**Graph Context Used:** list of node labels and counts that were included in the prompt. "View on Canvas" switches to the Graph tab and highlights those exact nodes — critical for diagnosing whether a bad response was caused by wrong/missing graph data vs a prompt issue.

**Quality Flag:** radio buttons:
- `good` — correct and well-formatted
- `wrong_data` — incorrect facts (likely bad graph data — fix the graph)
- `bad_format` — correct content but poorly structured for WhatsApp
- `missing_context` — incomplete because the graph node doesn't exist yet
- `hallucinated` — model invented something not in the context
- `too_long` — correct but needs to be shorter
- `other` — free text note only

**Ideal Response:** editable textarea. Write what the response should have been. "Copy from sent" pre-fills with the actual response to edit rather than start from scratch.

**[Add to Examples]:** marks this record as a training example (`added_to_examples = true`) — included in the exportable example set for prompt improvement.

**[Save]:** saves flag + note + ideal response. Non-destructive, can be updated any time.

---

### Quality Dashboard

Summary panel at the top of the tab (collapsible):

```
Last 30 days: 312 interactions · 28 flagged (9%)

By domain:        flagged   top flag type
  NDIS               8      wrong_data      ← graph data gap likely
  Finance            6      wrong_data
  Health             4      missing_context ← nodes don't exist yet
  General/School     7      bad_format
  Property           3      good

By flag type:
  wrong_data        11  →  fix graph data
  missing_context    7  →  create missing nodes
  bad_format         5  →  prompt engineering
  hallucinated       3  →  model issue / reduce context noise
  too_long           2  →  prompt engineering
```

This is the primary signal for where to invest effort. `wrong_data` clusters mean graph data needs fixing. `missing_context` means nodes that should exist don't. `hallucinated` or `bad_format` point to prompt work.

---

### Prompt Improvement Workflow

When enough flags accumulate on a domain:

1. Filter log to that domain + flag type
2. Read the context snapshots — see exactly what was sent to the model
3. Write ideal responses for the worst examples → mark as examples
4. Export the example set
5. Update the prompt template for that domain (or add few-shot examples)
6. Use the Replay tool to verify improvement against known-bad cases

---

### Replay Tool

Accessible from any logged interaction via a [Replay] button:

- Takes the stored `context_snapshot` (the exact graph data sent to the model at the time) and re-runs it through the **current** prompt template
- Shows new response alongside the original in a side-by-side diff view
- Does not send anything to WhatsApp — local comparison only
- Lets you verify a prompt change actually fixes a known-bad case without waiting for a real query

---

### Example Set Export

```
GET /quality/examples?domain=ndis&format=jsonl
```

Returns all `added_to_examples = true` interactions as:

```jsonl
{"prompt_version": "ndis_v3", "context": {...}, "response": "...", "ideal": "..."}
```

Use for:
- Few-shot examples in prompt templates (paste best 3–5 into the system prompt for that domain)
- Future fine-tuning data if you ever want to fine-tune qwen2.5 on your specific patterns

---

### FastAPI Endpoints Required

```
POST /quality/log                           ← pipeline calls this after every WhatsApp send
GET  /quality/log?domain=&flag=&limit=      ← list with filters
GET  /quality/log/{id}                      ← single interaction detail
PATCH /quality/log/{id}                     ← save flag, note, ideal_response
POST /quality/log/{id}/replay               ← re-run stored context through current prompt
GET  /quality/examples?domain=&format=      ← export flagged examples
GET  /quality/summary                       ← dashboard counts by domain + flag type
```

---

### Merge Notes for Claude Code

The existing pipeline has a direct generate → send flow with no logging.

**Only change required to the existing pipeline:** after the WhatsApp send succeeds, fire a non-blocking `POST /quality/log` with: query text, intent, context node IDs, context snapshot JSON, prompt version/hash, response text, model name, latency. One write, fire-and-forget.

Everything else is additive — new table, new FastAPI endpoints, new UI tab. The live flow is not touched beyond the log write.

Prompt templates should be stored as versioned records in PostgreSQL (not hardcoded strings) so historical log entries can reference the exact prompt that generated a given response, and so the Replay tool can compare old vs new versions.

---

### File Structure Additions

```
graph-explorer/
└── src/
    └── components/
        ├── QualityLab.jsx             ← tab container + dashboard
        ├── InteractionLog.jsx         ← left panel list with filters
        ├── InteractionDetail.jsx      ← right panel detail view
        ├── QualityFlagPanel.jsx       ← flag radio + note + ideal response editor
        ├── ReplayPanel.jsx            ← side-by-side original vs replayed
        └── QualityDashboard.jsx       ← summary counts + flag type breakdown
```

---

## Emoji Feedback Signal

A passive, zero-friction feedback mechanism. When the sender replies to a Family Brain response with a reaction emoji, n8n intercepts it and translates it into a quality signal on the originating interaction log record. No interface change, no extra step for the user — a 👍 or 👎 from WhatsApp is enough.

---

### How It Works

WhatsApp message reactions arrive via the bridge as a distinct message type. n8n already processes inbound messages; it needs one additional branch to handle reaction events:

```
Inbound WhatsApp event
      │
      ├── type: message  →  normal query pipeline
      │
      └── type: reaction
              │
              ▼
        n8n: look up the message_id being reacted to
              │
              ▼
        GET /quality/log?whatsapp_message_id={id}
              │
              ▼
        map emoji → quality signal
              │
              ▼
        PATCH /quality/log/{id}   { emoji_feedback: "👍" | "👎", quality_flag: ... }
```

The `whatsapp_message_id` is stored in the interaction log at write time (when the response is sent, the bridge returns the message ID).

---

### Emoji → Flag Mapping

| Emoji | Signal | Action |
|-------|--------|--------|
| 👍 ❤️ ✅ | Positive | Set `emoji_feedback = positive`; if no existing flag, set `quality_flag = good` |
| 👎 ❌ 😡 | Negative | Set `emoji_feedback = negative`; set `quality_flag = emoji_flagged`; surface in Quality Lab for review |
| 🤔 ❓ | Uncertain | Set `emoji_feedback = uncertain`; surface in Quality Lab with lower priority |
| Any other emoji | Ignored | No action — people use reactions for many reasons |

A negative reaction does not auto-set a specific flag type (`wrong_data`, `bad_format`, etc.) — that determination is made in the Quality Lab when you review it. The emoji just gets it into the queue.

---

### Quality Lab Integration

Emoji-flagged interactions appear in the log with a distinct indicator:

```
14 Jun 11:03  NDIS  Olivia
"How much NDIS budget left?"
👎 emoji_flagged  ← auto-flagged, awaiting review
```

Filter: `[Flag: emoji_flagged]` shows all emoji-triggered items that haven't been reviewed yet. Once you open one, assign a proper flag type, and save — it moves from `emoji_flagged` to the specific type (`wrong_data`, `bad_format`, etc.) and the emoji indicator stays on the record as provenance.

---

### Schema Addition

One column added to `interaction_log`:

```sql
ALTER TABLE interaction_log ADD COLUMN
  emoji_feedback    TEXT,        -- 'positive' | 'negative' | 'uncertain' | null
  whatsapp_message_id TEXT;      -- bridge message id, for reaction lookup
```

---

### FastAPI Addition

```
POST /quality/reaction
Body: {
  "whatsapp_message_id": "msg_abc123",
  "emoji": "👎",
  "reactor_number": "+61400000000"
}
Response: { "matched": true, "log_id": "...", "flag_set": "emoji_flagged" }
```

Returns `matched: false` if the message ID doesn't correspond to a logged Family Brain response (i.e. the reaction was to something else).

---

## Response Format Templates

Intent-level templates that define the *shape and depth* of a response before the model generates it. Rather than a single prompt per domain, each specific intent subtype has a schema: what sections to include, what to omit, how long to be, what format to use.

This solves the problem where a holiday *summary* and a holiday *day plan* are fundamentally different artifacts — same domain, same data source, but the model needs to know which shape to produce.

---

### Concept

The classifier (phi3.5-mini) currently outputs a domain: `health | ndis | property | finance | travel | insurance | vehicle | general`. The response template layer adds a second dimension: **intent subtype** within each domain, plus a **depth level** (summary vs detail).

```
query: "what's happening on Tuesday in Portugal?"
domain:   travel
subtype:  day_itinerary
depth:    detail          ← specific day requested

query: "remind me what we're doing in Portugal"
domain:   travel
subtype:  trip_summary
depth:    summary         ← whole-trip overview
```

The model receives not just context but a **response schema** telling it what structure to produce.

---

### Template Structure

Each template defines:

```json
{
  "id": "travel.day_itinerary",
  "domain": "travel",
  "subtype": "day_itinerary",
  "depth": "detail",
  "description": "Single day breakdown for a trip — time, activities, logistics",
  "sections": [
    { "key": "date_header",   "required": true,  "format": "📅 {day} {date} — {destination}" },
    { "key": "accommodation", "required": false, "format": "🏨 {name}, check-in {time}" },
    { "key": "activities",    "required": true,  "format": "list, emoji per item, time if known" },
    { "key": "transport",     "required": false, "format": "🚗/✈️ {detail}" },
    { "key": "notes",         "required": false, "format": "plain text, max 1 line" }
  ],
  "max_length": 400,
  "tone": "concise, practical",
  "example": "📅 Tuesday 8 Jul — Lisbon\n🏨 Bairro Alto Hotel, no check-in (mid-stay)\n🗺️ 10am Jerónimos Monastery\n🍽️ 1pm lunch — Time Out Market\n🚋 28 Tram to Alfama\n🎵 8pm Fado show — booked ✅"
}
```

---

### Template Library (Initial Set)

**Travel**

| Template ID | Trigger | Shape |
|-------------|---------|-------|
| `travel.trip_summary` | "what are we doing in X", "remind me about our trip" | Destination, dates, key highlights, accommodation name, total days |
| `travel.day_itinerary` | "what's on Tuesday", "what are we doing on the 8th" | Day header, accommodation, activities with times, transport, notes |
| `travel.logistics` | "how do we get to X", "what time does the flight leave" | Flight/transport details, times, reference numbers, terminal |
| `travel.documents` | "do we have travel insurance", "what's the booking ref" | Document list, policy/ref numbers, expiry dates |

**Health**

| Template ID | Trigger | Shape |
|-------------|---------|-------|
| `health.appointment_summary` | "when's my next appointment", "when does X see the doctor" | Practitioner, specialty, date/time, location, referral status |
| `health.medication_status` | "any scripts due", "when does X need a repeat" | Per-medication: name, dose, repeats remaining, action date, urgency |
| `health.appointment_history` | "when did X last see Dr Y" | Date, practitioner, notes if available |

**NDIS**

| Template ID | Trigger | Shape |
|-------------|---------|-------|
| `ndis.budget_summary` | "how much NDIS budget left" | Category breakdown: allocated / spent / remaining / burn rate / plan end date |
| `ndis.provider_info` | "who provides X for Olivia" | Provider name, service type, contact, last service date |
| `ndis.recent_claims` | "what has been claimed lately" | List of recent NDISReceipts: date, provider, amount, category |

**Finance / Property**

| Template ID | Trigger | Shape |
|-------------|---------|-------|
| `finance.bill_summary` | "what bills are due", "BAS status" | Bill name, amount, due date, status, entity |
| `property.rent_status` | "did rent come in for X" | Property address, gross/net, disbursement date, trust account |
| `property.loan_summary` | "what's the balance on the Maple St loan" | Lender, balance, rate, next payment, facility type |

**School / Family**

| Template ID | Trigger | Shape |
|-------------|---------|-------|
| `school.day_summary` | "what does X need Wednesday" | Child name, day, uniform/equipment needed, activity name, term/week |
| `school.term_overview` | "when does term end", "what week are we in" | Current week, term end date, upcoming events |

---

### Depth Signal Detection

The intent classifier (phi3.5-mini) is extended to output both `domain` and `subtype`. Depth is inferred from the query:

**Summary signals:** "remind me", "what's happening", "overview", "how much", "any", "status", "did X"

**Detail signals:** specific date ("on Tuesday", "on the 8th"), specific person ("for Olivia"), specific entity ("Maple St", "Dr Chen"), "tell me more about", "what exactly"

If depth cannot be determined, default to summary for multi-item domains (travel, finance) and detail for single-entity queries (appointments, medications).

---

### How It Feeds into the Prompt

The FastAPI response generation step adds the template to the model call:

```python
template = get_template(domain=intent.domain, subtype=intent.subtype, depth=intent.depth)

system_prompt = f"""
You are Family Brain, a household assistant responding via WhatsApp.

Response format for this query type ({template.id}):
{template.sections_as_instructions()}

Max length: {template.max_length} characters
Tone: {template.tone}

Example of a good response:
{template.example}

Graph context:
{context_snapshot}
"""
```

The example in the template acts as a one-shot prompt — the model sees exactly what shape a good response looks like for this type, without needing to infer it from the domain alone.

---

### Template Editor (Quality Lab Integration)

A sub-panel in the Quality Lab tab — accessible as a "Templates" section alongside the interaction log:

```
┌─────────────────────────────────────────────────────────────────┐
│  RESPONSE TEMPLATES              [Domain: Travel ▾]             │
├─────────────────────────────────────────────────────────────────┤
│  travel.trip_summary     [Edit]  used 14×  avg flag rate 2%    │
│  travel.day_itinerary    [Edit]  used 31×  avg flag rate 8% ⚠️ │
│  travel.logistics        [Edit]  used 6×   avg flag rate 0%    │
│  travel.documents        [Edit]  used 3×   avg flag rate 0%    │
│                                                [+ New Template] │
├─────────────────────────────────────────────────────────────────┤
│  EDITING: travel.day_itinerary                                  │
│                                                                 │
│  Description: [Single day breakdown for a trip____________]     │
│                                                                 │
│  Sections:                                                      │
│  ✅ date_header    required   [{day} {date} — {destination}]   │
│  ☐  accommodation  optional   [🏨 {name}, check-in {time}  ]   │
│  ✅ activities     required   [list, emoji per item, time   ]   │
│  ☐  transport      optional   [🚗/✈️ {detail}              ]   │
│  ☐  notes          optional   [plain text, max 1 line       ]   │
│  [+ Add Section]                                                │
│                                                                 │
│  Max length:  [400      ] chars                                 │
│  Tone:        [concise, practical_________________________]     │
│                                                                 │
│  Example response:                                              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 📅 Tuesday 8 Jul — Lisbon                               │   │
│  │ 🏨 Bairro Alto Hotel, no check-in (mid-stay)           │   │
│  │ 🗺️ 10am Jerónimos Monastery                            │   │
│  │ 🍽️ 1pm Time Out Market                                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  [Test with last query of this type]  [Save]  [Save as new]    │
└─────────────────────────────────────────────────────────────────┘
```

**Flag rate** shown per template — `travel.day_itinerary` at 8% is a signal the template needs work. Clicking through shows which interactions using that template were flagged and why.

**[Test with last query of this type]** replays the most recent interaction that used this template against the updated version — same as the Replay tool but scoped to templates.

---

### Schema

```sql
CREATE TABLE response_templates (
  id              TEXT PRIMARY KEY,    -- e.g. 'travel.day_itinerary'
  domain          TEXT NOT NULL,
  subtype         TEXT NOT NULL,
  depth           TEXT NOT NULL,       -- 'summary' | 'detail'
  description     TEXT,
  sections        JSONB NOT NULL,      -- array of {key, required, format}
  max_length      INTEGER DEFAULT 400,
  tone            TEXT,
  example         TEXT,
  version         INTEGER DEFAULT 1,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- interaction_log gets two new columns:
ALTER TABLE interaction_log ADD COLUMN
  template_id     TEXT,               -- which template was used
  intent_subtype  TEXT,               -- e.g. 'day_itinerary'
  intent_depth    TEXT;               -- 'summary' | 'detail'
```

---

### FastAPI Additions

```
GET  /templates                          ← list all templates with usage stats
GET  /templates/{id}                     ← single template detail
POST /templates                          ← create new template
PATCH /templates/{id}                    ← update template (bumps version)
GET  /templates/{id}/interactions        ← log entries that used this template
POST /templates/{id}/test                ← replay last interaction using updated template
```

---

### Merge Notes for Claude Code

**Changes to existing pipeline:**
1. Extend phi3.5-mini classification prompt to output `subtype` and `depth` in addition to `domain` (or add a second lightweight classification step)
2. In the FastAPI response generation endpoint, look up the matching template by `(domain, subtype, depth)` and inject it into the system prompt
3. Log `template_id`, `intent_subtype`, `intent_depth` alongside existing interaction log fields
4. Add `response_templates` table to PostgreSQL init script, seeded with the initial template library above

The template lookup is a simple PostgreSQL row fetch — negligible latency impact.

---

### File Structure Additions

```
graph-explorer/
└── src/
    └── components/
        ├── TemplateLibrary.jsx        ← list of templates with flag rate
        ├── TemplateEditor.jsx         ← edit sections, example, tone, max_length
        └── TemplateTestPanel.jsx      ← replay last query against updated template
```

---

## Calendar & Billing Quality Enhancement

**This is an enhancement to existing Family Brain features**, not a new feature. The calendar integration, bill tracking, and n8n event handling already exist in the v2.1 system spec. This section defines three improvements to those existing capabilities:

1. **Entity completeness schemas** — what a complete record looks like for each calendar-adjacent entity type, so the system knows when it has enough information to respond well and when it should prompt for more
2. **Calendar entry quality standards** — what a good rendered calendar entry looks like per entity type, including reminders, so creation and sync are consistent
3. **Invoice/receipt reconciliation** — matching inbound receipts to open bills and flipping their status automatically, extending the existing confidence-gated write pattern

These three connect: the completeness schema defines what fields must exist, the calendar standard defines how they render, and reconciliation uses those same fields to match receipts to invoices.

---

### Background: What Already Exists

The current system (v2.1) has:
- Bill nodes with `amount`, `due_date`, `status` written to AGE on invoice ingestion
- n8n calendar event creation for appointments and school activities
- Confidence-gated WhatsApp confirmation before graph writes
- Receipt filing to Nextcloud on inbound image/PDF

What's missing is a **shared definition of completeness** across entity types — the system can create a Bill node without a cost centre, or an Appointment without a location, and there's nothing enforcing or prompting for the gap. This enhancement adds that layer.

---

### Entity Completeness Schemas

One schema per entity type. Each defines required fields (without which the entity is considered incomplete and flagged), optional fields (enrich the calendar entry and WhatsApp response), the rendered calendar entry when complete, reminder rules, and reconciliation rules where applicable.

These schemas are stored in PostgreSQL (`entity_schemas` table) and referenced by:
- The NL ingestor — knows what to try to extract for each entity type
- The calendar creation flow — prompts for missing required fields before confirming
- The Quality Lab — `missing_context` flags trace to a specific missing field
- The response templates — know which fields to expect in `context_snapshot`
- The reconciliation engine — knows which fields to match on

---

#### Bills & Invoices

**Utility Bill** (electricity, gas, water, rates)

Required fields:
- `provider` — supplier name
- `account_number` — billing account reference
- `amount` — amount due
- `due_date` — payment due date
- `billing_period_start` + `billing_period_end` — period covered
- `cost_centre` — NDIS / Trust / Company / Person (entity attribution)
- `status` — `unpaid` | `paid` | `overdue`

Optional fields:
- `payment_method` — direct debit / BPAY / manual
- `bpay_biller_code` + `bpay_ref` — for BPAY payment
- `receipt_ref` — populated on reconciliation
- `paid_date` — populated on reconciliation
- `file_path` — Nextcloud path to invoice PDF

Golden calendar entry:
```
📄 Ausgrid — $312.40 due
💰 Cost centre: Smith Family Trust
📅 Due: Thursday 19 Jun
🔴 Status: UNPAID
Acc: 4123 8821 · Period: 1 Apr–30 Jun
BPAY: 3456 / 8821001
```

Reminder: 3 days before `due_date` → WhatsApp: "⚠️ Ausgrid bill $312.40 due in 3 days — Smith Family Trust"

Reconciliation: match inbound receipt on `provider` (fuzzy) + `amount` (exact or ±$0.50) + date within 7 days of `due_date` → flip `status` to `paid`, set `paid_date`, attach `receipt_ref`

---

**Insurance Premium**

Required fields:
- `insurer` — insurer name
- `policy_number`
- `policy_type` — life / income protection / landlord / vehicle / travel / health
- `premium_amount`
- `frequency` — monthly / annual
- `renewal_date`
- `cost_centre`
- `status` — `current` | `due` | `lapsed`

Optional fields:
- `coverage_summary` — one line description of what's covered
- `insured_entity` — property address / vehicle rego / person name
- `excess` — policy excess amount
- `file_path` — certificate of currency

Golden calendar entry (annual renewal):
```
🛡️ NRMA — Landlord Insurance renewal
📋 Policy: NRM-4821-LLX
💰 Cost centre: Smith Family Trust (14 Maple St)
📅 Renewal: 1 Aug · Premium: $1,840/yr
Coverage: Landlord + loss of rent + public liability
```

Reminder: 30 days before `renewal_date` → WhatsApp: "🛡️ NRMA Landlord Insurance renewal in 30 days — $1,840. Review or renew."

---

**NDIS Invoice / Service Delivery**

Required fields:
- `provider` — provider name
- `provider_abn`
- `service_date` (or date range)
- `support_category` — Core / Capacity Building / Capital
- `line_items` — array of `{description, hours_or_units, rate, total}`
- `invoice_total`
- `invoice_ref` — provider's invoice number
- `status` — `unpaid` | `claimed` | `paid`

Optional fields:
- `support_worker` — individual worker name if known
- `claim_ref` — NDIS portal claim reference (populated after claiming)
- `file_path`

Golden calendar entry:
```
🧩 Ability Action Australia — Support session
👤 Olivia · Support worker: Jamie
📅 Tue 11 Jun, 10am–12pm (2hrs)
💰 Core — Daily Activities · $117.40
Invoice: AAA-2024-0892 · 🔴 UNCLAIMED
```

Reminder: none on creation. Alert if `status = unpaid` and `service_date` > 14 days ago → WhatsApp: "⚠️ NDIS invoice from Ability Action (11 Jun) not yet claimed — $117.40"

---

**Recurring Payment**

Required fields:
- `name` — payment name
- `payee`
- `amount`
- `frequency` — weekly / fortnightly / monthly / quarterly / annual
- `next_due`
- `cost_centre`
- `payment_method`
- `status` — `active` | `paused` | `cancelled`

Optional fields:
- `account_bsb` + `account_number` — destination account
- `category` — subscription / loan / insurance / rates / other
- `cpi_track` — boolean, whether to track year-on-year uplift vs CPI

Golden calendar entry:
```
🔁 Mortgage repayment — 14 Maple St
🏦 CBA → Smith Family Trust loan
💰 $2,340/month · Cost centre: Trust
📅 Next: 1 Jul (monthly, direct debit)
YoY change: +$24 vs last year (+1.0% vs CPI 3.2%)
```

Reminder: 2 days before `next_due` if `payment_method != direct_debit` → WhatsApp: "💸 [name] $[amount] due in 2 days — manual payment required"

---

#### Health & Medical

**Appointment**

The appointment record is not static — it is progressively enriched as the date approaches. The context retrieved from the graph and sent to the model when someone queries this appointment depends on how far away the date is. A query 6 months out gets a thin response focused on logistics. The same query 2 weeks out gets a clinically dense response including historical observations, current medications, and open questions for the visit. This is handled by the MCP context retrieval layer, not by changing the appointment node itself.

Required fields (captured at booking):
- `practitioner` — name
- `specialty`
- `clinic_name`
- `clinic_address`
- `date`
- `time`
- `person` — which family member
- `status` — `scheduled` | `confirmed` | `completed` | `cancelled`

Optional fields (populated as available):
- `referral_required` — boolean
- `referral_expiry` — date
- `medicare_provider_number`
- `estimated_cost` + `gap_amount`
- `parking_notes`
- `telehealth` — boolean
- `agenda_notes` — reason for visit, what to discuss
- `reminder_sent` — boolean

**Progressive Enrichment Timeline**

The `get_health_context()` MCP function checks `days_until_appointment` when building context for any appointment query, and adjusts what it retrieves accordingly:

```
days_until > 60   →  TIER 1: logistics only
days_until 14–60  →  TIER 2: logistics + agenda
days_until 1–14   →  TIER 3: logistics + agenda + last 3 observations + current medications
days_until = 0    →  TIER 4: TIER 3 + freshness check (re-query graph for any updates since yesterday)
appointment past  →  TIER 5: post-visit — prompt to capture outcome notes
```

**TIER 1 — Logistics only** (> 60 days out)

Context retrieved: appointment node fields only.

Golden calendar entry:
```
🩺 Dr Sarah Chen — Paediatrician
👤 Olivia
📅 Thu 20 Jun, 2:30pm
📍 Greenslopes Paediatrics, 15 Calam Rd
Referral: required ✅ expires Aug 2024
Gap: ~$95 · Provider: 2184736B
```

WhatsApp response to "when is Olivia's next appointment?":
```
📅 Olivia — Dr Chen (Paediatrician)
Thu 20 Jun, 2:30pm · Greenslopes Paediatrics
Referral ✅ · Gap ~$95
```

---

**TIER 2 — Logistics + Agenda** (14–60 days out)

Context retrieved: appointment node + any `agenda_notes` + referral status + gap estimate.

Golden calendar entry adds:
```
📋 Agenda: Review Concerta dosage, discuss sleep issues, growth check
🔖 Referral: ✅ expires Aug (renew if further visits needed)
```

WhatsApp response adds agenda summary to logistics.

---

**TIER 3 — Full clinical context** (1–14 days out)

Context retrieved: appointment node + agenda + last 3 completed Appointment nodes for same practitioner (or same specialty if practitioner varies) + outcome notes from those visits + current active Medication nodes for this person + any open clinical questions flagged in previous visits.

Golden calendar entry (2 weeks out):
```
🩺 Dr Sarah Chen — Paediatrician
👤 Olivia · Thu 20 Jun, 2:30pm
📍 Greenslopes Paediatrics, 15 Calam Rd
Referral ✅ · Gap ~$95 · Parking: free on-site

📋 AGENDA
• Review Concerta dosage (currently 36mg)
• Sleep difficulties — onset ~8 weeks ago
• Growth check — last weight 28.4kg (Mar)

💊 CURRENT MEDICATIONS
• Concerta 36mg — daily (1 repeat remaining ⚠️)
• Melatonin 2mg — nightly PRN

📓 LAST 3 VISITS — Dr Chen
Mar 2024: Dosage increased 27→36mg. Tolerating well, appetite slightly reduced. Follow up 3 months.
Nov 2023: Annual review. Development on track. Continued current plan.
Aug 2023: Behaviour concerns raised. Referred to OT (completed).

❓ OPEN QUESTIONS
• Script renewal needed at this visit
• Ask about sleep — is this medication-related?
```

WhatsApp response to "what's happening at Olivia's appointment Thursday?":
```
🩺 Olivia — Dr Chen Thursday 2:30pm

Agenda: Concerta review (36mg, 1 repeat left ⚠️), sleep issues (8wks), growth check.

Last visit Mar: dose increased, tolerating well.
Nov: annual review, on track.

Current meds: Concerta 36mg daily, Melatonin 2mg nightly.

Ask about sleep — possibly medication-related.
Need script renewal at this visit.

📍 Greenslopes Paediatrics · Referral ✅ · Gap ~$95
```

---

**TIER 4 — Day-of freshness check** (day of appointment)

Same as TIER 3 context, plus:
- Re-query graph for any Appointment, Medication, or observation nodes updated in the last 24hrs for this person
- If anything has changed since TIER 3 was last served, flag it: "⚠️ Updated since yesterday: [field]"
- Check referral hasn't expired overnight
- Check if any new medication changes were logged

WhatsApp reminder (morning of):
```
🩺 Olivia — Dr Chen TODAY 2:30pm
📍 Greenslopes Paediatrics · Gap ~$95

Agenda: Concerta review, sleep, growth check.
Script renewal needed today.
Referral ✅ (no changes since yesterday)

Last visit Mar: dose up, tolerating well.
```

---

**TIER 5 — Post-visit capture** (within 24hrs after appointment time passes)

n8n scheduled trigger fires when `appointment.date + time` has passed and `status` is still `scheduled` or `confirmed` (i.e. not manually marked `completed`).

Sends WhatsApp:
```
🩺 Olivia saw Dr Chen today — how did it go?
Reply with any notes and I'll update her record.
(Or just reply "done" to mark it complete with no notes.)
```

Inbound reply is processed by the existing NL ingestion pipeline, with context hint `health` and the appointment node ID pre-loaded. Extracted observations are written as properties on the appointment node (`outcome_notes`) and a new relationship `PRECEDED_BY` links this appointment to the previous one, making the chain queryable for future TIER 3 context.

---

**MCP Implementation Note**

The progressive enrichment is implemented in `get_health_context()` in the MCP server:

```python
def get_health_context(appointment_id: str) -> dict:
    appt = graph.get_node(appointment_id)
    days_out = (appt.date - today()).days

    context = { "appointment": appt.properties }

    if days_out <= 60:
        context["agenda"] = appt.properties.get("agenda_notes")
        context["referral_status"] = get_referral_status(appt)

    if days_out <= 14:
        context["last_3_visits"] = get_prior_appointments(
            person=appt.person,
            practitioner=appt.practitioner,
            specialty=appt.specialty,
            limit=3,
            status="completed"
        )
        context["current_medications"] = get_active_medications(appt.person)
        context["open_questions"] = appt.properties.get("open_questions", [])

    if days_out == 0:
        context["freshness_check"] = get_recent_updates(
            person=appt.person,
            since_hours=24
        )

    return context
```

The response template `health.appointment_summary` reads `days_out` from the context and selects the appropriate golden entry shape to inject into the prompt. No changes to the model call itself — just richer context and a different template example.

---

**Schema Additions to Appointment Node**

New properties added to existing Appointment nodes in AGE:

```
agenda_notes        text     — reason for visit, topics to cover
outcome_notes       text     — captured post-visit
open_questions      text[]   — list of things to raise or follow up
enrichment_tier     integer  — last tier served (1–5), for debugging
last_enriched_at    datetime — when context was last built at this tier
```

New relationship added:
```
(Appointment)-[:PRECEDED_BY]->(Appointment)
```
Written post-visit, links the appointment chain for the same person + practitioner, making `get_prior_appointments()` a simple graph traversal rather than a date-sorted query.

Reminders (schema-driven, replacing ad-hoc n8n nodes):
- 48hrs before → TIER 3 context summary → WhatsApp
- Day of (morning) → TIER 4 freshness check → WhatsApp
- 24hrs after (if status not `completed`) → TIER 5 post-visit capture prompt → WhatsApp

---

**Medication / Script**

Required fields:
- `medication_name`
- `dose`
- `frequency`
- `repeats_remaining`
- `prescriber`
- `script_number`
- `action_date` — when to book repeat or new script

Optional fields:
- `pharmacy` — preferred pharmacy
- `controlled_drug` — boolean (affects how far ahead to book)
- `pbs_item_code`
- `person`

Golden calendar entry (action date):
```
💊 Concerta 36mg — repeat script
👤 Liam · Prescriber: Dr Nguyen
📅 Action by: 25 Jul
⚠️ 1 repeat remaining — book GP before this date
Script: 8821-042A · Controlled drug
```

Reminder: 14 days before `action_date` → WhatsApp: "💊 Liam — Concerta repeat due. 1 repeat remaining, book Dr Nguyen before 25 Jul. ⚠️ Controlled drug — book early."

---

#### Travel & Holidays

**Trip (wrapper)**

Required fields:
- `destination`
- `trip_name`
- `start_date` + `end_date`
- `travellers` — array of person names
- `status` — `planning` | `booked` | `departed` | `completed`

Optional fields:
- `trip_notes`
- `travel_insurance_policy` — link to InsurancePolicy node

The Trip node blocks out time on the calendar between `start_date` and `end_date`. Individual child nodes (Flight, Accommodation, Activity) attach to it and populate the day-level detail.

Golden calendar entry (trip summary):
```
✈️ Portugal & Spain — Family trip
👨‍👩‍👧 Glenn, Shannon, Liam, Olivia
📅 4 Jul – 22 Jul (18 nights)
🛡️ Travel insurance: NIB · Policy TRV-8821
Status: BOOKED ✅
```

---

**Flight**

Required fields:
- `airline`
- `flight_number`
- `departure_airport` + `departure_time`
- `arrival_airport` + `arrival_time`
- `booking_ref` — PNR
- `direction` — outbound / return / internal

Optional fields:
- `terminal` — departure terminal
- `seat_numbers` — array per traveller
- `check_in_opens` — datetime
- `baggage_allowance`
- `frequent_flyer_refs` — per traveller
- `file_path` — e-ticket PDF

Golden calendar entry:
```
✈️ QF1 — Sydney → London (outbound leg 1)
📅 Thu 4 Jul, departs 16:05 T1
🛬 Arrives Fri 5 Jul, 05:30 LHR T3
🎫 PNR: XKPL92 · Seats: 24A, 24B, 25A, 25B
🧳 23kg checked · Check-in opens 4 Jul 04:05
```

Reminders:
- 48hrs before departure → WhatsApp: "✈️ QF1 departs Thursday 16:05 T1. Check-in opens tomorrow 04:05. PNR: XKPL92. Seats: 24A/B 25A/B."
- 24hrs before → WhatsApp: "✈️ Flight tomorrow. Bags packed? Check-in open now."

---

**Accommodation (per stay)**

Required fields:
- `property_name`
- `address`
- `check_in_date` + `check_in_time`
- `check_out_date` + `check_out_time`
- `confirmation_number`
- `contact_phone` — hotel front desk

Optional fields:
- `room_type`
- `breakfast_included` — boolean
- `parking_available` — boolean
- `wifi_details`
- `loyalty_ref` — hotel loyalty number
- `file_path` — booking confirmation PDF
- `notes` — e.g. "sea view room requested", "early check-in requested"

Golden calendar entry (check-in day):
```
🏨 Bairro Alto Hotel — CHECK IN
📍 Rua do Norte 28, Lisboa
📅 Sat 6 Jul · Check-in from 3pm
🏷️ Conf: BAH-29841 · Tel: +351 21 340 8288
🛏️ Superior double × 2 · Breakfast ✅
Loyalty: Marriott #8821004
```

Golden calendar entry (mid-stay days — shown as hotel name only):
```
🏨 Bairro Alto Hotel (night 2 of 4)
📍 Lisbon
```

Reminder (check-in day): morning → WhatsApp: "🏨 Checking in to Bairro Alto Hotel today from 3pm. Conf: BAH-29841. Tel: +351 21 340 8288."

---

**Activity / Tour**

Required fields:
- `activity_name`
- `date` + `time`
- `operator` — company or venue name
- `booking_ref`
- `meeting_point`
- `contact_phone` — operator contact

Optional fields:
- `duration`
- `what_to_bring`
- `included` — what's included (meals, transport, etc.)
- `notes`

Golden calendar entry:
```
🎭 Fado Show — Clube de Fado
📅 Tue 8 Jul, 8pm (2hrs)
📍 Rua de São João da Praça 94, Alfama
🎫 Booking: CDF-0421 · Tel: +351 21 885 2704
Meeting point: at the door, 7:45pm
Dinner included ✅
```

Reminder: 3hrs before → WhatsApp: "🎭 Fado show tonight 8pm — Clube de Fado, Alfama. Meet at door 7:45pm. Dinner included. Tel: +351 21 885 2704."

---

#### School & Family Activities

**School Activity / Sport**

Required fields:
- `child` — person name
- `activity_name`
- `day_of_week`
- `term` — which school term applies
- `uniform_or_equipment` — what to bring

Optional fields:
- `location` — if off-campus
- `cost` + `cost_status` — paid / unpaid
- `permission_slip_required` + `permission_slip_due`
- `coach_or_teacher` + `contact`

Golden calendar entry:
```
⚽ Liam — Football (Term 3)
📅 Every Wednesday · Week 4 of 10
👕 Gold sports polo + shorts + boots
📍 School oval
Coach: Mr Patterson · 0412 000 000
```

Reminder: Sunday evening during term → WhatsApp: "👕 Tomorrow: Liam needs gold sports polo + shorts + boots for football."

---

### Invoice / Receipt Reconciliation

**Enhancement to existing receipt ingestion flow.**

Currently when a receipt arrives (image or PDF via WhatsApp), it is filed to Nextcloud and a ReceiptNode is written to the graph. This enhancement adds a matching step that attempts to link the receipt to an open Bill node and update its status.

**Revised receipt ingestion flow:**

```
Receipt arrives (WhatsApp image/PDF)
      │
      ▼
Existing flow: OCR → extract {supplier, amount, date, ref}
      │
      ▼  NEW STEP
MCP: find_matching_bill(supplier, amount, date)
      │
      ├── match found, confidence ≥ 0.85
      │       │
      │       ▼
      │   auto-reconcile:
      │   Bill.status → paid
      │   Bill.paid_date → receipt date
      │   Bill.receipt_ref → receipt node id
      │   Bill.file_path → Nextcloud path
      │   WhatsApp: "✅ Ausgrid bill $312.40 matched and marked paid."
      │
      ├── match found, confidence 0.60–0.85
      │       │
      │       ▼
      │   WhatsApp confirmation:
      │   "Receipt: Ausgrid $312.40 (18 Jun).
      │    Match this to your bill due 19 Jun? [yes/no]"
      │       │ yes
      │       ▼
      │   reconcile as above
      │
      └── no match found
              │
              ▼
          Write ReceiptNode with status: unmatched
          WhatsApp: "Receipt from Ausgrid $312.40 filed.
                     No open bill found — filed for review."
```

**Matching logic (in MCP `find_matching_bill`):**

Score each open Bill node:
- Supplier fuzzy match (Levenshtein ≤ 2, or known alias) → +0.5
- Amount exact match → +0.3 / within ±$0.50 → +0.2 / within ±5% → +0.1
- Date within 7 days of `due_date` → +0.2
- Cost centre consistent with sender → +0.1
- Sum scores → confidence

**Visual state in calendar:**

| Bill status | Calendar display |
|-------------|-----------------|
| `unpaid` | 🔴 Red — amount + due date prominent |
| `paid` | 🟢 Green — amount + paid date, muted |
| `overdue` | 🔴 Red + ⚠️ — days overdue shown |
| `claimed` (NDIS) | 🟡 Amber — awaiting payment |

---

### Schema Additions

```sql
-- Entity completeness schema definitions
CREATE TABLE entity_schemas (
  entity_type       TEXT PRIMARY KEY,   -- e.g. 'bill.utility', 'travel.flight'
  required_fields   JSONB NOT NULL,     -- [{key, label, type, hint}]
  optional_fields   JSONB NOT NULL,
  reminder_rules    JSONB,              -- [{trigger_field, offset_days, message_template}]
  reconcile_config  JSONB,             -- {match_fields, confidence_threshold, auto_above}
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Bill node additions (ALTER existing Bill node properties in AGE)
-- New properties on Bill nodes:
--   status: 'unpaid' | 'paid' | 'overdue' | 'claimed'
--   paid_date: date
--   receipt_ref: AGE ReceiptNode id
--   cost_centre: 'NDIS' | 'Trust' | 'Company' | 'Person'
--   billing_period_start, billing_period_end: date
--   bpay_biller_code, bpay_ref: text

-- Receipt node additions:
--   matched_bill_ref: AGE Bill node id
--   match_confidence: float
--   match_method: 'auto' | 'confirmed' | 'manual'
```

---

### FastAPI Additions

```
GET  /schemas                              ← list all entity schemas
GET  /schemas/{entity_type}               ← single schema with fields + reminders
PATCH /schemas/{entity_type}              ← update schema fields or reminder rules

POST /reconcile/match                     ← manual trigger: match a receipt to a bill
GET  /reconcile/unmatched                 ← list unmatched receipts
GET  /bills/open                          ← list unpaid/overdue bills by cost centre
GET  /bills/summary                       ← total outstanding by entity
```

---

### Merge Notes for Claude Code

**Changes to existing pipeline:**

1. Receipt ingestion (existing n8n + MCP flow): after OCR extraction, call `MCP.find_matching_bill()` before filing. Insert the confidence-gated branch described above. This is a new step inserted into an existing n8n workflow node sequence.

2. Calendar event creation (existing n8n flow): before writing the calendar event, call `GET /schemas/{entity_type}` and check all required fields are present on the source node. If any are missing, send a WhatsApp prompt listing the missing fields and hold the calendar write until they are supplied or explicitly skipped.

3. Reminder scheduling (existing n8n): reminder rules are currently ad-hoc per workflow. Replace with schema-driven rules: on node creation or update, read `entity_schemas.reminder_rules` for that entity type, calculate trigger datetimes, and create n8n scheduled webhook triggers accordingly.

4. No changes to AGE schema structure — new fields are added as properties on existing node types, consistent with the flexible property model already in use.

---
|----------|--------|-----|
| Person, School | Blue | #4A9EFF |
| HealthPractitioner, Medication, Appointment | Green | #4AFF91 |
| NDISPlan, NDISProvider, NDISReceipt | Orange | #FF8C42 |
| Property, Trust, LoanFacility, Bill | Purple | #B44AFF |
| Trip, Flight, Accommodation | Cyan | #4AFFEE |
| InsurancePolicy, InsuranceClaim | Yellow | #FFD94A |
| Vehicle | Red | #FF4A4A |
| RecurringPayment | Pink | #FF4AB0 |
| (unknown) | Grey | #888888 |

---

## Phased Delivery

### Phase 1 — Core Viewer (send to Claude Code first)
- Canvas with Cytoscape.js
- Query bar with preset queries
- Node/edge click → Detail Panel (read-only)
- Display options: name / ID / label
- Label filter checkboxes
- Layout algorithm selector
- FastAPI: `POST /graph/query`, `GET /graph/labels`, `GET /graph/relationship-types`

### Phase 2 — Editing
- Add Node dialog
- Add Edge dialog
- Inline property edit in Detail Panel
- Delete node / edge with confirmation
- FastAPI: full CRUD endpoints

### Phase 3 — DB Tools
- Full Edit Node modal (bulk property editor)
- Schema Inspector panel
- Batch Property Setter
- Node Merge Tool
- Cypher Console tab
- FastAPI: `GET /graph/schema/{label}`

### Phase 4 — Natural Language Ingestor
- IngestorPanel with text input and context hint dropdown
- `/ingest/extract` FastAPI endpoint with qwen2.5:32b prompt
- IngestReviewPanel with per-item approve/reject/edit
- `/ingest/commit` FastAPI endpoint with MERGE/CREATE logic
- Canvas highlight of newly written nodes after commit
- Ingest history log (localStorage)

### Phase 5 — Response Quality Lab
- `interaction_log` PostgreSQL table + `prompt_versions` table
- Non-blocking log write added to existing pipeline after WhatsApp send
- `/quality/*` FastAPI endpoint group
- QualityLab tab: interaction log, detail panel, flag panel, ideal response editor
- Quality dashboard with flag breakdown by domain
- Replay tool (re-run stored context through current prompt)
- Example set export (`/quality/examples`)

### Phase 6 — Emoji Feedback + Response Templates
- `whatsapp_message_id` stored in interaction log at send time
- n8n reaction branch: emoji → `/quality/reaction` → auto-flag
- `emoji_feedback` and `whatsapp_message_id` columns on `interaction_log`
- `response_templates` PostgreSQL table seeded with initial library
- phi3.5-mini classifier extended to output `subtype` + `depth`
- Template lookup injected into FastAPI response generation prompt
- `template_id`, `intent_subtype`, `intent_depth` logged per interaction
- Template Library + Template Editor UI in Quality Lab tab
- Template test/replay panel

### Phase 7 — Calendar & Billing Quality Enhancement
- `entity_schemas` PostgreSQL table seeded with all entity types
- Receipt ingestion: add `find_matching_bill()` MCP call + confidence-gated reconciliation branch in n8n
- Bill node: add `status`, `paid_date`, `receipt_ref`, `cost_centre`, billing period + BPAY fields
- Receipt node: add `matched_bill_ref`, `match_confidence`, `match_method`
- Calendar creation flow: schema completeness check before event write, WhatsApp prompt for missing required fields
- Reminder scheduling: replace ad-hoc n8n reminders with schema-driven `reminder_rules`
- `/schemas/*` and `/reconcile/*` and `/bills/*` FastAPI endpoint groups
- Graph Explorer: bill status colour (red/green) on Bill nodes in canvas

### Phase 8 — Polish
- Property value filter (not just label filter)
- Search/highlight on canvas
- Query history persistence
- Node size scaled by degree
- Property type badges
- Keyboard shortcuts

---

## Open Questions

| # | Question | Owner | Blocking? |
|---|----------|-------|-----------|
| 1 | AGE supports one label per node in v1.5 — confirm version in use and whether multi-label is available | Engineering | Phase 2 |
| 2 | Should the explorer be served by FastAPI on `/explorer` route, or run as a separate dev server on a different port? | Engineering | Phase 1 |
| 3 | What LIMIT should the default "Show All" query use? 100 nodes may be too few for some domains but too many for rendering performance | Product | Phase 1 |
| 4 | Node Merge Tool — is this needed in v1 or can it wait for Phase 3? | Product | No |
| 5 | Should deleted nodes/edges be soft-deleted (flagged) or hard-deleted from AGE? | Engineering | Phase 2 |
| 6 | Ingestor LLM: confirm whether qwen2.5:14b (Arc GPU) is accurate enough for entity extraction, saving qwen2.5:32b (CPU) for query responses only | Engineering | Phase 4 |
| 7 | Should the ingestor also accept image input (photo of a document) via OpenVINO OCR before NL extraction? | Product | Phase 4 |
| 8 | Ingest history: localStorage fine for v1 — should it eventually persist as an AGE node type (IngestRun) for auditability? | Product | No |
| 9 | Quality Lab: should the Replay tool show a diff view (highlighting changed words) or just side-by-side text? | Product | Phase 5 |
| 10 | Prompt templates: store as versioned PostgreSQL rows or as files in the repo? Files are easier to version-control; rows are easier to reference from logs and edit via UI | Engineering | Phase 6 |
| 11 | Emoji feedback: does the WhatsApp bridge (wa-bridge) deliver reaction events as a distinct message type, or do reactions need to be parsed from a different event stream? Confirm before Phase 6 | Engineering | Phase 6 |
| 12 | Intent subtype classification: extend phi3.5-mini prompt (faster, same model) vs add a dedicated second classification step with a larger model (more accurate, adds ~200ms latency)? | Engineering | Phase 6 |
| 13 | Template depth detection: summary vs detail from the query text — confirm whether phi3.5-mini can reliably distinguish these, or whether a rule-based fallback (date mentioned → detail, no date → summary) is more reliable | Engineering | Phase 6 |

| 17 | Progressive enrichment: TIER 3 retrieves last 3 visits for same practitioner OR same specialty — which should take priority when the practitioner varies (e.g. locum covers)? | Product | Phase 7 |
| 18 | Post-visit capture (TIER 5): if the WhatsApp reply is thin ("fine", "good"), should the system prompt for more detail or accept it as-is and mark complete? | Product | Phase 7 |
| 19 | `PRECEDED_BY` relationship written post-visit — should this be written automatically when TIER 5 fires, or only when outcome notes are actually supplied? | Engineering | Phase 7 |
| 15 | Calendar completeness prompt: when required fields are missing, should the WhatsApp prompt ask for all missing fields in one message, or one field at a time? | Product | Phase 7 |
| 16 | Reminder scheduling: n8n scheduled webhook triggers per entity — confirm whether n8n can handle dynamic trigger creation at scale (many bills + appointments) without performance issues | Engineering | Phase 7 |

---

## Acceptance Criteria (Phase 7 — Appointment Progressive Enrichment)

- [ ] When an appointment is queried > 60 days out, the WhatsApp response contains only logistics (practitioner, date, time, location, referral status) with no historical notes
- [ ] When queried 14–60 days out, the response adds agenda notes and referral expiry check
- [ ] When queried 1–14 days out, the response includes last 3 completed appointment outcome notes for that person + practitioner/specialty, current active medications, and any open questions
- [ ] When queried on the day of the appointment, the context is re-fetched fresh and any fields updated in the last 24hrs are flagged with ⚠️
- [ ] 48hrs before the appointment, a TIER 3 summary is sent automatically via WhatsApp without requiring a query
- [ ] Morning of the appointment, a TIER 4 freshness-checked reminder is sent automatically
- [ ] 24hrs after the appointment time if status is not `completed`, a post-visit capture prompt is sent via WhatsApp
- [ ] An inbound reply to the post-visit prompt is processed by the NL ingestor with the appointment node pre-loaded as context, and outcome notes are written to the appointment node
- [ ] The `PRECEDED_BY` relationship is created between this appointment and the previous one for the same person + practitioner after post-visit capture

## Acceptance Criteria (Phase 7 — Calendar & Billing Quality)

- [ ] When a receipt arrives and a matching bill is found with confidence ≥ 0.85, the Bill node status flips to `paid` automatically and a WhatsApp confirmation is sent
- [ ] When confidence is 0.60–0.85, a WhatsApp confirmation is sent before any status change
- [ ] When no match is found, the receipt is filed as unmatched and a WhatsApp notification is sent
- [ ] When a calendar entity is created with missing required fields, a WhatsApp prompt lists the missing fields before the calendar entry is confirmed
- [ ] Reminder messages are generated from `reminder_rules` on the entity schema, not from hardcoded n8n nodes
- [ ] Bill nodes with `status: unpaid` render with a red indicator in the graph explorer canvas
- [ ] Bill nodes with `status: paid` render with a green indicator
- [ ] `GET /bills/open` returns all unpaid and overdue bills grouped by cost centre

## Acceptance Criteria (Phase 1 MVP)

- [ ] Given the FastAPI server is running, when the explorer is opened in a browser, it loads without errors
- [ ] Given nodes and edges exist in AGE, when the default query runs, the graph renders on the canvas within 2 seconds
- [ ] When a node is clicked, the Detail Panel shows its label, AGE ID, and all properties
- [ ] When an edge is clicked, the Detail Panel shows its type, start/end node names, and properties
- [ ] When the Display dropdown is changed to "Node ID", all node labels on the canvas switch to show AGE IDs
- [ ] When a label is unchecked in the Filter Panel, all nodes of that label (and their edges) disappear from the canvas
- [ ] When a layout is selected from the dropdown, the canvas re-runs that layout algorithm and re-positions nodes
- [ ] When a Cypher query is typed and Ctrl+Enter is pressed, the canvas refreshes with the new result set
- [ ] When a query returns an error from FastAPI, a non-modal error message appears below the query bar

## Acceptance Criteria (Phase 6 — Emoji Feedback)

- [ ] When a sender reacts to a Family Brain WhatsApp message with 👎, the corresponding interaction log record is updated with `emoji_feedback = negative` and `quality_flag = emoji_flagged` within 5 seconds
- [ ] When a sender reacts with 👍, the record is updated with `emoji_feedback = positive`; if no existing flag, `quality_flag = good`
- [ ] Emoji-flagged interactions appear in the Quality Lab log with a distinct indicator and are filterable
- [ ] Reactions to messages that are not Family Brain responses return `matched: false` and make no log changes
- [ ] After reviewing and saving a specific flag type, the record shows the specific flag type and retains the emoji provenance

## Acceptance Criteria (Phase 6 — Response Templates)

- [ ] The classifier outputs `subtype` and `depth` alongside `domain` for all query types in the initial template library
- [ ] When a query matches a known template, the template sections and example are injected into the qwen2.5:32b system prompt
- [ ] The `template_id` used is recorded in the interaction log
- [ ] The Template Library UI shows all templates with usage count and flag rate
- [ ] Editing a template and saving bumps its version and takes effect on the next query of that subtype
- [ ] "Test with last query of this type" replays the stored context through the updated template and shows the result without sending to WhatsApp
- [ ] Queries with no matching template fall back to the existing domain-level prompt with no error

## Acceptance Criteria (Phase 5 — Response Quality Lab)

- [ ] After every WhatsApp response is sent, a record appears in the interaction log within 1 second (non-blocking, no impact on response latency)
- [ ] The log is filterable by domain, flag type, date range, and free text
- [ ] Clicking an interaction shows the exact response text, latency, and context node list
- [ ] "View on Canvas" highlights the correct nodes used as context for that interaction
- [ ] Selecting a flag type and saving updates the record; flag is immediately reflected in the log list
- [ ] Writing an ideal response and clicking Save persists it to the record
- [ ] [Add to Examples] marks the record; it appears in the `/quality/examples` export
- [ ] The quality dashboard shows accurate counts by domain and flag type
- [ ] The Replay tool re-runs the stored context through the current prompt and shows the new response alongside the original
- [ ] Replay never sends anything to WhatsApp

## Acceptance Criteria (Phase 4 — Ingestor)

- [ ] Given text is pasted into the ingestor and Extract is clicked, the Review Panel appears within 5 seconds with proposed nodes and edges
- [ ] High-confidence items are checked by default; low-confidence items are unchecked and collapsed
- [ ] Each proposed node shows its label, properties, and MERGE/CREATE intent
- [ ] Clicking Edit on a proposed node allows changing any property value before commit
- [ ] Clicking ✕ on any item removes it from the proposal
- [ ] Clicking Commit Selected writes only the checked items and returns AGE IDs for each
- [ ] After commit, the canvas highlights the newly created/merged nodes in a distinct flash colour
- [ ] If the LLM returns malformed JSON, an error is shown and no data is written
- [ ] The ingest run is appended to the history log after a successful commit

---

*Spec version 1.6 — Family Brain Graph Explorer — June 2026*

