-- Human-in-the-loop review queue for uncertain financial email senders.
-- One row per domain — deduped automatically, collects sample subjects.
-- Actioned via the dashboard /review page.

CREATE TABLE IF NOT EXISTS personal.review_queue (
    id               SERIAL PRIMARY KEY,
    domain           TEXT NOT NULL,
    from_address     TEXT NOT NULL,           -- most recent sender address
    sample_subjects  TEXT[] NOT NULL DEFAULT '{}',  -- up to 3 example subjects
    email_count      INTEGER NOT NULL DEFAULT 1,
    suggested_entity TEXT,
    confidence       TEXT,
    reason           TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'junked'
    resolved_entity  TEXT,
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT review_queue_domain_key UNIQUE (domain)
);

CREATE INDEX IF NOT EXISTS review_queue_status_idx ON personal.review_queue(status);

GRANT SELECT, INSERT, UPDATE ON personal.review_queue TO dashboard_ro;
GRANT USAGE, SELECT ON SEQUENCE personal.review_queue_id_seq TO dashboard_ro;
