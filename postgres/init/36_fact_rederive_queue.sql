-- Fact re-derivation queue — when a graph edge is suppressed (confidence -> 0),
-- every fact_* whose factsrc_* cites that edge's source node must be re-derived
-- excluding the now-zeroed source, or deleted if no source remains.
-- Populated by zero_edge() in ingestor/src/graph.py; drained by
-- task_rederive_facts() in wa-agent/src/maintenance.py.

SET search_path = personal, public;

CREATE TABLE IF NOT EXISTS personal.fact_rederive_queue (
    id           BIGSERIAL PRIMARY KEY,
    node_ref     TEXT NOT NULL,       -- e.g. 'personal.asset:2' — the node carrying the fact
    fact_name    TEXT NOT NULL,       -- e.g. 'current_ot' (without fact_ prefix)
    source_ref   TEXT NOT NULL,       -- the suppressed source ref to remove from factsrc_<fact_name>
    enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason       TEXT
);

CREATE INDEX IF NOT EXISTS idx_rederive_queue_enqueued ON personal.fact_rederive_queue (enqueued_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON personal.fact_rederive_queue TO curator;
GRANT USAGE, SELECT ON SEQUENCE personal.fact_rederive_queue_id_seq TO curator;
