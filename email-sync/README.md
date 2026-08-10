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

## Triage keyword coverage — extracurricular activities

`ingestor/src/triage.py`'s fast keyword gate is what decides whether an email reaches the decomposer at all. The "School/kids" keyword group only covered `compass|excursion|permission slip|report card|uniform|tuckshop|term dates` — nothing for concerts, choir, music programs, or performances. Anything about those fell through to the ambiguous LLM triage step, which inconsistently classified genuinely relevant emails ("Melodies Choir - Permission Forms and Upcoming Performances", "Beginner Strings - End of Term Information") as `skip`, with no error logged anywhere — they looked enough like generic newsletter content that the fast classifier judged them irrelevant. Fixed by expanding the keyword group to cover `concert|recital|performance|rehearsal|ensemble|choir|orchestra|cello|violin|strings|music lesson|music program|gala|sports day|carnival|team assignment`.

**Recovering already-mis-skipped emails is not automatic.** A `skip`-classified email only gets a minimal DB record (`ingest_status='skip'`) — its body text is never persisted (`ingest_email()` in ingestor's `main.py` discards the payload entirely on that branch). Fixing the keyword gate only affects emails ingested *from that point forward*; anything already sitting as `skip` needs to be re-fetched from source (Gmail/Outlook, by `provider_msg_id`) and re-submitted — Outlook's delta feed in particular won't return an old message again just because the cursor is intact, since delta only returns what's changed since the last token.

## Routine synonym matching and person inheritance

`_resolve_routine_asset()` in `email_decomposer.py` matches a calendar item's title+detail text against every active routine asset's name and `synonyms` (`personal.asset.synonyms TEXT[]`) — longest match wins. This is a second, independent route to linking an event to a routine, alongside slot-key/date matching (Stage 2 override): informational emails ("Beginner Strings Blue - Term 3", "Melodies Choir - Upcoming Performances") don't land on one specific placeholder date, so they never hit Stage 2 at all, and previously never got tied to any routine.

When a match is found and the event's own person resolution (`_resolve_person_id()`, requires the person's name to literally appear in the text) comes up empty, the event **inherits the routine's `person_id`** — a bare title like "Gold Coast Eisteddfod" says nothing about who's attending, and an event untethered from a person/entity/property is close to meaningless on its own.

**Not yet automatic:** routines currently need synonyms seeded manually (e.g. direct SQL) whenever a naming gap is discovered. The natural next step is a maintenance task that notices recurring alternate terms in documents already linked to a routine and appends them on its own — see the main [README's Routines section](../README.md#synonyms--matching-a-routine-under-whatever-name-an-email-uses).

## Email attachment OCR

Image/PDF email attachments were never processed at all — `"attachments": []` was hardcoded in every payload sent to the ingestor, regardless of what the email actually had. Schedules, venue details, and timetables sent as an attached image/screenshot (not in the email body text) were invisible to both triage and the decomposer.

Fixed by reusing the ingestor's existing `/ingest/extract` endpoint (tesseract OCR, already used for the file-drop pipeline) for email attachments too:
- **Gmail** (`gmail.py`) — `_find_attachment_parts()` recursively walks the message payload for parts with a filename + `attachmentId`, fetches each via `messages().attachments().get()`, and OCRs image/PDF ones.
- **Outlook** (`outlook.py`) — `_extract_attachments_text()` calls `/me/messages/{id}/attachments`, which returns `contentBytes` directly (no second per-attachment fetch needed like Gmail); gated behind `hasAttachments` on the message so accounts without attachments don't pay for the extra API call.

Extracted text is appended to `body_text` before triage/`should_ingest()` runs, so both the fast keyword gate and the LLM decomposer see attachment content the same as body content — no separate code path needed downstream.

## Location was captured but never stored

`_create_calendar_event()` in `email_decomposer.py` extracted `item.get("location")` from the LLM's output but never passed it anywhere — `upsert_event()` had no `location` parameter at all, and neither the GCal event body nor the Outlook event body included a `location` field despite both APIs supporting one directly. Fixed: `upsert_event()` now accepts and stores `location`; `appointment_updater.py`'s GCal (`body["location"]`) and Outlook (`body["location"] = {"displayName": ...}`) write paths both include it when present. The `personal.event` SELECT that feeds the update loop was also missing the `location` column entirely, so this required three separate fixes to actually reach the calendar, not one.

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
| Suspended routine occurrences (see below) | Tentative calendar (falls back to Primary if unconfigured) |
| Everything else | Primary calendar |

### Suspended events → Tentative calendar

wa-agent's maintenance cycle can mark a generated routine occurrence `status='suspended'` instead of deleting it — e.g. a school holiday suppresses a pickup routine, or a `personal.routine_gap` row suspends just the occurrences tied to a specific away provider (see [wa-agent's maintenance docs](../wa-agent/src/maintenance.md) for how suspension is decided). `appointment_updater.py` checks `status` on every row it processes: a `suspended` event routes to the Tentative calendar regardless of what `classify_event()` would otherwise pick, and `[reason]` (from `suspended_reason`) is prepended to the calendar notes so the reason is visible directly in Google Calendar, not just in the DB.

This reuses the existing "move, never copy" reroute logic in `calendar_router.py` (`target_calendar_id()` compares the event's currently-stored `gcal_calendar_id` against the newly computed target and moves it if they differ) — no special-case code was needed for suspend/reinstate transitions. When wa-agent later reinstates a suspended event (the gap or holiday no longer applies), the next `appointment_updater` pass sees a normal `status` again, `classify_event()` picks its regular target calendar, and the event moves back automatically.

Configure the Tentative calendar per account via `personal.email_account.tentative_calendar_id`:
```sql
UPDATE personal.email_account
SET tentative_calendar_id = '<google_calendar_id>'
WHERE id = <account_id>;
```
If unset, `target_calendar_id()` falls back to the account's default/primary calendar — suspension still works, it just isn't visually separated in Calendar.

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
