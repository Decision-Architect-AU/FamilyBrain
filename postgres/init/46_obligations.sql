-- Interrogation Layer, Increment 1: obligation events, per-fact epistemic/conflict
-- state, and the WhatsApp conversation-session store.
--
-- Obligations are personal.event rows (event_type='obligation'), not a new table —
-- they flow through the existing channel_resolver/materialise pipeline for free.
-- starts_at is relaxed to nullable (NOT NULL since 08_schema_personal.sql) so a
-- dateless obligation — one whose only deadline is derived from a REQUIRED_FOR
-- parent at query time, never copied onto this row — naturally falls out of
-- appointment_updater.py's calendar-push polling query
-- (`starts_at >= now() - INTERVAL '1 hour'`, which SQL excludes NULL rows from
-- automatically) instead of needing a sentinel value every downstream consumer has
-- to remember to filter. See email-sync/src/appointment_updater.py's added
-- `event_type <> 'obligation'` guard for the belt-and-suspenders half of this.
ALTER TABLE personal.event ALTER COLUMN starts_at DROP NOT NULL;

ALTER TABLE personal.event
    ADD COLUMN obligation_status TEXT
        CHECK (obligation_status IN ('active', 'waiting', 'blocked', 'completed', 'cancelled')),
    -- Dedicated column, not the generic updated_at/trg_event_updated trigger — that
    -- trigger fires on ANY column change, so reusing it for staleness would mean
    -- correcting an unrelated field (e.g. owner) silently resets the staleness
    -- clock. Only obligation_status transitions should touch this.
    ADD COLUMN obligation_status_changed_at TIMESTAMPTZ,
    ADD COLUMN owner TEXT,
    ADD COLUMN waiting_on TEXT,
    -- Event-level epistemic marker (separate from the per-fact epistemic_{key}
    -- properties added to AGE graph nodes below — those are a different mechanism
    -- for a different kind of node).
    ADD COLUMN epistemic TEXT CHECK (epistemic IN ('known', 'inferred')),
    ADD COLUMN confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    -- 1:1 obligation -> target event, plain self-FK — mirrors the existing
    -- superseded_by_event_id/suspended_by_event_id idiom already used for this
    -- exact relationship shape on this table.
    ADD COLUMN required_for_event_id BIGINT REFERENCES personal.event(id);
-- 'blocked' and 'stale' are deliberately excluded from the CHECK — both are derived
-- at query time by the interrogation primitives, never stored.

CREATE INDEX idx_event_obligation_active ON personal.event (obligation_status)
    WHERE event_type = 'obligation' AND obligation_status = 'active';
CREATE INDEX idx_event_required_for ON personal.event (required_for_event_id)
    WHERE required_for_event_id IS NOT NULL;

-- DEPENDS_ON: many-to-many between obligations, kept separate from REQUIRED_FOR's
-- plain FK since dependency_chain needs recursive-CTE traversal over it. Relational,
-- not an AGE Cypher edge — event_collision_architecture.md §11 documents a
-- deliberate project-wide decision to keep event lifecycle/precedence state in
-- Postgres, not AGE ("it's ACID, dashboard-queried, and stateful, which is where it
-- belongs"); this follows that precedent.
CREATE TABLE personal.obligation_dependency (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    obligation_event_id BIGINT NOT NULL REFERENCES personal.event(id),
    depends_on_event_id BIGINT NOT NULL REFERENCES personal.event(id),
    UNIQUE (obligation_event_id, depends_on_event_id)
);
CREATE INDEX idx_obligation_dep_obligation ON personal.obligation_dependency (obligation_event_id);
CREATE INDEX idx_obligation_dep_depends_on ON personal.obligation_dependency (depends_on_event_id);

-- Never-overwrite conflict discipline for graph fact_* properties — same philosophy
-- as personal.event's suspend-with-reason pattern (42_event_suspension.sql): the
-- losing value survives and stays queryable instead of disappearing. Kept
-- relational (not an AGE CONFLICTS_WITH edge) so graph-api's interrogation
-- primitives can read it via plain SQL. Per-fact, not per-node (fact_key column) —
-- the existing fact model (ingestor/src/graph.py's set_node_facts) is already
-- per-key (fact_{k}/factsrc_{k} pairs), so a single node-level conflict marker
-- would force the whole node to read as unreliable over one disagreeing field.
CREATE TABLE personal.fact_conflict (
    id              BIGSERIAL PRIMARY KEY,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    node_ref        TEXT NOT NULL,     -- "personal.event:{id}" convention, matches existing ref/hydrate usage
    fact_key        TEXT NOT NULL,
    existing_value  TEXT NOT NULL,
    existing_source TEXT,
    new_value       TEXT NOT NULL,
    new_source      TEXT,
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX idx_fact_conflict_open ON personal.fact_conflict (node_ref) WHERE resolved_at IS NULL;

-- Conversation session store for the WhatsApp interrogation flow (Increment 3 builds
-- the actual drill-down logic; this is just the table + a real read/write helper
-- with the TTL already baked in — see wa-agent/src/wa_session.py).
CREATE TABLE personal.wa_session (
    sender            TEXT PRIMARY KEY,
    last_plan         JSONB,
    last_result_refs  JSONB,
    focus_entity      TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- TTL (30 min) is enforced at read time by wa_session.get_session(), not here —
-- no cron cleanup needed at this scale per spec.

-- Config precedent: personal.event_config (key/value), same pattern _cfg() already
-- reads in wa-agent/src/routine_context_pack.py.
INSERT INTO personal.event_config (key, value) VALUES ('OBLIGATION_STALE_DAYS', '21')
    ON CONFLICT (key) DO NOTHING;

-- Channel routing for obligations WITH an explicit date only — route to the
-- existing bills channel (same before_event:3d lead time already used for
-- payment/bill item types there), not the default immediate/family channel.
-- Dateless obligations (deriving their deadline only from a REQUIRED_FOR parent)
-- must never reach materialise() at all — channel_rule matching alone can't
-- prevent this (before_event:Nd scheduling would crash/misbehave on a NULL
-- effective_date), so the obligation-insert code path skips calling materialise()
-- whenever effective_date IS NULL. See wa-agent obligation-creation helper.
-- personal.channel_rule has no unique constraint beyond its surrogate PK, so a bare
-- ON CONFLICT DO NOTHING would never actually trigger (id is always fresh) and a
-- re-run of this migration would insert a duplicate row — guard with NOT EXISTS
-- instead.
INSERT INTO personal.channel_rule (channel_id, item_type, schedule, target_slot, priority)
    SELECT c.id, 'obligation', 'before_event:3d', 'bills', 30
    FROM personal.channel c
    WHERE c.slug = 'gcal_bills'
      AND NOT EXISTS (
          SELECT 1 FROM personal.channel_rule r
          WHERE r.channel_id = c.id AND r.item_type = 'obligation'
      );

GRANT SELECT, INSERT, UPDATE ON personal.obligation_dependency, personal.fact_conflict, personal.wa_session
    TO openclaw_curator_role, openclaw_n8n_role;
GRANT SELECT ON personal.obligation_dependency, personal.fact_conflict, personal.wa_session
    TO openclaw_readonly;
GRANT USAGE, SELECT ON SEQUENCE personal.obligation_dependency_id_seq, personal.fact_conflict_id_seq
    TO openclaw_curator_role, openclaw_n8n_role, openclaw_readonly;
