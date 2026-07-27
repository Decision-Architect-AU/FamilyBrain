-- Soft, evidence-weighted data-quality expectations — NOT hard constraints.
-- A row here is a claim like "MedicationAsset should have exactly one Subject,
-- confidence 0.95", not an enforced schema rule. Confidence moves based on
-- observed evidence (violations found by the light nightly check, chat
-- feedback that confirms or contradicts the expectation) and is revised by
-- the deeper LLM-based audit, which can soften, strengthen, or retire an
-- expectation entirely. This is the native (Postgres, no RDF/OWL) version of
-- what openclaw/ontology/familybrain-shapes.ttl encodes as static SHACL shapes
-- — same checks, but the rule itself is data that evolves instead of a fixed file.
CREATE TABLE IF NOT EXISTS config.data_expectation (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    name              TEXT NOT NULL UNIQUE,
    target_type       TEXT NOT NULL,           -- e.g. 'personal.event', 'personal.asset:medication'
    description       TEXT NOT NULL,           -- human-readable claim, e.g. "MedicationAsset should have exactly one Subject"
    check_sql         TEXT NOT NULL,           -- SQL returning violating row ids for the light check to run
    confidence         NUMERIC(4,3) NOT NULL DEFAULT 0.800,  -- 0.000-1.000; how much to trust this expectation right now
    violations_seen    INT NOT NULL DEFAULT 0,  -- cumulative count from the light check
    confirmations_seen INT NOT NULL DEFAULT 0,  -- cumulative count of rows that satisfy the expectation
    last_checked_at    TIMESTAMPTZ,
    last_reviewed_at   TIMESTAMPTZ,             -- last time the deeper audit reconsidered this expectation's confidence
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'softened', 'retired')),
    review_notes       TEXT                     -- LLM's reasoning from the last deep-audit pass, kept for provenance
);

CREATE INDEX IF NOT EXISTS idx_data_expectation_status ON config.data_expectation (status);

-- Table for individual violations found by the light check — feeds the
-- deeper audit's review of whether an expectation's confidence should move.
CREATE TABLE IF NOT EXISTS config.data_expectation_violation (
    id              BIGSERIAL PRIMARY KEY,
    expectation_id  BIGINT NOT NULL REFERENCES config.data_expectation(id) ON DELETE CASCADE,
    target_ref      TEXT NOT NULL,   -- e.g. 'personal.event:417047'
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,     -- set once fixed or explicitly accepted as a valid exception
    resolution      TEXT             -- 'fixed' | 'accepted_exception' | null (still open)
);

CREATE INDEX IF NOT EXISTS idx_dev_expectation ON config.data_expectation_violation (expectation_id);
CREATE INDEX IF NOT EXISTS idx_dev_unresolved ON config.data_expectation_violation (expectation_id) WHERE resolved_at IS NULL;

-- Seed rows from what today's ontology work actually found in production —
-- starting confidence reflects how solid the evidence was, not asserted as absolute.
INSERT INTO config.data_expectation (name, target_type, description, check_sql, confidence)
VALUES
(
    'event_has_subject',
    'personal.event',
    'Every non-cancelled event should resolve to a person, or a routine/property/entity asset — a bare event title with no Subject is close to meaningless (e.g. "Gold Coast Eisteddfod" says nothing about who is attending). Found 216 real violations (mostly insurance renewals) on first check.',
    $sql$SELECT id::text FROM personal.event
         WHERE status NOT IN ('cancelled','superseded')
           AND person_id IS NULL AND asset_id IS NULL$sql$,
    0.850
),
(
    'medication_asset_has_dose',
    'personal.asset',
    'A medication-type asset should have a recorded dose and frequency in facts — an incomplete record (e.g. Perampanel created without a dose before follow-up) is a real gap, not just a style nitpick.',
    $sql$SELECT id::text FROM personal.asset
         WHERE asset_type = 'medication' AND status = 'active'
           AND (facts->>'dose' IS NULL OR facts->>'frequency' IS NULL)$sql$,
    0.750
),
(
    'medication_asset_has_person',
    'personal.asset',
    'A medication-type asset should have exactly one person_id — found asset 2 (legacy disposed duplicate) missing this on first check; low-severity since already inactive.',
    $sql$SELECT id::text FROM personal.asset
         WHERE asset_type = 'medication' AND status = 'active' AND person_id IS NULL$sql$,
    0.900
),
(
    'medication_facts_not_misclassified',
    'personal.asset',
    'An asset whose facts contain drug_name/dose/frequency-shaped data should be asset_type=medication, not subscription or other — found asset 12 (Robina chemist Epilim script) genuinely misclassified this way on first check.',
    $sql$SELECT id::text FROM personal.asset
         WHERE asset_type != 'medication' AND status = 'active'
           AND (facts ? 'drug_name' OR facts ? 'dose')$sql$,
    0.700
)
ON CONFLICT (name) DO NOTHING;
