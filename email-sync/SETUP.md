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

---

## How it works

Each poll cycle runs five stages in sequence:

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 1 — Email sync                                        │
│                                                              │
│  Gmail (history API) + Outlook (delta query)                 │
│  → personal.email_message (dedup, one row per message)       │
│  → Two independent cursors per account:                      │
│       sync_cursor          (email history / delta)           │
│       calendar_sync_cursor (GCal syncToken / cal delta)      │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 2 — Email decomposer  (qwen2.5:14b)                   │
│                                                              │
│  One email can produce multiple typed items:                 │
│    calendar_event  → personal.event                          │
│    payment         → financial_doc note → bill_calendar      │
│    observation     → personal.note                           │
│    task            → personal.note (tagged)                  │
│                                                              │
│  Skips: junk / marketing / newsletter / notification         │
│  Marks email_decomposed = true when done                     │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 3 — Financial processor                               │
│                                                              │
│  Structured extraction from attachments (PDF, invoices)      │
│  → personal.note (financial_doc)                             │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 4 — Bill calendar                                     │
│                                                              │
│  Creates / enriches personal.event rows for financial notes  │
│  One event per payment (multi-bill emails → multiple events) │
│  effective_date set from due date in Brisbane timezone       │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 5 — Appointment updater (sole GCal writer)            │
│                                                              │
│  Polls personal.event WHERE:                                 │
│    gcal_event_id IS NULL                (never written)      │
│    OR updated_at > calendar_written_at  (changed since sync) │
│    OR next_update_at <= now()           (scheduled recheck)  │
│                                                              │
│  Routes to target calendar:                                  │
│    bills     → Bills calendar (3 days before due, day-of)   │
│    family    → Family calendar (per-person colour tags)      │
│    holiday   → Holidays calendar + individual day events     │
│    default   → Primary calendar                              │
│                                                              │
│  Updates gcal_event_id, calendar_written_at, next_update_at  │
└──────────────────────────────────────────────────────────────┘
```

### Channel rules

Scheduling and routing are driven by `personal.channel_rule` rows — not hardcoded logic. When a new event is inserted, `channel_resolver.materialise()` evaluates the rules and writes `next_update_at` immediately. The appointment updater just polls that indexed column.

| Schedule | When `next_update_at` fires | Typical use |
|----------|-----------------------------|-------------|
| `immediate` | now() | Family events, tasks, catch-all |
| `before_event:3d` | 06:00 AEST, 3 days before effective_date | Bill reminders |
| `on_due_date` | 06:00 AEST on effective_date | Final bill check |
| `batch:daily:07:00` | Next 07:00 AEST | Observation digests |
| `never` | NULL | Only re-process on explicit change |

### effective_date

Every event stores `effective_date DATE` — the Brisbane local calendar date, regardless of the event's TIMESTAMPTZ. All-day events from any timezone resolve correctly. Use `effective_date` for all date-range queries, not `starts_at`.

### Separate sync cursors

Each `personal.email_account` row has two independent cursors:
- `sync_cursor` — email history position (Gmail historyId / Outlook deltaLink)
- `calendar_sync_cursor` — calendar sync position (GCal syncToken / Outlook calendar deltaLink)

They advance independently so an email-only resync never resets the calendar cursor.

---

## Adding more accounts

Just insert another row into `personal.email_account` with the refresh token.
No code changes, no restarts needed after the first sync pass completes.

---

## Reprocessing / resets

### Reset email ingestion for a date range

```sql
-- Reprocess all emails from FY2024 onwards (unset email_decomposed and ingest_status)
UPDATE personal.email_message
SET email_decomposed = false, ingest_status = 'pending'
WHERE received_at >= '2023-07-01';
```

### Delete and resync Google Calendar events after a date

```bash
docker compose run --rm email-sync python -m scripts.reset_calendar_after
```

Edit `CUTOFF_DATE` in `scripts/reset_calendar_after.py` before running. This:
1. Deletes all GCal events on/after the cutoff from all writable calendars
2. Clears `target_cal_provider_id`, `gcal_event_id`, `calendar_written_at` for affected events
3. Clears `calendar_sync_cursor` so the next poll re-fetches the full window

### Model

Set `AGENT_MODEL` in `.env` (default `qwen2.5:14b`). This controls the decomposer and financial extraction LLM.
