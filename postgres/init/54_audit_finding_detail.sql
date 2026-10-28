-- Make the deep model's audit worth the 205 seconds it costs.
--
-- The first version of the spot check spent three and a half minutes of GPU on
-- one word: a verdict, plus whatever sentence happened to follow it. That is a
-- poor trade. The model reasons at length whether or not it is asked to, and
-- that reasoning was being thrown away — it weighs alternatives, notices facts
-- the extractor dropped, and can say what the extractor should have done
-- differently. Only the conclusion was being kept.
--
-- So the audit now records the reasoning as well, under columns that answer
-- separate questions:
--
--   confidence / alternatives  how firm the verdict is, and what else it
--                              weighed before settling. A medium-confidence
--                              finding that names a rejected alternative is
--                              far more use in review than a bare verdict:
--                              it shows whether to trust the disagreement.
--   mismatch                   where the entry contradicts the source text.
--   missing                    facts the source states that the entry lacks.
--                              This is the knowledge-completeness check: an
--                              entry can be entirely correct and still be
--                              missing the cost, the location, or the fact
--                              that an adult must attend.
--   improve                    what the extractor should do differently with
--                              this kind of email. These accumulate into
--                              evidence about where the small models fail.
--
-- Still recorded, still never auto-applied: a second model's opinion is
-- evidence, not authority.

ALTER TABLE personal.audit_finding
    ADD COLUMN IF NOT EXISTS confidence   text,
    ADD COLUMN IF NOT EXISTS alternatives text,
    ADD COLUMN IF NOT EXISTS mismatch     text,
    ADD COLUMN IF NOT EXISTS missing      text,
    ADD COLUMN IF NOT EXISTS improve      text;

COMMENT ON COLUMN personal.audit_finding.confidence IS
    'high, medium or low, in the auditor''s own words. Low confidence is a '
    'reason to read the finding, not a reason to discard it.';

COMMENT ON COLUMN personal.audit_finding.alternatives IS
    'Verdicts the auditor weighed and rejected. Shows the judgement behind a '
    'disagreement instead of asserting it.';

COMMENT ON COLUMN personal.audit_finding.mismatch IS
    'Where the entry contradicts its source text.';

COMMENT ON COLUMN personal.audit_finding.missing IS
    'Facts stated in the source that belong on the entry but are absent. An '
    'entry with no mismatch can still be incomplete.';

COMMENT ON COLUMN personal.audit_finding.improve IS
    'What the extractor should have done differently. Collected across '
    'findings, these say where the small models need help.';

-- A complete audit is worth keeping even when the verdict is OK: an entry that
-- is right but missing half the email's facts has nothing to disagree about
-- and everything to improve. Only a finding with nothing at all to say is
-- dropped, so the uniqueness rule has to admit OK rows now.
COMMENT ON COLUMN personal.audit_finding.verdict IS
    'OK, WRONG_DATE, WRONG_PERSON, NOT_AN_EVENT or UNSURE. An OK verdict is '
    'stored only when the auditor still found something missing or worth '
    'improving; a clean OK is counted and discarded.';
