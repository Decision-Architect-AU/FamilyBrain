-- Surface email/calendar account sync health on the dashboard.
--
-- Confirmed live: shannon.garner@gmail.com's Gmail refresh token started
-- failing with invalid_grant on every sync attempt from 2026-08-18 onward —
-- silent for over 2 weeks because the only trace was in email-sync's own
-- container logs (docker logs), never persisted anywhere queryable. Every
-- sync failure is already caught and logged in gmail.py/outlook.py; this
-- just gives that catch block somewhere durable to write to, so a broken
-- account shows up on the dashboard instead of requiring someone to notice
-- symptoms (like this session's missing-invite investigation) and go
-- spelunking through logs to find the actual cause.
--
-- last_synced_at (already existed) tells you the account is stale but not
-- why; last_sync_error is the actual exception message from the most recent
-- failed attempt, cleared automatically the next time a sync succeeds.

ALTER TABLE personal.email_account
    ADD COLUMN IF NOT EXISTS last_sync_error    TEXT,
    ADD COLUMN IF NOT EXISTS last_sync_error_at TIMESTAMPTZ;
