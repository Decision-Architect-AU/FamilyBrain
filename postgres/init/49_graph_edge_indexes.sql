-- start_id/end_id btree indexes on AGE edge label tables that were missing
-- them — personal_graph/decision_graph/property_graph.
--
-- Investigated live this session while fixing task_dedup()'s timeout
-- (wa-agent/src/maintenance.py): only 4 of personal_graph's 12 edge labels
-- (ASSERTS, FROM, LINKED_TO, MENTIONS) had start_id/end_id indexes;
-- decision_graph had 2 of 5 (ASSERTS, MENTIONS); property_graph had ZERO of
-- its 5. Every unlabeled/undirected Cypher edge pattern — e.g. `(dup)-[r]->
-- (b)` or `(dup)--()`, both used by task_dedup()'s redirect/orphan-delete
-- queries — makes AGE scan every edge label table in the graph (see
-- postgres/README.md's AGE usage note), so a missing index on any one of
-- them means that table gets a full sequential scan on every such call.
-- This mattered far more than table size alone might suggest: several of
-- these tables have grown to hundreds of thousands of rows (personal_graph
-- SIMILAR_TO: 884,567 live rows) while never once being ANALYZEd (confirmed
-- live: last_analyze/last_autoanalyze both NULL), so even where an index
-- already existed (e.g. idx_mentions_start) the planner's stale ~14-166 row
-- estimate made it prefer a "cheap-looking" Seq Scan over the index anyway.
-- Run ANALYZE on these tables after applying this file if that hasn't
-- already been done (see postgres/README.md's Maintenance task throttling
-- section).
--
-- Applied live via CREATE INDEX CONCURRENTLY (not the plain form below) to
-- avoid blocking live ingest/linker writes to these tables while building.

-- ── personal_graph ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_aliasof_start      ON personal_graph."ALIAS_OF"      USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_aliasof_end        ON personal_graph."ALIAS_OF"      USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_authoredby_start   ON personal_graph."AUTHORED_BY"   USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_authoredby_end     ON personal_graph."AUTHORED_BY"   USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_fromframework_start ON personal_graph."FROM_FRAMEWORK" USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_fromframework_end  ON personal_graph."FROM_FRAMEWORK" USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_hasasset_start     ON personal_graph."HAS_ASSET"     USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_hasasset_end       ON personal_graph."HAS_ASSET"     USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_partof_start       ON personal_graph."PART_OF"       USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_partof_end         ON personal_graph."PART_OF"       USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_relatedto_start    ON personal_graph."RELATED_TO"    USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_relatedto_end      ON personal_graph."RELATED_TO"    USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_relatesto_start    ON personal_graph."RELATES_TO"    USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_relatesto_end      ON personal_graph."RELATES_TO"    USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_similarto_start    ON personal_graph."SIMILAR_TO"    USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_similarto_end      ON personal_graph."SIMILAR_TO"    USING btree (end_id);

-- ── decision_graph ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_decision_aliasof_start   ON decision_graph."ALIAS_OF"   USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_decision_aliasof_end     ON decision_graph."ALIAS_OF"   USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_decision_relatesto_start ON decision_graph."RELATES_TO" USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_decision_relatesto_end   ON decision_graph."RELATES_TO" USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_decision_similarto_start ON decision_graph."SIMILAR_TO" USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_decision_similarto_end   ON decision_graph."SIMILAR_TO" USING btree (end_id);

-- ── property_graph ─────────────────────────────────────────────────────────────
-- (had zero edge indexes of any kind before this file)

CREATE INDEX IF NOT EXISTS idx_property_aliasof_start   ON property_graph."ALIAS_OF"   USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_property_aliasof_end     ON property_graph."ALIAS_OF"   USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_property_asserts_start   ON property_graph."ASSERTS"    USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_property_asserts_end     ON property_graph."ASSERTS"    USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_property_mentions_start  ON property_graph."MENTIONS"   USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_property_mentions_end    ON property_graph."MENTIONS"   USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_property_relatesto_start ON property_graph."RELATES_TO" USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_property_relatesto_end   ON property_graph."RELATES_TO" USING btree (end_id);
CREATE INDEX IF NOT EXISTS idx_property_similarto_start ON property_graph."SIMILAR_TO" USING btree (start_id);
CREATE INDEX IF NOT EXISTS idx_property_similarto_end   ON property_graph."SIMILAR_TO" USING btree (end_id);
