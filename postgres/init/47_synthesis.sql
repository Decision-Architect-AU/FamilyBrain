-- Interrogation Layer, Increment 3a: synthesis response contract support.
--
-- last_answer_summary: the first line of a validated synthesis ANSWER section (or
-- the fallback header on degradation), written alongside last_plan/last_result_refs
-- on every synthesis response so a follow-up message ("what about the second one")
-- has a one-line recap of what was just said, not just the raw refs. Part of the
-- same upsert as the rest of the row (wa_session.py's save_session()) so it can't
-- fall out of sync with the 30-minute TTL the other columns already share.
ALTER TABLE personal.wa_session ADD COLUMN IF NOT EXISTS last_answer_summary TEXT;

-- One row per synthesis validation failure in wa-agent's (future, Increment 3b)
-- plan-execution reply path: the 14B model's delimiter-structured output violated
-- the grounding/section/length contract on its first attempt. Modeled directly on
-- config.query_flags (same write-before-retry / update-in-place-after lifecycle,
-- same "operational metadata about the system's own answering, not family data"
-- schema placement) — see that table's comment for the full rationale, which
-- applies unchanged here.
CREATE TABLE IF NOT EXISTS config.synthesis_failures (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sender              TEXT,
    message             TEXT NOT NULL,
    plan                JSONB NOT NULL,
    first_violations    JSONB NOT NULL,
    first_model_output  TEXT NOT NULL,
    retry_violations    JSONB,                    -- null until the in-request retry runs
    retry_model_output  TEXT,                      -- null until the in-request retry runs
    outcome             TEXT
                        CHECK (outcome IS NULL OR outcome IN ('recovered_on_retry', 'fell_back')),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_synthesis_failures_created_at ON config.synthesis_failures (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_synthesis_failures_outcome ON config.synthesis_failures (outcome) WHERE outcome IS NULL;

GRANT SELECT, INSERT, UPDATE ON config.synthesis_failures TO openclaw_curator_role, openclaw_n8n_role;
GRANT USAGE ON SEQUENCE config.synthesis_failures_id_seq TO openclaw_curator_role, openclaw_n8n_role;
GRANT SELECT ON config.synthesis_failures TO dashboard_ro;
