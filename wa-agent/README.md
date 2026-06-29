# wa-agent

WhatsApp-facing agent. Receives messages from the WhatsApp bridge, retrieves knowledge graph context, generates responses, and handles structured commands.

## What it does

- Accepts text and voice messages from the WhatsApp bridge (`familybrain-whatsapp`)
- Routes structured commands (calendar, notifications, assets, add event, send email) to dedicated handlers that query Postgres directly
- For open knowledge queries, runs a three-stage retrieval pipeline: Cypher graph traversal → FTS + vector search → cross-encoder rerank → LLM synthesis
- Detects appointment/schedule queries and runs a targeted event search across all time (not just a fixed window), so historical appointments surface alongside upcoming ones
- Injects today's date into every LLM call so responses are temporally accurate
- Maintains per-sender conversation history (configurable window)
- Handles email composition with confirmation flow before sending
- Accepts voice messages via Whisper transcription

## Ports

| Port | Purpose |
|------|---------|
| `4002` | HTTP API |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Main message handler — routes to command or knowledge query |
| `POST` | `/ingest/text` | Store a text note via WhatsApp |
| `POST` | `/ingest/voice` | Transcribe and store a voice message |
| `POST` | `/notify` | Push a formatted message to WhatsApp (used by n8n) |
| `POST` | `/maintenance` | Nightly maintenance sweep (called by maintenance-cron) |

## WhatsApp commands

| Say | Result |
|-----|--------|
| `what's on this week` | Events in the next 7 days |
| `my notifications` | Active alerts grouped by severity |
| `my assets` | All tracked assets with upcoming dates |
| `add event: <description>` | Routes to ingestor for extraction |
| `send email about <topic> to <email>` | Composes from knowledge base, awaits confirmation |

## Retrieval pipeline

1. **Cypher** — entity name match + 1-hop neighbourhood in AGE graph
2. **FTS** — tsvector/tsquery ranked search on `personal.note`, falls back to pg_trgm
3. **Vector** — pgvector semantic similarity (top 20 candidates)
4. **Targeted event search** — when query mentions appointment/schedule keywords, searches `personal.event` title + notes across all time
5. **Hierarchy traversal** — when the query names a specific person or entity, a weighted-cost graph walk (see below) replaces flat FTS/vector matching for that branch
6. **Reranker** — cross-encoder (NPU) rescores all candidates, top 5 go to LLM
7. **Intent rules** — `config.intent_rule` table weights results by source type per query pattern (e.g. health queries boost `health_event` and `medication` sources)

## Hierarchy traversal (weighted graph walk)

Family/entity data isn't flat — it has natural direction. Asking about a child should surface a lot about *that child* (appointments, school, health) and only a little about their parents or siblings. Asking about a trust should surface a lot about *what it owns* (properties, bills) and only a little about who governs it (trustees, directors, beneficiaries).

Rather than a fixed set of joins, retrieval runs a **pseudo-Dijkstra traversal**: starting from the focal node (the detected person or entity), it expands outward through related nodes, accumulating a `traversal_cost` per hop. Each direction of travel has its own per-hop cost, and a node is only included if its accumulated cost stays under a budget — exactly Dijkstra's "settle the cheapest frontier node first, stop once you run out of budget" shape, just applied to a handful of relationship types instead of arbitrary edge weights.

```
DOWN  (cheap)   → own records: appointments, school, medications, owned properties/bills
SIDEWAYS (mid)  → siblings, partners, co-owned/related entities
UP    (expensive) → parents, trustees, directors, beneficiaries
```

Concretely: own records cost `down`, a sibling's own records cost `sideways + down`, a parent's records cost `up + down`. Anything over budget (default 30) is excluded entirely; everything under budget is included but converted to a `match_score` so cheap/close hops still outrank expensive/distant ones in the final context bundle. This is what makes the result *feel* natural — topics flow down and outward from the thing you asked about, the way you'd actually explain it to another person, rather than dumping every linked row at equal weight.

Each hierarchy type is its own independently-tunable **weighting profile** (`HierarchyProfile`: budget + down/sideways/up costs), not a shared global config — so different categories of data can have different "natural flow" shapes without the constants colliding:

- `FAMILY_HIERARCHY` — people: down=3, sideways=8, up=10, budget=30
- `ENTITY_HIERARCHY` — trusts/companies: down=3, sideways=8, up=10, budget=30 (down = properties/bills/invoices, up = trustee/director/beneficiary)
- Future: a `FINANCIAL_HIERARCHY` profile could weight investment/super structures differently (e.g. cheaper "up" toward fund performance, expensive "sideways" across unrelated accounts) without touching the other two

Override via env: `<NAME>_HIERARCHY_BUDGET`, `<NAME>_HIERARCHY_COST_DOWN`, `<NAME>_HIERARCHY_COST_SIDEWAYS`, `<NAME>_HIERARCHY_COST_UP` (e.g. `FAMILY_HIERARCHY_COST_UP=15`).

## Pushing work into the LLM (batched querying)

Where a query would otherwise require N separate LLM calls (e.g. summarising appointments across several time windows), the agent instead batches records and asks for **all windows in a single structured response** (`=== WINDOW: <name> === ... === END ===` blocks), parsed back out in Python. This trades a slightly more complex prompt for fewer, larger LLM round-trips — appointment digests batch 15 events per call and request TODAY / 3_DAYS / 1_WEEK / 1_MONTH / 3_MONTHS summaries in one shot, rather than one call per window per batch.

The appointment digest task also doubles as a pre-computed cache: results are saved back into `personal.note` (tagged `digest`/`appointments`/`window:<label>`) during nightly maintenance, so a live query naturally retrieves the pre-summarised digest instead of re-asking the LLM to walk every event at request time.

## Response personas

`config.response_persona` rows match trigger patterns and inject a context-specific system prompt. The `appointment` persona fires only on specific time-lookup queries (`when is my`, `what time is`) — general questions about appointments get a conversational prose response instead.

## Key env vars

```env
DATABASE_URL=postgresql://curator:<password>@postgres:5432/familybrain
OLLAMA_URL=http://172.23.96.1:11434
AGENT_MODEL=qwen2.5:14b
EMBED_MODEL=nomic-embed-text
INGESTOR_URL=http://ingestor:4001
WHISPER_URL=http://172.23.96.1:11435
WA_SEARCH_TOP_K=5
WA_MAX_HISTORY=6
WA_CONTEXT_WINDOW_SEC=300
WA_BRIDGE_URL=http://whatsapp:3002
WA_SELF_NUMBER=<E.164 without +>
TZ=Australia/Brisbane
```
