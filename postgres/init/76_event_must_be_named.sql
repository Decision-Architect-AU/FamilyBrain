-- An event with no name is not a record of anything.
--
-- An appointment turned up with a blank title. Blank is worse than wrong: a
-- wrong title can be corrected because you can see what it says, and an
-- entry reading "(No title)" at 2pm tells the family there is something on
-- and nothing about it, so the only way to find out is to go looking through
-- email for whatever produced it.
--
-- Every writer here builds a title before it inserts, so a blank one means a
-- writer got an empty string from somewhere and inserted it anyway. The place
-- to stop that is the table, not each writer in turn: there are five paths
-- that insert events (the rules generator, the decomposer, the appointment
-- updater, the task sync, and hand-written SQL) and a guard in four of them
-- is not a guard.
--
-- "At least one letter" rather than "not empty", because '-', '?' and '...'
-- are all as useless as '' and all pass a NOT NULL. Deliberately nothing
-- about length: "MRI" is a perfectly good title.

BEGIN;

-- Nothing should be in here, but a constraint that fails to apply because of
-- existing data is a constraint nobody adds. Name the offenders first.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM personal.event
   WHERE title IS NULL OR title !~ '[[:alpha:]]';
  IF n > 0 THEN
    RAISE NOTICE 'personal.event: % row(s) have no usable title — listed below', n;
    FOR n IN SELECT id FROM personal.event
              WHERE title IS NULL OR title !~ '[[:alpha:]]' LOOP
      RAISE NOTICE '  event id %', n;
    END LOOP;
    RAISE EXCEPTION 'fix or supersede those rows, then re-run this migration';
  END IF;
END $$;

ALTER TABLE personal.event
  ADD CONSTRAINT event_title_is_a_name
  CHECK (title ~ '[[:alpha:]]');

COMMENT ON CONSTRAINT event_title_is_a_name ON personal.event IS
  'An event must say what it is: a title with at least one letter. '
  'Blank, "-" and "..." are all rejected — see 76_event_must_be_named.sql.';

COMMIT;
