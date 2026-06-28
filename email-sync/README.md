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
