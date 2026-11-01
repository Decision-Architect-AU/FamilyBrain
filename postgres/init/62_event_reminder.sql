-- A reminder on the entries that want one, now that nothing gets one by default.
--
-- Reminders were switched off wholesale, and rightly: FamilyBrain writes
-- school days, rent dates and enriched routines, and an entry it wrote is no
-- reason to interrupt somebody. But "off by default" only works if there is a
-- way to say otherwise, and some entries genuinely need it — roller skating
-- at 5pm on a Tuesday is no use remembered at 5pm.
--
-- So a reminder becomes a property of the event rather than a setting on the
-- calendar. A rule can ask for one, the generated occurrences carry it, and
-- the writer turns it into a notification; everything else stays silent. A
-- reminder the family adds by hand on the calendar is untouched either way,
-- because only inserts set this and patches leave reminders alone.

ALTER TABLE personal.event
    ADD COLUMN IF NOT EXISTS reminder_minutes smallint;

ALTER TABLE personal.event
    DROP CONSTRAINT IF EXISTS event_reminder_minutes_sane;
ALTER TABLE personal.event
    ADD CONSTRAINT event_reminder_minutes_sane CHECK (
        reminder_minutes IS NULL
        OR (reminder_minutes >= 0 AND reminder_minutes <= 40320)
    );

COMMENT ON COLUMN personal.event.reminder_minutes IS
    'Minutes before the start to notify. NULL means no reminder, which is the '
    'default for everything FamilyBrain writes. Capped at four weeks, which '
    'is Google''s own limit.';
