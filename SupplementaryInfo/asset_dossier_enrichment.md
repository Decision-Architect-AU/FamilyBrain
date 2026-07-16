# Asset Dossier & Enrichment — Implementation Spec

> Handoff spec for Claude Code. FamilyBrain, `personal` schema + AGE graph.
> **Assumes already implemented** (givens, do not rebuild): participant roles
> (`event_participant`), Routine Context Pack (tier-1/tier-2, glyphs, horizons),
> confidence bands + `event_config`, availability model, subject/provider collision split,
> nightly maintenance engine with fact-writing (`fact_*` node properties, 500-char display
> cap discipline), review queue.

---

## 1. Purpose

Give each asset a **dossier**: a human-facing projection of its graph neighbourhood —
the same pattern as the Routine Context Pack, different consumer. Click an asset in the
dashboard → see everything the graph knows about it, grouped by relationship type, with
per-item suppression ("this note is irrelevant") that behaves correctly downstream.

Second half: point the existing maintenance engine at assets, enriching **named facts**
(not prose) with provenance, including practitioner extraction from provider invoices.

---

## 2. Dossier = generic neighbourhood renderer (the "dynamic" answer)

Do **not** design per-asset-type pages. Build one renderer:

1. Query the asset's 1-hop neighbourhood in AGE, grouped by edge type.
2. Render each group as a section using a per-edge-type presenter (icon, item template,
   sort). Unknown edge types get a default presenter (label + date + snippet).
3. Merge in relational attachments: events via `event_participant` (this asset as
   subject / provider), routines it participates in, and the asset's own `fact_*`
   properties as a facts panel.

Sections (initial presenter set):

| Section | Source | Notes |
|---|---|---|
| Facts | `fact_*` properties on the node | k/v panel; each fact shows provenance (§5) and freshness (`facts_updated_at`) |
| Summary line | `fact_summary` (§6) | one-liner, rendered above the panel |
| Emails / documents | MENTIONS / EXTRACTED_FROM edges | newest first; snippet = display-capped preview |
| Notes | NOTE edges | each with suppress control (§4) |
| Events | `event_participant` rows | reuse Context Pack tier-2 renderer: date, glyph, status. For a provider asset, show events it provides across routines |
| Routines | participant bindings | name, role of this asset, cadence line |
| People / orgs | WORKS_AT / PROVIDES / related-asset edges | e.g. Sarah Chen → Centre of Movement |

New edge types added later appear automatically via the default presenter — the dashboard
never needs to know what an asset "is", only how to render a neighbourhood.

API: `GET /api/assets/:id/dossier` → JSON `{asset, facts[], sections[{edge_type,
items[]}], events[], routines[]}`. Single round trip; sections independently collapsible.

---

## 3. Edge confidence (prerequisite for suppression)

Edges carry `confidence INT` (0–100) like events, stored as an edge property in AGE.
Backfill existing edges at their source prior (email-derived edges 40, manual 65,
system-structural edges e.g. participant bindings 90). Retrieval, enrichment, and the
dossier read edges with `confidence > 0` by default.

---

## 4. Suppression = confidence-zero with reason (detach, not destroy)

"Delete this note" sets the edge to zero — it does **not** remove the edge or the node:

```
SET edge.confidence   = 0,
    edge.zeroed_by    = 'user',          -- 'user' | 'system'
    edge.zeroed_at    = now(),
    edge.zero_reason  = 'marked irrelevant from dossier'
```

Consequences (all mandatory):

1. **Invisible everywhere by default.** Zero is below every band → retrieval, hierarchy
   walk, enrichment context assembly, and the dossier all exclude it with the existing
   `confidence > 0` predicate. No special suppression checks anywhere.
2. **Durable against re-ingestion.** Before writing a MENTIONS/NOTE edge, ingestion checks
   for an existing edge (any confidence) between the same pair. If one exists with
   `zeroed_by='user'`, **do not re-score or re-create** — user suppression outranks the
   system's opinion permanently. (System-zeroed edges may be re-scored.)
3. **Node survives** if any other edge references it; a note linked only to this asset
   becomes orphaned but is retained (cheap, and preserves audit).
4. **Triggers re-derivation** of any fact/summary that cited the suppressed source (§5).
5. **Undo**: dossier offers "restore" on suppressed items (toggle a "show suppressed"
   view) → restores prior confidence from `zero_prev_confidence` (store it at zeroing).

UI: suppress control on every email/note/document item in the dossier; confirmation
inline (no modal); item moves to the suppressed view immediately.

---

## 5. Fact provenance & re-derivation (the part that makes suppression honest)

Every `fact_*` written by enrichment must carry sources. Alongside each fact, write
`factsrc_<name>` = JSON array of source refs (message ids / node refs), same discipline
as `confidence_reason` on events:

```
fact_current_ot      = "Sarah Chen"
factsrc_current_ot   = ["gmail:1852ab...", "gmail:1901cd..."]
```

**Re-derivation queue.** When an edge is zeroed, enqueue `(asset_id, fact_name)` for every
fact whose `factsrc_*` references the suppressed source. The nightly maintenance engine
processes the queue first: re-run the fact's derivation **excluding zeroed sources**;
if no supporting sources remain, delete the fact (and its factsrc). A fact must never
outlive its evidence — otherwise suppression is a lie: the visible link disappears but
its ghost persists in the summary.

Facts written without provenance are a build error: the enrichment path must refuse to
write a `fact_*` without its `factsrc_*`.

---

## 6. Asset summary enrichment (maintenance engine, new target)

Nightly job per active asset (same cadence/infra as existing fact-writing):

1. Assemble context: non-zero edges (recent emails/notes/invoices), current facts,
   Context Pack tier-1 for routines this asset participates in.
2. Derive/refresh **named facts** — structured, individually sourced, individually
   re-derivable. Seed set for a person asset:
   `fact_current_ot`, `fact_current_physio`, `fact_current_speech`,
   `fact_last_invoice_date`, `fact_invoice_ytd`, `fact_visit_cadence_<service>`,
   `fact_next_appointment`.
3. Write one `fact_summary` one-liner (LLM, ≤200 chars) derived **from the facts, not
   from raw documents** — so the summary can never assert something no fact supports.
   `factsrc_summary` = the fact names it drew on.

**No prose blobs.** A paragraph summary goes stale invisibly and can't be partially
updated or partially suppressed. Facts are the unit of enrichment; the one-liner is
presentation.

Skip-if-fresh: reuse the existing `facts_updated_at` freshness check; a zeroed-edge
re-derivation (§5) bypasses freshness.

---

## 7. Invoice practitioner extraction (entity resolution, gated)

For invoices from provider orgs (e.g. Centre of Movement) the **line items**, not the
org, carry the signal: which service (OT vs physio) and which practitioner.

Pipeline (extends existing financial processing):

1. LLM extracts per line item: `service_type`, `practitioner_name`, `date`, `amount`.
2. **Service disambiguation keys on the line item**, never the org — one org can feed
   two routines (OT routine vs physio routine) correctly.
3. **Practitioner resolution** (the fuzzy-match risk, same treatment as binding):
   - exact/alias match to an existing person asset → link, confidence per match quality
   - strong fuzzy ("S. Chen" vs "Sarah Chen", same org + service) → link at reduced
     confidence, log in decision trail
   - no match ≥ threshold → **create** person asset (`Sarah Chen`, role tag `OT`,
     WORKS_AT → org) only at high extraction confidence; else → review queue.
     Never silently fork ("S. Chen" node beside "Sarah Chen") or merge distinct people.
4. Write edges: practitioner PROVIDES → the matching routine; invoice EXTRACTED_FROM
   linkage as usual.
5. Update facts on the subject asset: `fact_current_<service>` = practitioner (with
   factsrc = the invoice), recency-weighted — the most recent invoice's practitioner
   wins ties, but a single locum appearance should not displace a stable practitioner:
   require 2+ occurrences or manual confirm to flip an established `fact_current_*`
   (flip attempts below that → review queue).

---

## 8. Acceptance tests

| Scenario | Expected |
|---|---|
| Open dossier for Elliana | Facts panel + summary line, emails newest-first, notes, events via participant rows (tier-2 renderer), routines with her role shown |
| Open dossier for Nanna | Provider view: events she provides across routines, incl. orphaned/glyph states |
| Unknown new edge type added later | Renders via default presenter, no dashboard change |
| Suppress a note | Edge → confidence 0, zeroed_by=user; vanishes from dossier/retrieval; node retained |
| Same email re-ingested after suppression | No edge re-created, no re-score (user zero is permanent) |
| Suppress source of `fact_current_ot` | Fact re-derived from remaining sources; if none, fact deleted |
| Fact written without factsrc | Build/test failure |
| CoM invoice, OT line + physio line | Two service resolutions, two routine links; org alone never decides service |
| Invoice shows "S. Chen", asset "Sarah Chen" exists | Fuzzy-link at reduced confidence, decision logged; no new node |
| Unknown practitioner, low extraction confidence | Review queue; no node created |
| Locum appears once | `fact_current_ot` unchanged; flip attempt queued |
| Restore a suppressed note | Prior confidence restored; reappears everywhere |

---

## 9. Build order

1. Edge confidence backfill + `confidence > 0` predicates in retrieval/enrichment/dossier.
2. Zeroing semantics (§4) incl. re-ingestion guard + restore.
3. Fact provenance (`factsrc_*`) + write-refusal without it (§5).
4. Re-derivation queue + maintenance-engine hook.
5. Dossier API + generic renderer with presenter set (§2).
6. Asset summary enrichment target (§6).
7. Invoice practitioner extraction + resolution gates (§7).
8. Acceptance tests (§8).

Ship note: dossier (5) can ship after (1)–(2) only; but do not ship suppression without
(3)–(4) — a delete that doesn't propagate teaches users to distrust the summaries, which
is worse than no delete.
