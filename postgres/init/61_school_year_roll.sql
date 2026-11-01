-- The school year has to turn over, and the class has to be learned.
--
-- school_year is how a notice gets attributed to a child: audience resolution
-- reads "3C" out of an email, takes the 3, and finds the child in year 3.
-- That number was typed in once. Come late January, Elliana is in year 4 and
-- Olivia in year 2 — and nothing would have changed, so every year-4 notice
-- would have gone unattributed and every year-3 notice would have been
-- pinned on a child who had left that class.
--
-- Two facts are needed to make that turn over safely.
--
-- school_year_as_at: the calendar year the school_year value describes. A
-- year cannot be incremented idempotently without knowing when it was last
-- incremented — a task that runs nightly through February would otherwise put
-- a Year 1 child into Year 30.
--
-- class_code: the class itself, as the school writes it ("3C"). It lived only
-- inside an asset's facts and the text of a generated event title, so there
-- was nowhere to say "we know she has moved up but not which class she is
-- in". That gap is the normal state of things for the fortnight between the
-- school's allocation email in mid-January and the first day back.

ALTER TABLE personal.person
    ADD COLUMN IF NOT EXISTS school_year_as_at smallint,
    ADD COLUMN IF NOT EXISTS class_code        text,
    ADD COLUMN IF NOT EXISTS class_code_as_at  smallint;

COMMENT ON COLUMN personal.person.school_year_as_at IS
    'The calendar year that school_year describes. Without it the annual '
    'roll cannot tell whether it has already run.';

COMMENT ON COLUMN personal.person.class_code IS
    'The class as the school writes it, e.g. 3C. NULL means the child has '
    'moved up and the new class is not known yet — which is the true state '
    'between the roll and the school''s allocation email.';

COMMENT ON COLUMN personal.person.class_code_as_at IS
    'The calendar year class_code applies to, so a stale code from last year '
    'is never mistaken for this year''s.';

-- What is true as this is written: the class codes sitting in the two school
-- routine assets, which were the only record of them.
UPDATE personal.person p
   SET class_code = a.facts->>'class',
       class_code_as_at = 2026,
       school_year_as_at = 2026,
       updated_at = now()
  FROM personal.asset a
 WHERE a.person_id = p.id
   AND a.asset_type = 'routine'
   AND a.name ILIKE '%varsity college%'
   AND coalesce(a.facts->>'class', '') <> ''
   AND p.school_year IS NOT NULL;

-- Any other child on file: the school year is known, the class is not.
UPDATE personal.person
   SET school_year_as_at = 2026, updated_at = now()
 WHERE school_year IS NOT NULL AND school_year_as_at IS NULL;
