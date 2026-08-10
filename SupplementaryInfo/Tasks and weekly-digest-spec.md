# Family Brain — Scheduled Task Engine & Weekly Digest

**Spec version:** 1.0
**Status:** Draft for Claude Code handoff
**Depends on:** WhatsApp fallback/self-healing feature (spec'd separately in conversation; delimiter contract + `query_flags` table)

---

## Problem statement

The household needs a proactive weekly briefing over WhatsApp: each family member's routine for the week ahead, anything **unusual** about the week called out explicitly, and cross-domain deadlines (e.g. a hotel free-cancellation window closing). Today the knowledge core holds all of this — routines, holidays, TRIGGERED reminders, provider facts — but nothing assembles and sends it on a schedule.

Additionally, there is no general scheduled-task capability. This digest is task #1; the engine must generalise so future scheduled outputs are a handler + a table row, not new infrastructure.

**A critical prerequisite fix:** the current collision-awareness behaviour destroys exactly the information the digest needs. Example — routine event *"Grandparent pickup — Child1 & Child2"* (provider: Grandparent1) collides with holiday event *"Grandparents away"* (same person, overlapping dates). The system detects this correctly, but **removes the routine event**. Removal is lossy: a deleted event cannot tell the digest *"note: no scheduled pickup Wednesday — grandparents still on holidays."* The exception must survive detection.

---

## Goals

1. A reusable scheduled-task engine: n8n heartbeat → FastAPI dispatch → task handlers → WhatsApp send → run log. Adding task #2 later = one handler function + one DB row.
2. A weekly digest task producing per-person routine summaries, an explicit exceptions section, and an upcoming-deadlines section, sent to a configured WhatsApp number on a configurable cron.
3. Collision resolution changed from **delete** to **suspend-with-reason**, preserving exceptions as first-class, queryable data.
4. Dashboard page for scheduled tasks: list, enable/disable, cron edit, run-now, and per-run output history.

## Non-goals

- **No new anomaly-inference ML/LLM layer.** Exceptions come from the existing collision detector's output (now persisted instead of deleted). The LLM's job is phrasing, not discovery.
- **No daily digest, per-person digests, or reply-to-digest interactivity** in v1. Deferred until the weekly digest proves its content quality (consistent with the earlier decision to defer proactive digest features).
- **No new graph edge types** (e.g. `RESPONSIBLE_FOR`) in v1 — see Open Questions; Claude Code verifies whether existing data makes this unnecessary before anyone designs new structure.
- **No timezone configurability.** Everything is AEST/Brisbane local, consistent with `effective_date` handling.

---

## Instruction to Claude Code: verify data, don't assume structure

This spec deliberately does **not** assert column names, event title conventions, or how events are attributed to family members. Before implementing, inspect the live database and answer for yourself:

1. **Person attribution:** How are `personal.event` rows / `:Event` nodes tied to a specific family member today? (Property, title convention, `CHILD1_NAMES`/`CHILD2_NAMES`-style matching, `Person` node edge, calendar routing tag?) Whatever the mechanism is, the digest groups by it — build on the real mechanism, not an idealised one.
2. **Collision detector location and behaviour:** Find the code path that currently detects and removes colliding routine events. Confirm exactly what "removes" means today (row delete? gcal delete? status change?) before changing it.
3. **Provider linkage:** For routine events like the pickup, confirm where the provider (e.g. "Grandparent1") lives — `fact_provider`, title text, or a `Person` edge — since the suspension reason and digest phrasing both reference it.
4. **Holiday person coverage:** Confirm holiday events name the people who are away in a machine-usable place (title, `fact_*`, edge), since the collision match keys on person overlap.

Record findings as comments in the implementation and flag any of the four that turn out to be free-text-only, because that limits collision-match reliability and should surface in the Phase 1 review rather than silently degrade.

---

## Requirements

### P0-1 — Collision resolution: suspend, don't delete

When the collision detector matches a routine event against an overriding event (holiday/unavailability of the routine's provider):

- The routine event row/node is **not deleted**. It gains a suspension state, e.g. `status = 'suspended'` (or equivalent field per existing schema conventions), plus:
  - `suspended_reason` — human-readable, e.g. `"Provider away: Grandparents on holidays"`
  - `suspended_by_event` — reference to the overriding event (reuse the existing `ref`/event_key convention; if an edge is more natural in AGE, a `SUSPENDED_BY` edge is acceptable — Claude Code decides based on how `RESOLVES` is implemented, as it is the closest existing analogue)
- The outbound appointment updater treats suspended events as **route-to-Tentative** (see P0-1b): removed from their natural calendar, written to the Tentative calendar with the reason in the description. From the family's main calendars the end state is identical to today's delete; the event remains visible in one place.
- Auto-reinstatement: when the overriding event ends (its date range passes) and the routine's future occurrences no longer overlap, future occurrences are active again. Verify how recurrence is currently materialised (individual rows per occurrence vs. recurring master) and suspend at the occurrence level if occurrences are individual rows.

**Acceptance criteria**
- [ ] Given a routine event whose provider has an overlapping holiday event, when the collision sweep runs, then the routine event has suspended status, a populated reason, and a link to the holiday event — and no row is deleted.
- [ ] Given a suspended event, when the appointment updater runs, then no entry exists for it on its natural calendar, and one entry exists on the Tentative calendar carrying the suspension reason.
- [ ] Given the holiday has passed, when the sweep next runs, then subsequent occurrences of the routine are active, written to their natural calendar, and removed from Tentative.
- [ ] Existing exclusions (birthdays, anniversaries, >30-day horizon) behave exactly as before.

### P0-1b — Tentative calendar (quarantine tier)

A new outbound calendar target, "Tentative", added via the existing symmetric channel registry (`personal.channel` + `personal.channel_rule`) — a channel row plus routing rules, consistent with the design principle that new destinations are a connector + channel row, no pipeline changes.

Two feeds route to Tentative:

1. **Suspended events** (from P0-1) — visible parking for exists-but-off items, with the suspension reason in the event description.
2. **Events extracted from unverified senders** — extending the senders-hub trust model to calendar writes. Extraction from a sender not yet verified/trusted still creates the `:Event` node, still flows through enrichment and the graph as normal, but the outbound router targets Tentative instead of the event's natural calendar until the sender is verified or the event is individually promoted. This replaces the current all-or-nothing outcome for unknown senders (write to a real calendar, or sit invisibly in a review queue) with a visible middle state.

**Promotion / demotion is a move, never a copy.** State transitions (sender verified in senders hub; suspension lifted; manual promote/demote from dashboard) delete the Tentative entry and write to the natural target (or vice versa) through `calendar_sync_map`, so an event never exists on two calendars simultaneously. Claude Code: verify how the sync map keys source→target today and extend rather than parallel it.

**Digest interaction (explicit decoupling):** the weekly digest reads suspension state and event status from the database, never from calendar placement. Unverified-sender events on Tentative do **not** appear in the per-person routine (unconfirmed data). Suspended routine events **do** appear — as exception notes, per P0-3. The two features share the Tentative calendar but must not couple through it.

**Acceptance criteria**
- [ ] Tentative exists as a channel row; routing to it required zero changes to the updater's core write logic beyond target resolution.
- [ ] An event ingested from an unverified sender appears on Tentative and no other calendar, and appears in the graph/enrichment pipeline identically to a trusted-sender event.
- [ ] Verifying that sender in the senders hub moves the event to its natural calendar within one updater cycle, with no duplicate window observable in `calendar_sync_map`.
- [ ] Suspension lift (P0-1 reinstatement) removes the Tentative entry in the same cycle it writes the natural-calendar entry.
- [ ] A digest run during a week containing Tentative unverified-sender events includes none of them in any person's routine.

### P0-2 — Scheduled task engine

Two tables (names per existing schema conventions):

- `scheduled_tasks`: id, name, task_type, cron_expr, enabled, target (WhatsApp number/channel ref — reuse `personal.channel` registry if it fits, since channels are symmetric by design), config JSONB, last_run_at, next_run_at
- `scheduled_task_runs`: id, task_id, started_at, finished_at, status (success/error), output_text (full message as sent), error_detail

Dispatch model: n8n hourly (or finer) heartbeat → FastAPI endpoint → engine selects rows where `next_run_at <= now()` and enabled → routes on task_type to a registered handler → sends output via the existing WhatsApp send path → logs run → computes and writes next_run_at. This mirrors the `next_update_at` materialised-poll pattern already used by the appointment updater: **no scheduling logic at query time, one indexed poll.**

**Acceptance criteria**
- [ ] A task with a cron of Sunday 17:00 fires once in that window and not again until the following week; `next_run_at` is always populated after a run.
- [ ] A handler exception writes a run row with status=error and error_detail; the engine continues to other due tasks.
- [ ] Disabling a task in the dashboard prevents execution without deleting history.
- [ ] Registering a second dummy task_type requires only a handler function and a table row.

### P0-3 — Weekly digest handler

Window: coming Monday 00:00 → Sunday 23:59 local (configurable offset in task config).

Gather (all from existing data — no new scan logic):
1. **Active events** in window, grouped by family member (per the attribution mechanism found in the data-verification step), including `fact_provider` / `fact_location` / `fact_notes` where present.
2. **Suspended events** in window (from P0-1) with their reasons — this is the "noting anything unusual" section, deterministically sourced.
3. **Deadline-type events** in window+lookahead (config, default 14 days): TRIGGERED reminder events and any event whose type/facts denote an expiry or cancellation window (e.g. the a hotel free-cancellation reminder — see P1-1 for how that event comes to exist).

Synthesis: single call to the deep-reasoning model (32B) with the gathered structured data, producing the WhatsApp message in the established style:

> **Child1** — school this week, music lesson Tuesday, dancing Wednesday.
> Note: no scheduled pickup Wednesday — grandparents still on holidays.
>
> **Child2** — school this week, OT Monday, physio Wednesday. Scheduled call with specialist.
>
> **Parent1** — specialist appointment Monday.
>
> **Extra notes:** hotel free-cancellation ends this week.

Prompt rules: the model may only rephrase and organise supplied facts — never add events, never drop a suspended-event note, never invent providers. If a section is empty, omit it rather than padding.

**Acceptance criteria**
- [ ] Digest groups by person and covers every active event in the window; spot-check against a direct DB query returns zero missed events.
- [ ] Every suspended event in the window appears as an explicit note attached to the relevant person.
- [ ] Deadline events within lookahead appear under extra notes even when their date is beyond the current week.
- [ ] Message sends via WhatsApp to the configured target and full text is stored in the run row.
- [ ] Runtime is untimed-path async (engine-triggered, nobody waiting) — no interaction with the wa-agent fast path.

### P0-4 — Dashboard: Scheduled Tasks page

- List view: name, schedule (human-readable cron), enabled toggle, last run status + timestamp, next run.
- Detail view: run history with full output_text per run (the "what did it actually say last Sunday" check), error details on failures.
- Run-now button (calls the same dispatch path, marked as manual in the run row).
- Follows existing dashboard stack/conventions (Next.js, existing auth/roles — `dashboard_ro` can view, writes go through the standard write role).

**Acceptance criteria**
- [ ] Toggling enabled off, then triggering the heartbeat, produces no run.
- [ ] Run-now produces a run row and a WhatsApp message within one engine cycle.
- [ ] A failed run is visibly distinguishable in the list without opening detail.

### P1-1 — Cancellation-deadline TRIGGERED rule

Extend the existing proactive-rule table (same shape as "insurance renewal → reminder 6 weeks before"): when extraction detects a booking with a free-cancellation or refund deadline, create a TRIGGERED reminder event N days before the deadline (default 3, per-rule config). This makes such deadlines appear on the calendar **and** in the digest with zero digest-specific code.

**Acceptance criteria**
- [ ] Ingesting a booking email containing a free-cancellation date produces a TRIGGERED reminder event linked to the booking, and it surfaces in the next digest's extra notes.

### P2 — Future considerations (design-compatible, not built)

- `RESPONSIBLE_FOR` edge (Person → recurring Event) making provider linkage structural rather than fact/text-based — only if the data-verification step shows provider linkage is unreliable in practice.
- Per-person digest variants to individual WhatsApp numbers (target field already supports it; only handler config work later).
- Digest replies feeding back into the wa-agent query path ("what time is physio?").

---

## Delivery phases

**Phase 1 — Suspend-not-delete (ship alone first).** Highest value independent of the digest: stops information destruction immediately and is the digest's data source. Includes the four data-verification checks.

**Phase 1b — Tentative calendar** (P0-1b). Suspended-event routing lands with Phase 1 since the updater change is shared; the unverified-sender feed can follow within the phase — it touches sender-trust resolution but no new pipeline.

**Phase 2 — Task engine + dashboard page**, proven with a trivial heartbeat-echo task before any digest logic exists.

**Phase 3 — Digest handler + prompt**, iterated against real data with run-now until output quality is right, then enable the weekly cron.

**Phase 4 — P1-1 cancellation rule.**

---

## Open questions

*For Claude Code to resolve against the live system (non-blocking, resolve during Phase 1):*
1. The four data-verification questions above (person attribution, collision code path, provider linkage, holiday person coverage).
2. Whether recurring routines are stored as materialised occurrences or a recurrence master — determines suspension granularity.
3. Whether `personal.channel` cleanly models the digest's outbound WhatsApp target or a direct number in task config is simpler for v1.
4. Where sender trust state is authoritatively stored (senders hub tables vs. `financial_domain` vs. filter rules) — the unverified-sender routing test in P0-1b must key off the real source of truth.
5. Whether `calendar_sync_map` supports retargeting an existing mapping or requires delete + recreate for promotion/demotion moves.

*For the project owner:*
1. *(Blocking Phase 3)* Digest send time — proposed default Sunday 17:00 local.
2. *(Blocking Phase 3)* Which WhatsApp number receives v1 (single shared target assumed).
3. *(Blocking Phase 3)* Deadline lookahead window — proposed default 14 days.
4. *(Blocking Phase 1b unverified-sender feed only)* Default trust posture: should **all** senders not explicitly verified route their events to Tentative (strict), or only senders never seen before / flagged (lenient)? Strict is safer but will move some currently-working senders' events off the main calendars until verified once each.
