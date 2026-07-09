# Routine Context Pack — Implementation Spec

> Handoff spec for Claude Code. FamilyBrain, `personal` schema. Self-contained feature.
> **Assumes already implemented** (treat as givens, do not rebuild): participant roles
> `event_participant(event_id, asset_id, role)` with role ∈ {provider, subject, location};
> availability as ranged events on assets (date interval + confidence); confidence bands +
> `event_config`; role-branched generation gate (all-subjects-gone → suppress,
> provider-gone → generate+orphan, some-subjects-gone → narrow); collision split into
> subject-collision vs provider-collision; `has_availability` / `can_hold_role` asset flags.

---

## 1. Purpose

Give the LLM a compact, reasoning-friendly projection of a routine so it can brief, answer
"what's on," and prompt for reassignment — **without re-deriving system rules**. The pack
leads with the baseline (what normally happens) and then the **diff** (what's different in
the window ahead). Models reason well on baseline-plus-deviation and badly on raw state.

Core inversion: **do not hand the LLM the routine and make it infer what's abnormal. Hand
it the normal pattern in one line, then the deviations explicitly, cause + confidence +
consequence inline.**

---

## 2. Design principles (each maps to a build requirement)

1. **Deviation-first.** Baseline is one line; everything after is a delta against it.
   Deviations render at the top, before any day-by-day list. Token-safe: if truncated, the
   gaps survive because they lead.
2. **Roles carry dependency semantics, not just labels.** The pack states *who the event
   exists for* and *what breaks it*, so the LLM never re-derives the asymmetry. One
   dependency line per routine (see §4). This is what makes the model say "Nanna's away,
   who covers pickup?" instead of "cancel pickup."
3. **Confidence + cause inline on every deviation.** e.g. `Nanna holiday (manual, conf 65)`
   vs `Elliana pupil-free (school email, conf 40)`. Lets the LLM hedge and explain *why*.
4. **Assembled, not generated.** The pack is built by the hydration layer as a set of
   queries. The LLM only reads it. No LLM call constructs the deviations block.
5. **Tiered.** Core (header + participants + baseline + differences) is always-on. The
   day-level occurrence list is hydrated only when a query needs it (§6).
6. **Dual-horizon.** The *differences* block spans a longer horizon than the *occurrences*
   block, so a provider gap surfaces with maximum lead time (§7).

---

## 3. Pack structure (canonical layout)

```
ROUTINE  <routine_key>
  produces   <event_type> · <cadence phrase> · <time> · <location name>
  cadence    <human cadence, incl. standing suppressions e.g. "suppressed on school holidays">
  dependency exists FOR <subject|subjects> · provider is <REASSIGNABLE|FIXED> ·
             voided ONLY if ALL subjects unavailable · narrows if SOME are

PARTICIPANTS
  provider   <asset name>        (availability-bearing | fixed)
  subject    <asset name>
  subject    <asset name>
  location   <asset name>

BASELINE
  <one-line description of the normal occurrence>

UPCOMING DIFFERENCES  (next <DIFF_HORIZON> days)          ← lead with this
  <glyph> <date | range>  <DEVIATION TYPE>
       cause   <human cause> (<source>, conf <n><, interval>)
       effect  <consequence in plain words>
       status  <UNRESOLVED GAP | ok, informational | narrowed | resolved: <who>>
  ...
  ✓ all other <cadence units> — normal

OCCURRENCES  (tier-2, hydrate on demand)
  <date> <weekday>  <normal | ORPHANED | NARROWED(subjects) | SUPPRESSED(reason) | CONFLICT(id)>
  ...
```

Glyphs: `⚠` unresolved gap (provider drop, subject collision ≥ notify tier), `◐` partial /
narrowed, `✗` suppressed, `⚑` conflict, `✓` normal. Keep them stable — they're a cheap
signal the LLM learns to key on.

Worked example (Nanna holiday + Elliana pupil-free):

```
ROUTINE  school_pickup
  produces   PICKUP · school days · 3:15pm · Riverside State School
  cadence    weekdays during term (suppressed on school holidays)
  dependency exists FOR the subjects · provider is REASSIGNABLE ·
             voided ONLY if ALL subjects unavailable · narrows if SOME are

PARTICIPANTS
  provider   Nanna            (availability-bearing)
  subject    Olivia
  subject    Elliana
  location   Riverside State School

BASELINE
  Mon–Fri 3:15pm — Nanna collects Olivia + Elliana from Riverside SS

UPCOMING DIFFERENCES  (next 21 days)
  ⚠ 15–19 Apr  PROVIDER UNAVAILABLE
       cause   Nanna holiday (manual, conf 65, interval)
       effect  5 pickups orphaned — need a provider
       status  UNRESOLVED GAP · no substitute assigned
  ◐ 17 Apr     SUBJECT PARTIAL
       cause   Elliana pupil-free day (school email, conf 40)
       effect  pickup narrows to Olivia only (1 subject remains → still runs)
       status  ok, informational
  ✓ all other school days — normal
```

---

## 4. Dependency line — derivation

Emit deterministically from the routine's role structure; never free-text it:

- `exists FOR` → the subject role assets (name list, or "the subjects" if >2).
- `provider is REASSIGNABLE` if the routine permits substitution; `FIXED` if not
  (e.g. a routine where only that asset can perform it — rare; default REASSIGNABLE).
- Voiding clause is constant for multi-subject routines: *voided only if ALL subjects
  unavailable; narrows if some are*. For single-subject routines collapse to
  *voided if the subject is unavailable*.

This line is the LLM's entire briefing on the routine's failure semantics. It must match
the implemented generation gate exactly, or the LLM will narrate behaviour the system
doesn't perform.

---

## 5. Deviation taxonomy (what the assembler classifies)

For each occurrence in the differences horizon, classify by intersecting participant
availability intervals and collision status with the occurrence:

| Type | Trigger | glyph | status text | severity |
|---|---|---|---|---|
| PROVIDER UNAVAILABLE | provider has an availability gap covering the date, no substitute | ⚠ | `UNRESOLVED GAP` | high — lead item |
| PROVIDER REASSIGNED | provider gap but a substitute is assigned | ✓/◐ | `resolved: <substitute>` | info |
| SUBJECT PARTIAL | ≥1 but not all subjects unavailable | ◐ | `narrows to <remaining>` | info |
| SUPPRESSED | ALL subjects unavailable, OR location unavailable, OR standing suppression (school holiday) | ✗ | `SUPPRESSED (<reason>)` | info (logged reason) |
| SUBJECT COLLISION | subject's time double-committed (band ≥ COLLISION_FLOOR) | ⚑ | `CONFLICT #<id> (<other event>)` | per band; ≥ IMMEDIATE_NOTIFY_MIN → lead item |
| PROVIDER COLLISION | provider double-booked across routines | ⚑ | `provider clash — reassign` | medium (reassign, not subject-facing) |

Notes:
- Subject vs provider collision are **distinct rows** with distinct resolutions (subject =
  real conflict; provider = resourcing/reassign). Do not merge.
- SUPPRESSED always carries its reason (already logged by the gate) so the pack — and any
  downstream courtesy notify — is explicable.
- Availability checks are **interval-covering**, not exact-date (a 15–19 Apr holiday hits
  all five pickups).

---

## 6. Tiering

- **Tier-1 (core):** header + dependency line + participants + baseline + differences.
  Always assembled. This is what briefings and reassignment prompts consume.
- **Tier-2 (occurrences):** day-by-day list. Assembled only when the query needs
  enumeration ("what's on this week", day view). Omitted from briefings and from
  multi-routine batches by default.

Retrieve-then-hydrate: a briefing over N routines = N tier-1 diff-blocks, not N calendars.

---

## 7. Horizons (two, deliberately different)

Read from `event_config` (add keys):

```sql
INSERT INTO personal.event_config (key, value) VALUES
  ('DIFF_HORIZON_DAYS', 21),   -- differences block: long, for lead time on gaps
  ('OCC_HORIZON_DAYS', 7)      -- occurrences block: near window only
ON CONFLICT (key) DO NOTHING;
```

Rationale: a provider gap (unfilled pickup) is the highest-value output and needs the most
warning — surface it as soon as the availability event lands, weeks out. Day-level
enumeration only matters for the near window. `DIFF_HORIZON_DAYS ≥ OCC_HORIZON_DAYS` always.

---

## 8. Serialization

Provide both; the text form is default for LLM context, JSON for programmatic callers
(dashboard, reassignment flow).

- **Text** (§3 layout): compact, glyph-led, deviation-first. This is what goes into the
  LLM prompt.
- **JSON** (same content, structured): `{routine, produces, dependency, participants[],
  baseline, differences[{glyph,type,date_or_range,cause,source,confidence,interval,
  effect,status,severity}], occurrences?[]}`.

Text is derived from the JSON, not assembled twice.

---

## 9. Batching

Assembling packs for many routines (e.g. morning briefing) is a single-pass DB build:
one query for participants across routines, one for availability intervals across the
horizon, one for collision/orphan status; assemble in memory. **No per-routine LLM call.**
When the LLM then narrates a briefing, it receives all tier-1 packs in one prompt and
produces the temporal output in one pass (matches existing single-pass temporal batching).

---

## 10. Integration points

- **Morning briefing:** tier-1 packs for all active routines; LLM narrates, leading with
  ⚠ items (gaps, immediate-tier subject collisions).
- **Reassignment prompt:** on an UNRESOLVED GAP, feed that routine's tier-1 pack +
  candidate substitute providers available in the interval → LLM drafts the ask
  ("Nanna's away Mon–Fri, can you or Shannon cover the 3:15 pickup?").
- **"What's on this week":** tier-1 + tier-2 for the OCC horizon.

---

## 11. Acceptance tests

| Scenario | Expected pack behaviour |
|---|---|
| Nanna holiday 15–19 Apr | ⚠ PROVIDER UNAVAILABLE range row, 5 pickups orphaned, UNRESOLVED GAP, leads the differences block |
| Elliana pupil-free 17 Apr (Olivia not) | ◐ SUBJECT PARTIAL, narrows to Olivia, still runs, informational |
| Both kids pupil-free same day | ✗ SUPPRESSED (all subjects unavailable), reason logged |
| School holiday week | ✗ SUPPRESSED (school holiday) for each day, no gap raised |
| Nanna away + substitute assigned | resolved: <substitute>, not an unresolved gap |
| Olivia dentist overlaps holiday-care | ⚑ SUBJECT COLLISION #id; if ≥ IMMEDIATE_NOTIFY_MIN, leads block |
| Nanna double-booked across two routines | ⚑ PROVIDER COLLISION on both, reassign status, not surfaced as subject conflict |
| Gap 3 weeks out, occurrences 1 week | Gap appears in differences (21d horizon), absent from occurrences (7d) |
| Briefing over 20 routines | 20 tier-1 blocks, no tier-2, single assembly pass, no per-routine LLM call |
| Normal week, no deviations | differences block = "✓ all school days — normal" only |

---

## 12. Build order

1. `event_config` horizon keys (§7).
2. Assembler queries: participants, availability-interval-covering, collision/orphan status.
3. Deviation classifier (§5) → JSON `differences[]`.
4. JSON pack (tier-1), then tier-2 occurrences.
5. Text serializer from JSON (§3 layout, glyphs).
6. Batch path (§9) + briefing integration.
7. Reassignment prompt integration (§10).
8. Acceptance tests (§11).
