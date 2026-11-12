-- "(no title)" is not a title.
--
-- 76_event_must_be_named.sql required a title to contain at least one letter,
-- which refuses '', '-', '...' and '???'. It does not refuse "(no title)",
-- and that is the one the family actually had: twelve rows, ten of them on a
-- calendar, a fortnightly Monday 3pm series running to 15 March 2027 showing
-- as "(no title)" at 3pm.
--
-- It came from a default standing in for a name. Both calendar importers read
-- the entry as `ev.get("subject", "(no title)")` / `ev.get("summary", "(no
-- title)")` and wrote the result straight through — into personal.event and
-- back out onto the routed calendar. The same meeting had been read from the
-- other parent's mailbox as "Glenn & Shannon Property Check-In"; that copy
-- never reached a calendar, so the named one is the one nobody saw.
--
-- A placeholder is worse than a blank. A blank fails a NOT NULL and gets
-- noticed. A placeholder satisfies every check, looks like data to the
-- generator, the digest and the calendar writer, and the only way to find out
-- what the entry is is to go hunting through two mailboxes for whatever
-- produced it. The importers now refuse one (see
-- calendar_router.usable_calendar_title); this is the same rule on the table,
-- because five paths insert events and a guard in four of them is not a
-- guard.

-- ── What counts as a placeholder ────────────────────────────────────────────
-- The list is short and literal on purpose. It holds only strings that assert
-- there is no name — not short names, and not vague ones. "MRI" stays, "Dr
-- Patel" stays, "School thing" stays: they are poor titles and they are
-- titles. Only a string whose entire content is "there is nothing here" is
-- refused, and the test is on the whole trimmed value, never a substring, so
-- "Pick up TBC forms" is unaffected.
--
-- IMMUTABLE because a CHECK constraint may only call an immutable function,
-- and this one genuinely is: same string in, same answer out, forever.
CREATE OR REPLACE FUNCTION personal.title_is_a_placeholder(t text)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT lower(btrim(coalesce(t, ''))) IN (
        '(no title)', 'no title', 'untitled', '(untitled)',
        '(no subject)', 'no subject', '(none)', 'none',
        'n/a', 'na', 'tbc', 'tba', 'tbd', '?', '-', '--'
    );
$$;

COMMENT ON FUNCTION personal.title_is_a_placeholder(text) IS
    'True when a title''s whole content asserts that there is no title. '
    'Matches the trimmed value in full, never a substring.';

-- ── Name the offenders, then stop any more arriving ─────────────────────────
-- A migration that cannot apply must say what is in the way, or the next
-- person runs it, reads "constraint violated", and has nothing to act on.
DO $$
DECLARE
    offender record;
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM personal.event
     WHERE personal.title_is_a_placeholder(title)
       AND status NOT IN ('cancelled', 'superseded');
    IF n = 0 THEN
        RAISE NOTICE 'personal.event: no placeholder titles — constraint will validate';
        RETURN;
    END IF;
    RAISE NOTICE 'personal.event: % live row(s) are named by a placeholder:', n;
    FOR offender IN
        SELECT id, effective_date, calendar_source,
               gcal_event_id IS NOT NULL AS on_calendar
          FROM personal.event
         WHERE personal.title_is_a_placeholder(title)
           AND status NOT IN ('cancelled', 'superseded')
         ORDER BY effective_date
    LOOP
        RAISE NOTICE '  event % — % — % %', offender.id, offender.effective_date,
            offender.calendar_source,
            CASE WHEN offender.on_calendar THEN '(on a calendar)' ELSE '' END;
    END LOOP;
    RAISE NOTICE 'Those rows are left as they are: most are live calendar '
                 'entries other people can see, and renaming or removing one '
                 'is a change to a shared calendar, not a migration''s '
                 'decision. The constraint goes on NOT VALID so nothing new '
                 'can be written; run VALIDATE once they have real names.';
END $$;

-- NOT VALID: enforced on every insert and update from now on, and silent
-- about the rows that are already there. The alternative was to fail until
-- somebody deleted ten entries off a shared calendar, which would have left
-- the fault in place for however long that took — the importers would have
-- gone on writing new ones the whole time.
ALTER TABLE personal.event DROP CONSTRAINT IF EXISTS event_title_is_not_a_placeholder;
ALTER TABLE personal.event
    ADD CONSTRAINT event_title_is_not_a_placeholder
    CHECK (NOT personal.title_is_a_placeholder(title)) NOT VALID;

COMMENT ON CONSTRAINT event_title_is_not_a_placeholder ON personal.event IS
    'Refuses a title whose whole content asserts there is no title — '
    '"(no title)", "untitled", "TBC". Matches the trimmed value in full, '
    'never a substring, so "Pick up TBC forms" is a fine title. Added '
    'NOT VALID with 12 such rows already present; finish with '
    'ALTER TABLE personal.event VALIDATE CONSTRAINT '
    'event_title_is_not_a_placeholder once they are named. See '
    '78_a_placeholder_is_not_a_name.sql.';

-- One thing NOT VALID does not do, which is worth knowing before relying on
-- it: it skips the existing rows at validation time, not at write time. A
-- grandfathered row still has to satisfy the constraint the moment anything
-- updates it, so these twelve are now frozen — an attempt to annotate their
-- notes from this very file failed on the constraint it had just added.
--
-- That is the right behaviour and it has a consequence: the only edit these
-- rows will accept is one that gives them a real name. Nothing can quietly
-- enrich, re-route or re-sync them in the meantime, which means they cannot
-- drift further and cannot be forgotten about either — the next writer that
-- touches one will fail loudly and name it.
