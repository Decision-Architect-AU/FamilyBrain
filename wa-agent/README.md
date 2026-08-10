# wa-agent

WhatsApp-facing agent. Receives messages from the WhatsApp bridge, retrieves knowledge graph context, generates responses, and handles structured commands.

## What it does

- Accepts text and voice messages from the WhatsApp bridge (`familybrain-whatsapp`)
- Routes structured commands (calendar, notifications, assets, add event, send email) to dedicated handlers that query Postgres directly
- For open knowledge queries, runs a three-stage retrieval pipeline: Cypher graph traversal → FTS + vector search → cross-encoder rerank → LLM synthesis
- Detects appointment/schedule queries and runs a targeted event search across all time (not just a fixed window), so historical appointments surface alongside upcoming ones, plus an explicit date-range parse (e.g. "28th to 30th of August") that's matched independently of keyword overlap
- When the model can't answer confidently, runs an in-request fallback (interim message → broad type-agnostic graph search → retry) instead of a flat refusal, and logs the activation for nightly self-healing review — see [Query fallback & self-healing](#query-fallback--self-healing) below
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
| `GET`  | `/api/query_flags` | Recent `/query` fallback activations (dashboard) |
| `GET`  | `/api/resolution_fixes` | Staged alias/pattern fixes from the nightly review pass, optional `?status=` filter |
| `POST` | `/api/resolution_fixes/{id}/approve` | Apply a staged fix — alias writes an `ALIAS_OF` edge, pattern activates its regex gate |
| `POST` | `/api/resolution_fixes/{id}/reject` | Discard a staged fix, no write |

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
4. **Targeted event search** — when query mentions appointment/schedule keywords, searches `personal.event` title + notes across all time, plus a parsed date range (e.g. "28th to 30th of August") matched independently of keyword overlap — a query naming a date but no matching title/notes text would otherwise return nothing even though a real event exists in that window
5. **Hierarchy traversal** — when the query names a specific person or entity, a weighted-cost graph walk (see below) replaces flat FTS/vector matching for that branch
6. **Reranker** — cross-encoder (NPU) rescores all candidates, top 5 go to LLM — **except** explicit date-range matches from step 4, which bypass reranking entirely (see below)
7. **Intent rules** — `config.intent_rule` table weights results by source type per query pattern (e.g. health queries boost `health_event` and `medication` sources)

**Date-range matches are pinned ahead of reranking, not fed into it.** The cross-encoder scores on surface text similarity and has no notion of "this row's date literally satisfies what was asked" — confirmed live that a row like *"Appointment... 17th of august"* out-ranked the actual requested-range match purely by sharing the literal words "of august". Rows whose date falls in the requested range are marked `_date_priority` and included unconditionally (not capped by `WA_SEARCH_TOP_K`); everything else still goes through reranking and fills the remaining context budget. Priority is further scoped to `provenance='email'` (a real one-off booking/confirmation) rather than `provenance='rule'` (routine-generated administrative rows — school days, medication refills) that coincidentally share the date — unscoped, a date range spanning a couple of school days pulled in enough recurring-routine noise to overwhelm the answering model's context and produce a truncated response even with a raised token budget; the fix was less context, not more tokens.

**No auto-created Concepts.** An earlier version of the Cypher stage, on finding no matching `Concept` node for a query term, asked the LLM to fabricate a one-line "definition" of that term (down to fragments like "28th") and created it as a real graph node before retrying search against it. Removed — confirmed live it actively derailed retrieval, injecting hallucinated definitions of query fragments as if they were real context and crowding out genuinely relevant results. The nodes never persisted (the connection closes without committing, an implicit rollback), so there was no lasting graph pollution, but the derailment happened within the same request regardless.

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

## Query fallback & self-healing

When the primary retrieval + 14B synthesis can't answer confidently, the system prompt has the model emit a structured signal instead of guessing:

```
INSUFFICIENT_CONTEXT
---------------
keywords: <comma-separated key terms>
concept: <best-guess concept category>
```

Detected by `fallback.py`'s `parse_model_response()` — **not** by requiring the delimiter line exactly (the model reliably writes the `INSUFFICIENT_CONTEXT` marker but sometimes drops the separator), scanning instead for the marker itself and taking the *last* occurrence of each labelled field, robust to however much reasoning/preamble surrounds it. A narrow hedge-phrase regex is a defensive secondary trigger for when the model answers in prose instead of emitting the block; a one-line `CONFIDENCE:`/`RETRIEVAL:` self-rating on every answer (stripped before the user sees it) feeds the same signal even on the happy path.

**In-request retry** (`main.py`'s `/query` handler), only on fallback:
1. Interim WhatsApp message sent immediately (`FALLBACK_INTERIM_MESSAGE`) via the existing `WA_BRIDGE_URL`/`/send` path — the retry adds ~20s, the thread should never sit silent that long
2. `config.query_flags` row written *before* the retry runs, so a crash mid-retry still leaves a reviewable record
3. `generic_search.py`'s type-agnostic search runs across every label in the schema (not the structured, per-shape lookups above) — naive regex scan, majority-keyword-match threshold (not ANY, not ALL: a single common keyword floods results, requiring all extracted keywords is too strict once there are 3+), fair per-label share of the result limit so one noisy label can't crowd out the rest
4. Same model re-run with the found context, framed as plain "Knowledge base excerpts" — **not** as a fallback/broader/uncertain search, which was confirmed live to prime the model into distrusting genuinely good context and re-emitting `INSUFFICIENT_CONTEXT` even with the real answer sitting right there
5. Final answer sent as a follow-up message; the same `query_flags` row updated in place (never a second row) with `outcome`: `recovered` / `still_empty` / `retry_error`

**Nightly review** (`query_flags_review.py`, task `review_query_flags`, throttled ~daily via the Postgres-backed pattern below): the 35B reasoning model classifies each unreviewed flag as `alias_miss` (right answer, wrong name — user's wording doesn't match the data), `pattern_gap` (right answer, right name, structured retrieval just never looks at this shape of data for this shape of question), or `data_gap` (genuinely absent). `generic_retrieval_hits > 0` with `structured_retrieval_hits = 0` on the flag row is the diagnostic shortcut — start there instead of exploratory digging.

**Staged, not auto-applied** (`config.resolution_fixes`, `resolution_fixes_store.py`): alias fixes merge an `ALIAS_OF` edge (generalised across any label, not just `Concept`) via `apply_alias_fix()`; pattern fixes store a regex that, once approved, is checked against future queries *before* the first `generate()` call — matching queries get their context enriched up front, resolving on the fast path with no fallback activation at all, rather than depending on the 14B correctly self-diagnosing every time. Nothing is written to the graph or activated until a human approves it via `/api/resolution_fixes/{id}/approve` (dashboard: **Query fallback** page).

**Morning report** (`query_flags_report.py`, task `query_flags_report`): unresolved data gaps + pending-fix count sent to the owner (`WA_SELF_NUMBER`) once daily. Never messages whoever originally asked the question.

## Model selection and the `thinking` toggle

`generate(prompt, system, model, thinking)` in `llm.py` accepts a per-call `model` override (the dashboard's chat model dropdown uses this) and a `thinking` bool, both forwarded straight through to `inference-server`'s `/api/generate`. `thinking` is meaningful only for models with a chat-template tokenizer loaded (currently just the reasoning VLM — see the main [README's model section](../README.md#the-reasoning-model--a-vlm-export-used-text-only-not-via-ovms)) and is silently ignored by qwen2.5 models, which have no such concept.

**`<answer>` extraction.** The reasoning model narrates a "Thinking Process:" preamble regardless of the thinking flag or any system-prompt instruction telling it not to — a system prompt saying "don't narrate your reasoning" gets read, acknowledged in the model's own reasoning trace, and ignored anyway. What does work: a positive, structural instruction. The system prompt (`main.py`'s `SYSTEM_PROMPT`) asks the model to wrap its final answer in `<answer>...</answer>`, and `llm.py`'s `_extract_answer()` pulls just that content out via regex, discarding everything before it. Falls back to the raw response untouched if no tags are found — every other model never emits them, so this is a no-op for the normal chat path.

## Maintenance job (`maintenance.py`)

Full reference: [`src/maintenance.md`](src/maintenance.md)

Runs nightly (or on demand via `POST /maintenance`). Handles event generation from asset rules, scheduling conflict detection, knowledge graph sync, and appointment digest pre-computation.

**Tasks in default run order:**
`rederive_facts → re_embed → link → audit_concepts → dedup → prune → detect_provider_gaps → generate_events → detect_conflicts → reconcile_ingested → refresh_asset_notes → asset_graph_sync → monitor → tune_weights → appointment_digest → routine_context_pack → notify_provider_conflicts → asset_summary → review_query_flags → query_flags_report`

`rederive_facts` runs first deliberately — it drains the re-derivation queue (facts whose sources were suppressed since the last run) before any other task reads or rewrites facts, so nothing downstream acts on stale conclusions.

`detect_provider_gaps` now runs **before** `generate_events` (not its original position) — `generate_events`' provider-aware suspension (see below) reads `personal.routine_gap`, which only `detect_provider_gaps` populates. Running them in the old order meant `generate_events` always saw the *previous* cycle's gap data.

Run a subset: `POST /maintenance?tasks=generate_events&tasks=detect_conflicts`

**`link`** (`linker.py`) — creates `ALIAS_OF`/`SIMILAR_TO` edges between `Concept` nodes. Incremental, not a full rescan: each concept gets a `linked_at` timestamp once processed, and only concepts with no `linked_at` are compared against the full set on subsequent runs — already-linked pairs keep their edges without being re-scored. Embeddings are cached on the node (`c.embedding`, JSON) and computed exactly once per concept ever, not on every run. Throttled independently via `LINK_INTERVAL_SECS` (default once/day). The very first run after this changed still processes everything (nothing has `linked_at` yet) — expected one-time backfill, not a bug.

**`audit_concepts`** (`linker.audit_concepts()`) — samples 5 already-linked concepts per graph (`CONCEPT_AUDIT_SAMPLE_SIZE`), asks the larger reasoning model (`CONCEPT_AUDIT_MODEL`, default `OpenVINO/Qwen3.6-35B-A3B-int4-ov`) to validate whether the linker's own `ALIAS_OF`/`SIMILAR_TO` edges actually hold up semantically, and zeroes any it flags as a genuine mismatch (`confidence=0, zeroed_by='system'` — same suppression mechanism as the asset dossier, re-scorable later). This runs several ~200s reasoning-model calls per invocation — a non-issue in a nightly background job, a real problem on the chat path, which is exactly why it lives here instead. Throttled independently via `CONCEPT_AUDIT_INTERVAL_SECS` (default once/day).

**`dedup`/`prune`** — merges `Concept` nodes with identical names, removes orphans. Now **throttled** (`DEDUP_INTERVAL_SECS`, default once/day, Postgres-backed — see below) — it never was before, despite `dedup`'s single Cypher call requiring `WHERE NOT EXISTS((dup)--())` (Apache AGE rejects the bare-pattern form `WHERE NOT (dup)--()` some other Cypher engines accept as an implicit `EXISTS`). Confirmed live: with no throttle, a new `dedup` pass started on every 5-minute maintenance-cron tick regardless of whether the last one had finished, and each pass holds one uncommitted transaction across its *entire* loop — 13 backends were found stuck 2–20 minutes deep in a queue waiting on the same frequently-duplicated concept names, actively blocking live `/query` traffic.

**`rederive_facts`** — for each `(asset_ref, fact_name)` queued by an edge suppression, drops the suppressed source from that fact's `factsrc_*` list and re-derives from what remains; deletes the fact (and its `factsrc_*`) entirely if no sources remain. A `fact_*` must never outlive its evidence — this is what makes suppression trustworthy instead of cosmetic.

**`asset_summary`** — for each active asset (skip-if-fresh via `facts_updated_at`, bypassed when the asset was just re-derived by suppression): assembles non-suppressed neighbourhood + current facts, derives/refreshes named `fact_*` properties, and writes one `fact_summary` one-liner via LLM, derived only from the facts (never raw documents) so it can't assert anything a fact doesn't support. Always match the Asset node by `ref` (`"personal.asset:{id}"`) — **never** by an unlabeled/undirected Cypher `MATCH`, which forces AGE to scan every vertex and edge label table in the graph rather than the much smaller `:Asset`-labeled set.

**`review_query_flags`** / **`query_flags_report`** — see [Query fallback & self-healing](#query-fallback--self-healing) above.

### Postgres-backed throttling

`link`, `audit_concepts`, and `review_data_expectations` throttle via a `/tmp/last_<task>_run` flag file — **fragile**, confirmed live: `/tmp` is container-local, so any restart (a crash, an image rebuild, a host reboot) resets it to "never run", and the very next 5-minute cron tick re-fires the expensive task immediately instead of respecting its daily interval. `dedup_prune`, `review_query_flags`, and `query_flags_report` use `config.maintenance_throttle` (`task_name, last_run_at`) instead, which survives restarts. Migrating the three older tasks to the same table is a worthwhile follow-up, not yet done.

**Collision pipeline — suspend, not delete:**
- **Stage 1 (Suppress-or-suspend)** — inside `task_generate_events`: an occurrence colliding with an *institutional* context event (`SCHOOL_HOLIDAY`/`PUBLIC_HOLIDAY` — legitimately global, no provider check needed) or a *provider-specific* gap (via `personal.routine_gap` — only suppresses if **this routine's actual provider**, resolved through `personal.event_participant`, is the one who's away) gets `status='suspended'`, a `suspended_reason`, and `suspended_by_event_id` linking to the overriding event. The row is never deleted — auto-reinstated (`_reinstate_if_suspended`) once the gap no longer applies. Replaced a blind "does any holiday-shaped event exist on this date, for anyone" check that had zero person-awareness and could misattribute an unrelated family member's time off to a routine it had nothing to do with.
- **Stage 2 (Override)** — inside `email_decomposer`: email event supersedes a generated placeholder in the same slot when rank ≥ placeholder rank
- **Stage 3 (Notify)** — `task_detect_conflicts`: sweep for overlapping `blocks_person=true` events across different slot_classes for the same person; write `personal.conflict` rows

Suspended events route to the **Tentative** calendar (`appointment_updater.py`, via the existing reroute-on-calendar-change logic — already "move, never copy", no new mechanism needed) with the suspension reason folded into the description, instead of appearing on their natural calendar.

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

# Throttled maintenance tasks (seconds, all default 86400 = once/day)
LINK_INTERVAL_SECS
CONCEPT_AUDIT_INTERVAL_SECS
DATA_EXPECTATION_REVIEW_INTERVAL_SECS
DEDUP_INTERVAL_SECS
QUERY_FLAGS_REVIEW_INTERVAL_SECS
QUERY_FLAGS_REPORT_INTERVAL_SECS
```
