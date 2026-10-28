-- Give the audit a queue you can work through, and let the safe repairs apply
-- themselves.
--
-- A findings table nobody triages is a second inbox. Three things were
-- missing: a sense of which findings matter, a record of the ones already
-- dealt with, and a way for the obviously-safe repairs to happen without a
-- person retyping them.
--
--   score        1 to 10, the auditor's own judgement of how much this
--                matters. Sort by it and the wrong appointment someone will
--                act on comes above a missing ticket price.
--   risk         how dangerous the auditor thinks its own suggestion is to
--                apply blind. Its own estimate, and never the only gate.
--   patch_field  a machine-readable repair: which field, and the text to put
--   patch_value  in it. Filled only for repairs the code is allowed to make.
--
-- Lifecycle, in `status`:
--   open      nobody has dealt with it
--   applied   code made the change (applied_at, applied_note say what)
--   resolved  a person fixed it (reviewed_at, review_notes say how)
--   dismissed not a real problem
--
-- What the code may apply is deliberately narrow, and the narrowness lives in
-- the code rather than here: additive text only, on an entry the auditor
-- judged correct, with the new text traceable to the source email. Dates,
-- people and titles are never touched automatically. The cost of silently
-- corrupting a correct appointment is much higher than the cost of a line in a
-- review list, so the automatic path only ever adds detail the email states.

ALTER TABLE personal.audit_finding
    ADD COLUMN IF NOT EXISTS score        smallint,
    ADD COLUMN IF NOT EXISTS risk         text,
    ADD COLUMN IF NOT EXISTS patch_field  text,
    ADD COLUMN IF NOT EXISTS patch_value  text,
    ADD COLUMN IF NOT EXISTS applied_at   timestamptz,
    ADD COLUMN IF NOT EXISTS applied_note text;

COMMENT ON COLUMN personal.audit_finding.score IS
    '1 to 10, the auditor''s judgement of how much this finding matters. '
    '1 is cosmetic, 10 is an entry someone will act on and should not.';

COMMENT ON COLUMN personal.audit_finding.risk IS
    'low, medium or high: how safe the auditor thinks its own suggestion is '
    'to apply without a person reading it. Advisory only.';

COMMENT ON COLUMN personal.audit_finding.patch_field IS
    'The one field a safe repair would touch (notes or location). NULL when '
    'the finding needs a person.';

COMMENT ON COLUMN personal.audit_finding.patch_value IS
    'The text a safe repair would add, in the auditor''s words but drawn '
    'from the source email.';

COMMENT ON COLUMN personal.audit_finding.applied_at IS
    'When code applied the repair. A row with applied_at was changed by the '
    'machine, not by a person — applied_note records exactly what changed.';

-- Worth reading first: highest score among the ones nobody has dealt with.
CREATE INDEX IF NOT EXISTS idx_audit_finding_triage
    ON personal.audit_finding (score DESC NULLS LAST, created_at DESC)
    WHERE status = 'open';

-- The queue of repairs waiting to be applied. Partial so it stays small.
CREATE INDEX IF NOT EXISTS idx_audit_finding_patchable
    ON personal.audit_finding (created_at)
    WHERE status = 'open' AND patch_field IS NOT NULL;
