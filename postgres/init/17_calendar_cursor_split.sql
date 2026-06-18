-- Split sync_cursor into email_sync_cursor + calendar_sync_cursor
-- Previously both loops (email historyId/deltaLink AND calendar syncToken/deltaLink)
-- shared one column and overwrote each other on every sync run.

ALTER TABLE personal.email_account
    ADD COLUMN IF NOT EXISTS calendar_sync_cursor TEXT;

-- Migrate: existing sync_cursor values are email cursors (historyId / deltaLink).
-- Calendar cursors start NULL so the first calendar sync does a full backfill.
-- (No data loss — next email sync run will also refresh the email cursor.)

COMMENT ON COLUMN personal.email_account.sync_cursor IS
    'Email sync cursor: Gmail historyId or Outlook inbox deltaLink';

COMMENT ON COLUMN personal.email_account.calendar_sync_cursor IS
    'Calendar sync cursor: Gmail syncToken or Outlook calendarView deltaLink';
