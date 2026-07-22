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

## Reliability — connection reuse and the watchdog

**Connection leak (fixed).** `gmail.py`'s `_gmail_service()` / `_calendar_service()`, `bill_calendar.py`'s `_cal_service()`, and `appointment_updater.py`'s `_cal_service()` each used to call `googleapiclient.discovery.build()` fresh on every invocation — worse, the latter two built a *Gmail* service purely to steal its credentials, then built a *second, separate* Calendar service. `build()`'s underlying `httplib2` transport is not closed promptly on garbage collection, so under sustained polling (every 5–15 min, for hours) these accumulated as `CLOSE_WAIT` sockets. Traced live: dozens of stuck `CLOSE_WAIT` connections to Google/Microsoft endpoints, zero Postgres connections held, all three loop threads silently stopped making progress — no crash, no error logged, `docker ps` still showed the container `Up`.

Fixed by caching built API clients per `(account_id, api)` in `gmail.py._cached_service()` (rebuilt every 30 min so token refresh is still picked up), with `bill_calendar.py` and `appointment_updater.py`'s `_cal_service()` now just calling `gmail.py`'s cached `_calendar_service()` instead of building their own.

**Watchdog (structural safeguard).** Even with the leak fixed, any blocking call without a timeout can hang a loop thread silently — Python's `try/except` around each loop body only catches exceptions, never a hang. `main.py` now has each loop (`email_loop`, `calendar_loop`, `financial_loop`) touch a heartbeat file (`/tmp/heartbeats/<name>`) after every iteration, and a `_watchdog_loop()` thread checks staleness every 60s. If a loop misses 4 consecutive cycles' worth of heartbeat, the watchdog calls `os._exit(1)` — skipping cleanup entirely, since a hung thread may be holding a lock a clean shutdown would wait on forever — and `restart: unless-stopped` in `docker-compose.yml` brings the container back. Docker only restarts a container on process *exit*; it has no way to detect an internal hang on its own, so the watchdog's job is specifically to turn "silently stuck" into "exited, restart me."

## Bill classification & extraction

`bill_calendar.py` uses a **single combined LLM call** per financial document — classification and payment extraction happen together, not as two passes. The prompt returns `document_type`, `requires_payment_from_us`, `is_spam` (+ `spam_reason`), and `payments[]` (populated only when a payment is actually owed). This replaced an earlier extraction-only prompt that had no way to say "this isn't a bill" — it would extract *something* from any document handed to it, including payslips and loan-application paperwork, because the prompt only ever asked "what's the payment", never "is there one at all".

Two documents that surfaced the earlier design's failure mode, both now handled by the pre-filter or the classification step:
- **A payslip forwarded inside a loan-application email thread** — the LLM had no signal to say "this document has nothing to do with what I'm supposed to extract" and hallucinated a biller/amount/invoice-ref from payslip noise. Fixed with a keyword pre-filter (`_NOT_A_BILL_KW`: payslip/pay-period/loan-application-documents/etc.) *and* the `requires_payment_from_us` field.
- **Placeholder reference numbers slipping through** — the scrubbing regex only matched `INV1234` (exactly 4 digits) with a trailing word boundary, so a real-looking `INV 12345` (5 digits) sailed through unscrubbed. Fixed to `INV[-\s]*12345?\b` / `REF[-\s]*12345?\b`.

A note that fails classification (`is_spam=true`, `requires_payment_from_us=false`, or the LLM call itself errors) gets `bill_event_id = 'SKIP'` and is never retried — this is deliberate: retrying every cycle on a document that will never become a bill just burns LLM time. On LLM failure the function now **skips rather than falls back to a single-item guess** — the old fallback used the email subject as `biller`, which is exactly the kind of unverified data suppression is meant to catch further downstream.

## Email decomposer — per-item transactions

`email_decomposer.py` processes each extracted item (`calendar_event` / `payment` / `observation` / `task`) inside its **own** `psycopg2.connect(...)` block rather than one connection held open across the whole item list. Holding a single connection across multiple items risked a self-deadlock: `_create_calendar_event` could hold an open transaction on an event row while `upsert_event` (called for a later item) opened a second connection and tried to touch the same row — a genuine 47-hour hang was traced to exactly this. `db.conn()` also sets `lock_timeout=8000`/`statement_timeout=60000` so any future lock conflict fails fast instead of hanging indefinitely.

Every row fetched with `RealDictCursor` is a dict-like object, not a tuple — `cur.fetchone()[0]` raises `KeyError(0)`, not `IndexError`. Every function in this codebase that reads a `RealDictCursor` result accesses it by column name (`row["id"]`), never by position.

Calendar event source metadata (account email, From address, received date) is appended to the event's notes field so its provenance is visible directly in Google Calendar, not just in the DB. The event UPDATE inside `_create_calendar_event` sets `status = 'confirmed'`, not `'ingested'` — `appointment_updater` explicitly excludes `status IN ('cancelled','superseded','ingested')` from its GCal write query, so setting `'ingested'` here silently blocked every email-derived event from ever reaching the calendar.

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
