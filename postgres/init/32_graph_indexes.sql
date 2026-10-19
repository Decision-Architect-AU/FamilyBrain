-- Graph vertex indexes across personal_graph, decision_graph, property_graph
-- btree on name for O(log n) lookups; GIN on properties for containment queries

-- ── personal_graph ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_personal_concept_name
    ON personal_graph."Concept"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_personal_concept_props
    ON personal_graph."Concept"
    USING gin (properties);

CREATE INDEX IF NOT EXISTS idx_personal_event_name
    ON personal_graph."Event"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_personal_event_props
    ON personal_graph."Event"
    USING gin (properties);

CREATE INDEX IF NOT EXISTS idx_personal_person_name
    ON personal_graph."Person"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_personal_person_props
    ON personal_graph."Person"
    USING gin (properties);

CREATE INDEX IF NOT EXISTS idx_personal_document_name
    ON personal_graph."Document"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_personal_document_props
    ON personal_graph."Document"
    USING gin (properties);

-- ── decision_graph ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_decision_concept_name
    ON decision_graph."Concept"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

-- (GIN index idx_decision_concept_props already exists from earlier migration)
CREATE INDEX IF NOT EXISTS idx_decision_concept_props
    ON decision_graph."Concept"
    USING gin (properties);

-- ── property_graph ─────────────────────────────────────────────────────────────
--
-- idx_property_concept_props went missing on the live database despite this
-- IF NOT EXISTS statement being in place — confirmed live: AGE label tables
-- (e.g. property_graph."Concept") are created lazily on first Cypher write,
-- not by create_graph() in 03_schemas.sql, so a CREATE INDEX against a label
-- whose table doesn't exist yet fails on just that statement without
-- stopping the rest of the script. property_graph presumably had zero
-- Concept nodes the one time this file last ran, so this statement silently
-- did nothing while idx_property_concept_name above (and every index on the
-- other two graphs) succeeded. The gap wasn't caught until task_dedup()
-- timed out: without this index, AGE's Cypher planner falls back to a full
-- Seq Scan for every `MATCH (c:Concept {name: ...})` — confirmed live,
-- ~700ms per call against 20k+ rows vs ~0.4ms with the index (see
-- wa-agent/src/maintenance.py's DEDUP_BATCH_SIZE comment for the full
-- incident). Re-applied live via CREATE INDEX CONCURRENTLY. If this ever
-- needs re-verifying: `SELECT indexname FROM pg_indexes WHERE tablename =
-- 'Concept'` should list idx_<graph>_concept_props for all three graphs —
-- if property_graph's is missing again (e.g. after restoring an empty
-- volume before any property_graph ingestion has run), re-run just that
-- CREATE INDEX statement manually once the table exists.

CREATE INDEX IF NOT EXISTS idx_property_concept_name
    ON property_graph."Concept"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_property_concept_props
    ON property_graph."Concept"
    USING gin (properties);
