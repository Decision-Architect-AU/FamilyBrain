# Build Doc — Node Properties as a Fact Store + Retrieve-then-Hydrate

**For:** Claude Code, operating inside the FamilyBrain repo
**Scope:** Make AGE node properties carry useful, structured, *lookup-only* facts; add a consistent relational **hydration handle** to entity nodes; expose a hydration path through `graph-api`; surface both in the customised **AGE Viewer** (and mirror in the dashboard graph page).
**Non-goal:** Do **not** move analytical/aggregatable facts (amounts, dates, performance series) onto nodes. Those stay in relational tables. This is a *placement* change, not a migration to a new store.

---

## 1. Background / the principle

We model three kinds of thing, each in the store that fits its access pattern:

| Kind | Example | Lives in | Why |
|---|---|---|---|
| **Relationships** | `(Bill)-[:ISSUED_BY]->(Supplier)` | Graph **edges** | Traversal, accretive, typed |
| **Lookup-only facts** | ABN, invoice number, booking ref, supplier name | Graph **node properties** | Only ever read *after* you've found the entity; never summed or trended. Keep them on the node so hydration is one hop, not two. |
| **Analytical facts** | amount, due_date, paid_date, status, yield, performance series | **Relational columns** | Summed, trended, range-queried, constrained. JSONB/agtype is poor at this. |

**Retrieve-then-hydrate** is the retrieval pattern this enables:
1. **Find** — vector/semantic search + typed-edge traversal lands a *collection* of anchor nodes (hits **plus** their relevant neighbours).
2. **Furnish** — read lookup-only facts straight off the node properties; for analytical facts, follow the node's **`ref`** handle to `SELECT` the authoritative relational row.
3. **Phrase** — generation receives a pre-assembled block of hard facts and only has to phrase them. It never invents an ABN, because the ABN was furnished, not generated.

The point of this build: today step 2 can't happen for most entities, because facts aren't on nodes and entity nodes have no `ref`.

---

## 2. Current-state findings (audited — confirm before changing)

In `ingestor/src/graph.py`:
- All node properties are written via f-string interpolation into Cypher, escaped by `_e()` which **truncates to 500 chars** and hand-escapes quotes. This is **lossy** and a **Cypher-injection** exposure.
- Entity nodes (`Concept`, `Person`, `Framework`) carry only `name` + `description` (+ sometimes `domain`). **No structured facts. No relational key.**
- `Document` nodes carry `row_id` + `schema` — this is the **only** existing hydration handle, and it's the right idea. Generalise it to all entities.
- `stamp_parse()` already accumulates list-valued properties (`parse_models`, `best_model`, `confidence`, `last_parsed_at`) via read-modify-write string juggling — precedent for richer props, but the mechanism is fragile.

In `graph-api/src/db.py`:
- `_node_to_dict()` already returns a `properties` map, so the API surface can carry facts once they exist. Good foundation; no blocker.

In `dashboard/src/app/api/graph-nodes/route.ts`:
- This route browses **relational** tables via an allowlist (`decision.theme`, `property.property`, …) — reuse this allowlist pattern for the new hydrate endpoint.

In `age-viewer/`:
- Vendored/compiled Apache AGE Viewer (Node backend under `app-root/backend/build`). Customising the built frontend is awkward; see Phase 4 for the recommended split.

**Task 0.1** — Produce a short inventory: for each graph (`personal_graph`, `property_graph`, `decision_graph`), list node labels and the property keys currently in use. Write it to `docs/graph-property-inventory.md`. Do not change anything yet.

---

## 3. Conventions to lock first

Add these to `docs/graph-conventions.md` and follow them everywhere:

- **Hydration handle:** every entity node that has a relational counterpart carries a property
  `ref = "<schema>.<table>:<id>"` — e.g. `"personal.bill:123"`, `"property_deals.property:8"`.
  One string, trivially parseable, allowlistable.
- **Lookup-only facts:** stored as **flat, prefixed, typed** node properties: `fact_abn`, `fact_invoice_no`, `fact_supplier`, `fact_booking_ref`, etc.
  - Use flat `fact_*` keys, **not** a nested agtype map — AGE's nested-map querying/escaping is uneven across versions and flat keys render cleanly in the viewer. (Confirm your AGE version's map handling before deviating.)
  - Add `facts_updated_at` (ISO string) whenever any `fact_*` is written.
- **Display keys:** keep `name` (and `label`) for display; never overload them with facts.
- **Placement rule (the test):** *if you would ever `SUM`/`AVG`/`ORDER BY`/range-filter it, or must quote it verbatim from an authoritative record, it is an analytical fact → relational. If you only read it once you already have the entity, it is lookup-only → node property.*

---

## 4. Phase 1 — Safe, structured property writes (`ingestor/src/graph.py`)

**Task 1.1 — Replace the lossy property writer.**
Add a single property-builder that all node writes go through:
```python
def build_props(d: dict) -> str:
    """Return a safe Cypher property-map body from a dict.
    - Strings: escaped, NOT truncated for fact_* / ref keys (cap display keys only).
    - Numbers/bools: emitted unquoted.
    - None/empty: skipped.
    """
```
- Stop truncating `fact_*` and `ref` values. Keep a length cap only on `description`/`preview`.
- Centralise escaping here; remove ad-hoc `_e()` interpolation from node writes. This closes the injection exposure.

**Task 1.2 — Add a facts/handle setter.**
```python
def set_node_facts(graph, label, key_props: dict, facts: dict, ref: str | None):
    """MATCH (n:label {key_props}) SET n.fact_* = ..., n.ref = ref, n.facts_updated_at = now."""
```
- Writes lookup-only facts as flat `fact_*` props and stamps `ref` + `facts_updated_at`.
- Idempotent (safe to re-run on re-ingest).

**Task 1.3 — Populate facts + `ref` at extraction time.**
Where the ingestor already creates entity nodes (`write_extracted_nodes`, plus the financial/bill path), call `set_node_facts` with:
- the relational `ref` for that entity (you have the row id at insert time — thread it through), and
- any lookup-only facts extracted (supplier, ABN, invoice no, booking ref).
- Priority entities for the first pass: **Bill / financial entities** (`postgres/init/18_financial_entities.sql` counterparts) and **Person** (→ `personal.person`).

**Task 1.4 — Backfill.**
Write `scripts/backfill_node_refs.py` that, for existing nodes with a derivable relational match (by name/key), stamps `ref` + available `fact_*`. Additive only; never deletes existing properties. Dry-run flag; run against a DB copy first.

---

## 5. Phase 2 — Hydration endpoint (`graph-api`)

**Task 2.1** — New router `graph-api/src/routers/hydrate.py`:
- `POST /hydrate` with body `{ "refs": ["personal.bill:123", ...] }` (or `{ "node_ids": [...] }` — resolve ids→ref first).
- Parse each `ref` → `schema`, `table`, `id`. **Allowlist** `schema.table` (reuse the pattern in `dashboard/.../graph-nodes/route.ts`); reject anything not listed.
- `SELECT` the authoritative row(s) and return:
  ```json
  { "ref": "...", "node_facts": { "fact_abn": "...", ... }, "record": { ...relational row... }, "source": { "doc": "...", "received_at": "..." } }
  ```
- Include relational provenance columns (`source_doc`, `received_at`) in `source` when present — this gives "per the clinic's email on 3 Jun" for free.

**Task 2.2** — Keep it read-only and PII-safe: do **not** log `record` contents (this DB holds family medical/NDIS data). Respect existing role grants on the `personal` schema.

---

## 6. Phase 3 — Wire retrieval (`wa-agent`)

**Task 3.1** — In the retrieve/search path:
- After anchor-node discovery + typed-edge expansion, read `fact_*` straight off the node properties (no hop).
- Collect each node's `ref`, call `graph-api /hydrate` once (batched) for the analytical facts.
- Assemble a single **hard-facts block** (node facts + hydrated record + provenance) and pass it to generation.

**Task 3.2** — Generation guardrail: high-stakes fields (amounts, dates, ABNs, invoice numbers) must be rendered **from the hard-facts block verbatim**, never synthesised. Add an instruction + a cheap post-check that any number/date in the answer appears in the supplied facts.

---

## 7. Phase 4 — Surface in the customised AGE Viewer (+ dashboard mirror)

The vendored AGE Viewer is mostly compiled. Two options — do **both** the minimal viewer change and the dashboard mirror, and treat the dashboard as the maintainable long-term home:

**Task 4.1 — AGE Viewer node panel (minimal).**
In the customised viewer's node-detail rendering, add a **Facts** section that lists `fact_*` and `ref`, and a **Hydrate** button that calls `graph-api /hydrate` for the node's `ref` and renders the returned `record` + `source` inline. If patching the compiled frontend is impractical, expose the hydrate result via a small backend proxy route in `age-viewer/app-root/backend` and render it in the existing detail pane.

**Task 4.2 — Dashboard graph page (recommended primary surface).**
`dashboard/src/app/graph/page.tsx` is your own TSX and easier to evolve than the vendored viewer:
- When a node is selected, show its `fact_*` properties and `ref`.
- Add `dashboard/src/app/api/graph-hydrate/route.ts` that proxies `graph-api /hydrate` (reuse the allowlist), and render the hydrated relational record + provenance beside the node.
- This becomes the canonical "find in graph → see the hard facts" view.

---

## 8. Acceptance criteria

- [ ] `docs/graph-property-inventory.md` and `docs/graph-conventions.md` exist and match the implementation.
- [ ] Node properties for `fact_*` are no longer truncated; node writes go through `build_props` with no raw f-string interpolation (injection path removed).
- [ ] Every newly written/updated entity node with a relational counterpart has a valid, parseable `ref`; backfill has stamped existing ones (Bill + Person first).
- [ ] `POST /hydrate` returns the correct relational record for a given `ref`, allowlisted tables only, no PII in logs.
- [ ] `wa-agent` assembles node-facts + hydrated record into one hard-facts block; high-stakes fields are quoted, not generated.
- [ ] Selecting a node in the AGE Viewer **and** the dashboard graph page shows `fact_*`, `ref`, and a one-click hydrated record with provenance.
- [ ] Existing ingest (Document/Concept/Person creation, `stamp_parse`) still works — no regressions.

## 9. Guardrails

- **Additive & reversible.** No destructive property edits; never delete or rebuild existing node properties. Run migrations/backfill against a DB copy first.
- **One engine.** Everything stays in Postgres + AGE + relational schemas. Do not introduce a new database.
- **Placement discipline.** Do not move analytical facts onto nodes to "save a hop" — that's the failure mode this design avoids.
- **PII.** This repo carries real family medical/NDIS data. Do not log hydrated records; honour `personal` schema role grants; keep example/test data synthetic.
- **AGE version check.** Verify map-property and parameterisation behaviour on the installed AGE version before relying on anything beyond flat `fact_*` string/number props.
