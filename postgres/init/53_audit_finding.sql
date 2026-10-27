-- Spot-check audit findings.
--
-- The pipeline is a cascade of small models: a 3B gate, a 3B router, a 14B
-- extractor. Each is fast and each is fallible, and nothing was checking their
-- work — the 35B reasoning model sat idle while 5,686 calls went to the small
-- ones. Nothing had ever called it: it was also misconfigured, named
-- qwen2.5:32b in compose while the loaded checkpoint is the OpenVINO 35B.
--
-- So the big model now earns its keep as an auditor rather than a worker: in
-- the nightly window it re-reads a small sample of what the small models
-- produced, against the source text they produced it from, and records where
-- it disagrees. Low volume, high value, and it runs when the GPU is free.
--
-- Findings are recorded, never auto-applied. A second model's opinion is
-- evidence, not authority, and the cost of silently "correcting" a correct
-- appointment is higher than the cost of a line in a review list.

CREATE TABLE IF NOT EXISTS personal.audit_finding (
    id           bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    entity_type  text        NOT NULL,
    entity_id    bigint      NOT NULL,
    verdict      text        NOT NULL,
    detail       text,
    model        text,
    source_email_id bigint,
    status       text        NOT NULL DEFAULT 'open',
    reviewed_at  timestamptz,
    review_notes text
);

COMMENT ON TABLE personal.audit_finding IS
    'Disagreements raised by the nightly deep-model spot check. Evidence for '
    'review, not instructions to change anything.';

COMMENT ON COLUMN personal.audit_finding.verdict IS
    'WRONG_DATE, WRONG_PERSON, NOT_AN_EVENT or UNSURE. An OK verdict is '
    'counted but not stored — only disagreements are worth a row.';

-- One open finding per entity per verdict: the check re-runs nightly and must
-- not pile up duplicates for something nobody has looked at yet.
CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_finding_open_unique
    ON personal.audit_finding (entity_type, entity_id, verdict)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_audit_finding_open
    ON personal.audit_finding (created_at DESC)
    WHERE status = 'open';
