# email-sync

Polls Gmail and Outlook accounts and pushes email and calendar data into the knowledge base.

## What it does

Runs five sequential stages on each poll cycle:

| Stage | What it does |
|-------|-------------|
| **Email sync** | Incremental Gmail (history API) + Outlook (delta query) → `personal.email_message`; includes inbox and sent items |
| **Email decomposer** | LLM breaks each email into typed items: `calendar_event`, `payment`, `observation`, `task` |
| **Financial processor** | Structured extraction from PDF/invoice attachments → `personal.note` |
| **Bill calendar** | Creates/enriches Google Calendar events for financial notes |
| **Appointment updater** | Polls `next_update_at <= now()` → writes enriched events to Google Calendar |

## Sent item ingestion

Both Gmail and Outlook sync sent items alongside received mail:
- **Gmail** — `in:sent` included in initial query; `SENT` label detected on incremental history events
- **Outlook** — separate `SentItems` delta query with its own cursor (`sent_sync_cursor`)

Sent emails are stored with `is_sent = true`, formatted as `To: <recipients>` in the knowledge base, and tagged `sent`. This captures your side of every conversation even when the other party's account isn't connected.

## Inter-party forwarding

When multiple accounts are connected (e.g. yours and your partner's), emails you sent appear in their inbox as received mail — and are ingested from both perspectives. Dedup is keyed on `(account_id, provider_msg_id)` so each account retains its own copy with any annotations or reply context.

## Calendar routing

| Event type | Target calendar |
|-----------|----------------|
| Bills / invoices | Bills calendar (reminder 3 days before due, day-of) |
| Child events (matched by `CHILD1_NAMES` / `CHILD2_NAMES`) | Family calendar |
| Public holidays | Holidays calendar + individual day events in Family calendar |
| Everything else | Primary calendar |

## Adding an account

1. Complete OAuth consent flow and obtain a refresh token
2. Insert a row into `personal.email_account`:
   ```sql
   INSERT INTO personal.email_account
     (provider, email_address, display_name, refresh_token, owner_person_id,
      is_primary, is_partner_calendar, sync_email, sync_calendar)
   VALUES ('gmail', 'user@gmail.com', 'Display Name', '<refresh_token>',
           <person_id>, false, false, true, true);
   ```
3. Restart email-sync — initial backfill starts on next poll

## GCal event tracking

Every event written to Google Calendar by the appointment updater carries two tracking identifiers:

**Stable event ID** — `fb{event_id:012x}` (e.g. `fb00000002028c` for DB event 131724). Used as the GCal event `id` on insert so that repeated runs are idempotent — re-inserting the same DB event will find the existing GCal event rather than creating a duplicate.

**Description tag** — `[fb:eXXXXX]` appended to the event description (e.g. `[fb:e131724]`). Also written to `extendedProperties.private.fb_id`. This tag survives if the GCal event is manually edited and is used by the duplicate scanner to identify FamilyBrain-owned events regardless of their current GCal event ID.

### Stable ID fallback — handling GCal trash

GCal does not release custom event IDs when an event is deleted — the ID remains reserved in the trash for up to 30 days. If the stable `fb*` ID is in the trash:

1. `INSERT` with stable ID → GCal returns **409** (identifier already exists)
2. `PATCH` the stable ID → GCal returns **403** (forbidden on cancelled event)
3. `INSERT` without custom ID → **succeeds**, returns a Google-generated ID
4. New ID is written to `personal.event.gcal_event_id`

This means after a bulk purge-and-restore cycle, events will temporarily carry Google-generated IDs instead of stable `fb*` IDs. The description tag (`[fb:eXXXXX]`) still identifies them.

### Duplicate detection — `purge_gcal_duplicates.py`

After a purge-and-restore cycle the fallback path can create a second GCal event while the original restored event still exists, both tagged `[fb:eXXXXX]`. Run the dedup scanner to clean these up:

```bash
# Dry run — shows what would be deleted
docker exec familybrain-email-sync python -m src.purge_gcal_duplicates

# Live delete
docker exec familybrain-email-sync python -m src.purge_gcal_duplicates --delete
```

The scanner fetches all upcoming events from each calendar, extracts the `fb:eXXXXX` tag from each, and compares against `personal.event.gcal_event_id`. Any GCal event whose ID does not match the DB record for that tag is an orphan and is deleted.

## Key env vars

```env
DATABASE_URL=postgresql://curator:<password>@postgres:5432/familybrain
GOOGLE_CLIENT_ID=<required>
GOOGLE_CLIENT_SECRET=<required>
MICROSOFT_CLIENT_ID=<required>
MICROSOFT_TENANT_ID=consumers
INGESTOR_URL=http://ingestor:4001
CHILD1_NAMES=firstname,nickname
CHILD2_NAMES=firstname,nickname
CALENDAR_MIRROR_PRIMARY_EMAIL=<email>
CALENDAR_MIRROR_PARTNER_EMAIL=<email>
GMAIL_INITIAL_DAYS=730
OUTLOOK_INITIAL_DAYS=90
EMAIL_POLL_INTERVAL_SECS=300
CALENDAR_POLL_INTERVAL_SECS=900
```
