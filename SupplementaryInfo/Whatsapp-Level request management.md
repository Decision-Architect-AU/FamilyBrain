# Family Brain — WhatsApp Query Fallback & Self-Healing

**Spec version:** 1.0
**Status:** Draft for Claude Code handoff
**Companion spec:** Scheduled Task Engine & Weekly Digest (`family-brain-weekly-digest-spec.md`) — independent features; the nightly review phase here shares the "extend an existing nightly job" pattern.

---

## Problem statement

The wa-agent's `/query` path (classify → retrieve → single fixed-model generate) fails flat on queries whose answer **is in the knowledge base** but which the structured retrieval path misses — e.g. asking about a mobile-service provider that exists as ingested data but doesn't match any pre-organised Cypher pattern. The system prompt correctly forbids guessing, so the user gets an honest "no relevant information found" for information the graph actually holds.

Design position (decided): this is a **retrieval-quality problem, not a knowledge-currency problem**. Web search is explicitly out — the point of the system is accessing private data that is not on the web. Escalating synchronously to the larger local model is also out for the live path — its known generation times (can exceed 300s under GPU contention, 480s budget) are unacceptable on a WhatsApp thread. The solution is a fast in-request fallback plus an offline self-healing loop.

---

## Goals

1. When the primary answer path can't respond confidently, recover **in-request** via a generic keyword query, adding roughly 20s — with the user told immediately that a retry is underway.
2. Every fallback activation is recorded for offline review, whether or not the retry succeeded.
3. A nightly self-healing pass (35B-class model, no latency pressure) converts fallback records into permanent fixes — entity aliases and reusable query patterns — so repeat questions resolve on the fast path.
4. Genuine data gaps (nothing found even offline) surface to the owner as a morning report, not silent failure and not an unsolicited message to the original asker.

## Non-goals

- **No web search**, in any tier, ever, for this feature. (Decided; also keeps the zero-external-API principle intact.)
- **No synchronous escalation to the 32B/35B-class model** in the live WhatsApp path.
- **No JSON output contract from the answering model** — the fallback signal is a plain delimiter format (below) precisely because structured-JSON reliability from the 14B under this prompt is not guaranteed.
- **No changes to the honest-refusal principle.** The model still never guesses; it now emits a machine-usable fallback signal instead of only prose.
- **No proactive follow-up messages to family members** from the nightly pass. Owner report only.

---

## Instruction to Claude Code: verify against the live code, don't assume

Line numbers and internals referenced here reflect the state at spec time; verify before editing:

1. `/query` in `wa-agent/main.py` — confirm the current classify → retrieve → generate flow and where the system prompt string lives.
2. `generate()` in `llm.py` — confirm the model-override parameter and how the dashboard chat uses it (pattern to reuse for the nightly pass, not the live path).
3. The retrieval pipeline is two-stage (vector + FTS → NPU reranker → top-5). Confirm where "retrieval came back empty/thin" is observable, because the structured self-rating (P0-1) should report the *post-rerank* hit count.
4. The nightly job in `linker.py` (concept audit) — confirm its scheduling mechanism; the review pass (P0-5) extends this job, it does not create a new scheduler.
5. Entity/alias handling — find how entity names are currently canonicalised during extraction/linking, since alias auto-fixes (P0-6) must write to the real mechanism.
6. The WhatsApp send path used for outbound messages — the interim and follow-up messages reuse it; confirm it supports two sequential sends to the same thread.

---

## Requirements

### P0-1 — Prompt output contract (fallback signal)

Extend the `/query` system prompt: when the model cannot answer confidently from the supplied context, it must not answer in prose. Instead it emits exactly:

```
INSUFFICIENT_CONTEXT
---------------
keywords: <comma-separated key terms from the user's question, e.g. ALDI, Mobile>
concept: <single best-guess concept category, e.g. telecom_subscription>
```

Additionally (both paths, answer or fallback), the model appends a machine-readable self-rating line the parser strips before sending to WhatsApp — confidence high/low plus retrieved-context sufficiency. The delimiter block is the primary signal; hedge-phrase regex ("no relevant information") is retained only as a defensive secondary trigger.

Parser rules (string operations only, no JSON):
- Response contains `---------------` → fallback mode. Lines after the delimiter split on first `:` into `keywords` and `concept`. Missing/malformed lines degrade gracefully: fall back to the entity-extraction output already produced earlier in the request as the keyword source.
- No delimiter → normal answer; strip the self-rating line; send.

**Acceptance criteria**
- [ ] A query known to be unanswerable from supplied context yields the delimiter block, parsed into non-empty keywords.
- [ ] A normally answerable query yields no delimiter and the user-visible message contains no self-rating artefacts.
- [ ] A malformed fallback block (e.g. missing `concept:` line) still triggers the fallback path using extraction-stage keywords.

### P0-2 — In-request fallback sequence

On fallback detection, strictly in this order:

1. **Send interim WhatsApp message immediately and unconditionally**: "This is taking a bit longer, trying again…" (exact copy configurable). Unconditional — the retry adds ~20s minimum; never leave the thread silent. (Decided.)
2. **Write the `query_flags` row now**, before the retry runs — so a retry crash/timeout still leaves a record, never a silent gap.
3. Run the **generic fallback query** (P0-3) with the parsed keywords.
4. Re-run the same 14B generate call with the (possibly richer) context.
5. Send the final answer as a follow-up WhatsApp message. If the retry also comes back insufficient, send an honest "couldn't find this — I've flagged it for review" style message (copy configurable).
6. **Update the same `query_flags` row** with the outcome — never insert a second row; the nightly pass needs the full before/after in one record.

**Acceptance criteria**
- [ ] Interim message arrives before the fallback query starts, every time the delimiter fires.
- [ ] Killing the process between steps 2 and 6 still leaves a flag row with `needs_review = true`.
- [ ] Exactly one flag row exists per fallback activation, containing both the original attempt and the retry outcome.
- [ ] End-to-end fallback path (interim → answer) completes within the existing request-handling model without holding a webhook response open past its timeout — restructure to async send if the current handler is synchronous.

### P0-3 — Generic fallback query

A type-agnostic search across the graph using the extracted keywords: match any node label where any keyword appears (case-insensitive) in a configured set of searchable properties (`name`, `title`, `description`, `fact_provider`, plus whatever the schema inventory shows is consistently populated — Claude Code inventories actual property usage per label and writes the per-label property list into config, not code).

- Ship the naive `CONTAINS`-scan version first. Family-scale graph size makes this acceptable; do **not** build a full-text index in v1. If measured latency on real data exceeds ~2s, note it in the flag row and raise it — the upgrade path (tsvector/GIN shadow table) is a later decision, not day-one scope.
- Results feed the same context-assembly used by the primary path (respect the same top-K cap so the second generate call's token budget is unchanged).

**Acceptance criteria**
- [ ] A query for an entity that exists in the graph but matches no structured Cypher pattern returns that entity's node(s) via the generic query and produces a substantive final answer.
- [ ] Generic query latency is logged per activation.

### P0-4 — `query_flags` table

One row per fallback activation: id, created_at, source (WhatsApp thread/message ref), original_query_text, classified_targets, extracted_keywords, concept_guess, structured_retrieval_hits, generic_retrieval_hits (null until retry), first_response_kind (fallback signal), final_answer_text (null until sent), outcome (recovered / still_empty / retry_error), needs_review (default true), reviewed_at, review_result.

**Acceptance criteria**
- [ ] Every delimiter activation produces exactly one row; every non-fallback query produces zero rows.
- [ ] Rows are queryable by the nightly pass via `needs_review = true`.

### P0-5 — Nightly self-healing review pass

Extend the existing nightly job (same scheduler as the concept audit — no new orchestration): for each unreviewed flag row, the large local model (35B-class, model override via the existing `generate()` parameter) re-attempts the query with no latency pressure — regenerating Cypher from scratch against the real schema, not merely re-reasoning over the day's failed retrieval.

Classify each row into one of three outcomes:

| Outcome | Signal | Action |
|---|---|---|
| **Alias miss** | Generic query found it during the day, or nightly pass finds it under a different canonical name | Stage an alias mapping (P0-6) |
| **Pattern gap** | Nightly Cypher succeeds where the day's structured Cypher failed, entity naming was fine | Stage the successful Cypher as a reusable pattern (P0-6) |
| **Data gap** | Nothing found even with full offline effort | Include in morning report (P0-7); no auto-fix |

Diagnostic shortcut baked in: `generic_retrieval_hits > 0` with `structured_retrieval_hits = 0` on the flag row is prima facie a pattern/alias problem — the nightly pass starts there instead of exploratory digging.

**Acceptance criteria**
- [ ] The pass runs inside the existing nightly job, processes all `needs_review` rows, and marks each with reviewed_at + review_result.
- [ ] A seeded alias-miss case and a seeded pattern-gap case each land in the correct outcome bucket.
- [ ] The pass respects the existing GPU-contention reality: sequential processing, no parallel 35B calls.

### P0-6 — Staged fixes (human check before graph writes)

**Decided: staging, not direct writes.** Alias mappings and query patterns produced by the nightly pass land in a staging table (proposed `resolution_fixes`: id, flag_id, fix_type alias|pattern, payload JSONB, status pending|approved|rejected, created_at, decided_at), reviewed via the dashboard alongside the morning report. Approval applies the fix:
- **Alias** → written into the real canonicalisation mechanism found in verification step 5.
- **Pattern** → stored where the 14B's Cypher-building prompt assembly can pull it as few-shot context, keyed by intent/concept shape.

Rationale: a nightly job silently rewriting entity-resolution rules is fine 99% of the time and confusing the one time it isn't; the approval step is one tap in a queue the owner is already checking.

**Acceptance criteria**
- [ ] No graph or alias write occurs without an approved staging row.
- [ ] After approving a pattern fix, re-asking the original question resolves on the structured fast path with no fallback activation (the end-to-end self-healing proof).

### P0-7 — Morning report

Data-gap outcomes (and a count of staged fixes awaiting approval) are compiled into a short owner-directed summary. Delivery: the scheduled-task engine from the companion spec is the natural carrier (a second task_type) — if this feature ships first, a minimal direct WhatsApp send to the owner number is acceptable and is replaced when the engine lands. Never messages the original asker proactively.

**Acceptance criteria**
- [ ] A data-gap flag from yesterday appears in this morning's report with the original question text.
- [ ] Family-member threads receive nothing from the nightly pass.

### P1 — Dashboard visibility

A simple view over `query_flags` and `resolution_fixes`: recent fallback activations with outcomes, pending fixes with approve/reject. Can reuse the existing review-queue UI patterns (thumbs-down feedback queue is the closest analogue).

### P2 — Future considerations

- Feed the existing thumbs-down feedback queue (`config.query_feedback`) into the same nightly pass — same review machinery, second input source.
- Auto-approve rule for high-confidence alias fixes after the staging process has earned trust (explicitly not v1).

---

## Delivery phases

**Phase 1 — Contract + parser + flag writes** (P0-1, P0-4, and the flag-write half of P0-2). No behaviour change for users yet beyond the self-rating strip; produces data immediately.

**Phase 2 — Live fallback** (P0-2 complete, P0-3). Interim message, generic query, follow-up answer. This is the user-visible win.

**Phase 3 — Nightly pass + staging + report** (P0-5, P0-6, P0-7).

**Phase 4 — Dashboard view** (P1).

---

## Open questions

*For Claude Code (non-blocking, resolve during Phase 1):*
1. The six verification items above.
2. Whether the current webhook/response structure already supports two sequential outbound sends per inbound message, or needs the async restructure noted in P0-2.
3. Where few-shot pattern injection best fits in the existing prompt assembly for Cypher generation.

*For the project owner:*
1. Exact copy for the interim and the still-couldn't-find messages.
2. Owner WhatsApp number/channel for the morning report (until the task engine carries it).
3. Whether thumbs-down feedback (P2) should join the nightly pass in v1.1 or later.
