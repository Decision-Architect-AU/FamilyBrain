-- Calendar routing columns on email_account
-- Stores named calendar IDs per account for Bills, Holidays, Family routing.
-- Fill these in after creating the calendars in Gmail/Outlook.

ALTER TABLE personal.email_account
    ADD COLUMN IF NOT EXISTS bills_calendar_id      TEXT,
    ADD COLUMN IF NOT EXISTS holidays_calendar_id   TEXT,
    ADD COLUMN IF NOT EXISTS family_calendar_id     TEXT,
    ADD COLUMN IF NOT EXISTS calendar_sync_cursor   TEXT;

COMMENT ON COLUMN personal.email_account.bills_calendar_id    IS 'Gmail calendarId or Outlook calendar objectId for Bills calendar';
COMMENT ON COLUMN personal.email_account.holidays_calendar_id IS 'Gmail calendarId or Outlook calendar objectId for Holidays calendar';
COMMENT ON COLUMN personal.email_account.family_calendar_id   IS 'Gmail calendarId or Outlook calendar objectId for Family calendar';
