-- WhatsApp query fallback & self-healing (family-brain-whatsapp-query-fallback-spec.md).
-- One row per fallback activation in wa-agent's /query path: the live 14B model
-- couldn't answer confidently from structured retrieval and emitted the
-- INSUFFICIENT_CONTEXT signal instead of guessing. The row is written before the
-- in-request retry runs (so a crash mid-retry still leaves needs_review = true)
-- and updated in place with the retry outcome — never a second row per activation.
--
-- Lives in `config`, not `personal` — this is operational self-healing metadata
-- about the system's own answering, not family data, and the dashboard's
-- read-only role already has USAGE on `config` (see config.data_expectation).
CREATE TABLE IF NOT EXISTS config.query_flags (
    id                       BIGSERIAL PRIMARY KEY,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    source                   TEXT NOT NULL,           -- WhatsApp thread/sender ref the query came from
    original_query_text      TEXT NOT NULL,
    classified_targets       TEXT[] NOT NULL DEFAULT '{}',  -- graphs the classifier routed to, e.g. {personal_graph}
    extracted_keywords       TEXT[] NOT NULL DEFAULT '{}',  -- from the delimiter block, or entity-extraction fallback
    concept_guess            TEXT,
    structured_retrieval_hits INT NOT NULL DEFAULT 0,  -- post-rerank hit count from the primary retrieve() pass
    generic_retrieval_hits   INT,                      -- null until the in-request generic-query retry runs
    first_response_kind      TEXT NOT NULL DEFAULT 'delimiter'
                             CHECK (first_response_kind IN ('delimiter', 'hedge_regex')),
    final_answer_text        TEXT,                      -- null until the follow-up WhatsApp send completes
    outcome                  TEXT
                             CHECK (outcome IS NULL OR outcome IN ('recovered', 'still_empty', 'retry_error')),
    needs_review             BOOLEAN NOT NULL DEFAULT true,
    reviewed_at              TIMESTAMPTZ,
    review_result            TEXT
                             CHECK (review_result IS NULL OR review_result IN ('alias_miss', 'pattern_gap', 'data_gap'))
);

CREATE INDEX IF NOT EXISTS idx_query_flags_needs_review ON config.query_flags (needs_review) WHERE needs_review = true;
CREATE INDEX IF NOT EXISTS idx_query_flags_created_at ON config.query_flags (created_at DESC);

-- Staged fixes from the nightly review pass — alias mappings and reusable Cypher
-- patterns land here for a human approval tap before touching the real
-- canonicalisation mechanism (linker.py's ALIAS_OF edges) or the 14B's Cypher
-- few-shot context. Never written to directly by the review pass.
CREATE TABLE IF NOT EXISTS config.resolution_fixes (
    id           BIGSERIAL PRIMARY KEY,
    flag_id      BIGINT NOT NULL REFERENCES config.query_flags(id) ON DELETE CASCADE,
    fix_type     TEXT NOT NULL CHECK (fix_type IN ('alias', 'pattern')),
    payload      JSONB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_resolution_fixes_pending ON config.resolution_fixes (status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_resolution_fixes_flag ON config.resolution_fixes (flag_id);

GRANT SELECT, INSERT, UPDATE ON config.query_flags TO openclaw_curator_role, openclaw_n8n_role;
GRANT SELECT, INSERT, UPDATE ON config.resolution_fixes TO openclaw_curator_role, openclaw_n8n_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA config TO openclaw_curator_role, openclaw_n8n_role;
GRANT SELECT ON config.query_flags TO dashboard_ro;
GRANT SELECT ON config.resolution_fixes TO dashboard_ro;
