"""
One-shot Gmail backfill for Feb 1 – May 31 2026.
Run inside container: python3 /app/backfill_gmail_feb_may.py
"""
import os, sys
sys.path.insert(0, "/app")
os.chdir("/app")

import psycopg2, psycopg2.extras, requests as req
from datetime import datetime, timezone
from src.gmail import _gmail_service, _parse_message
from src import db
from src.filters import should_ingest, reset_cache as reset_filter_cache

INGESTOR_URL = os.environ.get("INGESTOR_URL", "http://localhost:8000")
DB_URL       = os.environ["DATABASE_URL"]

# Unix timestamps for the query
AFTER_TS  = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
BEFORE_TS = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())

print(f"Backfill: Gmail Feb 1 – May 31 2026 (after:{AFTER_TS} before:{BEFORE_TS})")

with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM personal.email_account WHERE provider='gmail' AND id=3")
        acct = cur.fetchone()

if not acct:
    print("ERROR: Gmail account id=3 not found")
    sys.exit(1)

print(f"Account: {acct['email_address']}")
svc = _gmail_service(acct)

# Collect all message IDs in the date range
query = f"after:{AFTER_TS} before:{BEFORE_TS}"
msg_ids = []
page_token = None
while True:
    kwargs = {"userId": "me", "maxResults": 500, "q": query}
    if page_token:
        kwargs["pageToken"] = page_token
    result = svc.users().messages().list(**kwargs).execute()
    batch = result.get("messages", [])
    msg_ids.extend(m["id"] for m in batch)
    page_token = result.get("nextPageToken")
    print(f"  listed {len(msg_ids)} so far...")
    if not page_token:
        break

print(f"Total messages in range: {len(msg_ids)}")

reset_filter_cache()
ingested = 0
skipped_dup = 0
skipped_filter = 0
errors = 0

for i, msg_id in enumerate(msg_ids):
    if db.is_already_ingested(acct["id"], msg_id):
        skipped_dup += 1
        continue
    try:
        msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()

        msg_labels = set(msg.get("labelIds", []))
        if msg_labels & {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES"}:
            skipped_filter += 1
            continue

        parsed = _parse_message(msg)
        raw_headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        ok, reason = should_ingest(parsed, raw_headers)
        if not ok:
            skipped_filter += 1
            continue

        resp = req.post(f"{INGESTOR_URL}/ingest/email", json=parsed, timeout=60)
        if resp.ok:
            ingested += 1
        else:
            print(f"  ingestor rejected {msg_id}: {resp.status_code} {resp.text[:100]}")
            errors += 1

    except Exception as e:
        print(f"  error {msg_id}: {e}")
        errors += 1

    if (i + 1) % 50 == 0:
        print(f"  progress: {i+1}/{len(msg_ids)} | ingested={ingested} dup={skipped_dup} filtered={skipped_filter} err={errors}")

print(f"\nDone. ingested={ingested} already_had={skipped_dup} filtered={skipped_filter} errors={errors}")
