-- Human-in-the-loop review queue for uncertain financial email classifications.
-- Populated by financial_processor when confidence is low.
-- Actioned via the dashboard /review page.

CREATE TABLE IF NOT EXISTS personal.review_queue (
    id            SERIAL PRIMARY KEY,
    email_msg_id  INTEGER REFERENCES personal.email_message(id) ON DELETE CASCADE,
    from_address  TEXT NOT NULL,
    subject       TEXT NOT NULL,
    received_at   TIMESTAMPTZ,
    suggested_entity TEXT,        -- what the processor guessed (may be null)
    confidence    TEXT,           -- 'low', 'medium' — why it was queued
    reason        TEXT,           -- human-readable: 'unknown domain', 'llm uncertain', etc.
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'junked'
    resolved_entity TEXT,         -- set on approve
    resolved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS review_queue_status_idx ON personal.review_queue(status);
CREATE INDEX IF NOT EXISTS review_queue_email_idx  ON personal.review_queue(email_msg_id);
