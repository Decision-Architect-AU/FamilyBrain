# Email Sync Setup

## 1. Google OAuth2 App (once — covers all Gmail accounts)

1. Go to https://console.cloud.google.com → New Project → "OpenClaw"
2. Enable APIs: **Gmail API** and **Google Calendar API**
3. Credentials → Create OAuth 2.0 Client ID → Desktop app
4. Download client_secret JSON → note `client_id` and `client_secret`
5. Add to `.env`:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```

## 2. Microsoft OAuth2 App (once — covers all Outlook/Hotmail accounts)

1. Go to https://portal.azure.com → App registrations → New registration
2. Name: "OpenClaw", Supported account types: **Personal Microsoft accounts only**
3. Add Redirect URI: `http://localhost` (public client / mobile)
4. API permissions → Add: `Mail.Read`, `Calendars.ReadWrite`, `offline_access`
5. Note the Application (client) ID
6. Add to `.env`:
   ```
   MICROSOFT_CLIENT_ID=...
   MICROSOFT_TENANT_ID=consumers
   ```

## 3. Get refresh tokens (run once per account)

Use the helper script to authorise each account and get its refresh token:

```bash
# From openclaw root
docker compose run --rm email-sync python -m src.auth_helper
```

This opens a browser for each account and prints the refresh_token.

## 4. Add accounts to the database

Insert one row per inbox into `personal.email_account`:

```sql
-- Glenn's Gmail
INSERT INTO personal.email_account
    (provider, email_address, display_name, refresh_token, sync_email, sync_calendar, is_primary)
VALUES
    ('gmail', 'glenn@gmail.com', 'Glenn', '<refresh_token>', true, true, true);

-- Shannon's Gmail
INSERT INTO personal.email_account
    (provider, email_address, display_name, refresh_token, sync_email, sync_calendar)
VALUES
    ('gmail', 'shannon@gmail.com', 'Shannon', '<refresh_token>', true, true);

-- Glenn's Hotmail
INSERT INTO personal.email_account
    (provider, email_address, display_name, refresh_token, sync_email, sync_calendar)
VALUES
    ('outlook', 'glenn@hotmail.com', 'Glenn Hotmail', '<refresh_token>', true, true);
```

## 5. Start the service

```bash
docker compose --profile normal up -d email-sync
```

## How it works

- **Email**: polls each inbox every 5 min (configurable via `EMAIL_POLL_INTERVAL_SECS`)
  - New emails → classify (personal/property/decision) → ingest to graph + `personal.note`
  - Dedup via `personal.email_message` — each message ingested exactly once
  - Uses Gmail history API / Outlook delta query for incremental sync (no re-scanning)

- **Calendar**: syncs every 15 min (configurable via `CALENDAR_POLL_INTERVAL_SECS`)
  - Gmail events → `personal.event` → mirrored to Outlook
  - Outlook events → `personal.event` → mirrored to Gmail
  - Bidirectional via `personal.calendar_sync_map` — no duplicate mirror loops

## Adding more accounts

Just insert another row into `personal.email_account` with the refresh token.
No code changes, no restarts needed after the first sync pass completes.
