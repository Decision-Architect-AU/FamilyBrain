-- Some routines are facts about the week, not appointments.
--
-- The complaint was too many appointments, and the arithmetic backs it up:
-- two school-attendance routines generate 66 future entries each, three
-- medications 145 each. A child being at school between 9:00 and 14:40 is
-- the normal state of a Tuesday. Putting it on the family calendar every
-- weekday buries the things that actually need a decision — choir, cello
-- practice, dance, the babysitter, a grandparent doing a pickup — which are
-- exactly the entries that must stay.
--
-- The rows themselves are still worth having: they are what tells the rest of
-- the system that a child is unavailable at that hour, so a clash with a
-- 10am appointment can still be detected. It is only the calendar that does
-- not need to be told. So this is a display decision, not a data one: the
-- events are generated and kept, and the calendar writer leaves them alone.

ALTER TABLE personal.asset
    ADD COLUMN IF NOT EXISTS calendar_silent boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN personal.asset.calendar_silent IS
    'True for routines whose generated events belong in the database but not '
    'on anyone''s calendar — school attendance, a daily medication. They are '
    'still generated, still block the person''s slots, and still answer '
    '"what is on", but they are never written to Google or Outlook.';

-- School attendance: the routine that prompted this. Named rather than
-- matched on asset_type, because 'routine' also covers choir and dance.
UPDATE personal.asset
   SET calendar_silent = true
 WHERE status = 'active'
   AND name ILIKE '%varsity college%'
   AND asset_type = 'routine';
