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

CREATE INDEX IF NOT EXISTS idx_property_concept_name
    ON property_graph."Concept"
    USING btree (agtype_to_text((properties -> '"name"'::agtype)));

CREATE INDEX IF NOT EXISTS idx_property_concept_props
    ON property_graph."Concept"
    USING gin (properties);
