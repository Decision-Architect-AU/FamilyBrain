"""
Finds and deletes duplicate GCal events caused by the fresh-insert fallback
creating new events while old restored events (with same fb:eXXXX tag) remained.

Strategy: for each event in DB with a gcal_event_id, search the family calendar
for any other events sharing the same fb:eXXXX description tag — delete those.
"""
import os, re, sys, time
import psycopg2, psycopg2.extras
from googleapiclient.discovery import build

DB_URL = os.environ["DATABASE_URL"]
_FB_TAG_RE = re.compile(r"\[fb:(e\d+[^]]*)\]")

def _cal_svc():
    sys.path.insert(0, "/app")
    from src.db import get_enabled_accounts
    from src.gmail import _creds
    accounts = get_enabled_accounts()
    gmail = next(a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar"))
    return build("calendar", "v3", credentials=_creds(gmail), cache_discovery=False)

def run(dry_run=True):
    svc = _cal_svc()

    # Load all DB events with a gcal_event_id and their calendar
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, gcal_event_id, gcal_calendar_id, title
                FROM personal.event
                WHERE gcal_event_id IS NOT NULL
                  AND gcal_calendar_id IS NOT NULL
                  AND starts_at >= now() - INTERVAL '30 days'
            """)
            db_events = list(cur.fetchall())

    # Build a map: fb_id → canonical gcal_event_id
    canonical: dict[str, tuple[str, str]] = {}  # fb_id → (gcal_id, cal_id)
    for ev in db_events:
        fb_id = f"e{ev['id']}"
        canonical[fb_id] = (ev["gcal_event_id"], ev["gcal_calendar_id"])

    print(f"[dedup] {len(canonical)} DB events with GCal IDs to check")

    # Fetch all upcoming events from the calendars we care about
    # Search by extendedProperties won't find all, so search by description text
    calendars_to_check = list({ev["gcal_calendar_id"] for ev in db_events})

    total_deleted = 0
    for cal_id in calendars_to_check:
        page_token = None
        while True:
            resp = svc.events().list(
                calendarId=cal_id,
                timeMin="2026-06-01T00:00:00Z",
                timeMax="2030-01-01T00:00:00Z",
                maxResults=250,
                singleEvents=True,
                pageToken=page_token,
            ).execute()

            items = resp.get("items", [])
            for item in items:
                desc = (item.get("description") or "") + str(item.get("extendedProperties", {}).get("private", {}).get("fb_id", ""))
                m = _FB_TAG_RE.search(desc)
                fb_tag = item.get("extendedProperties", {}).get("private", {}).get("fb_id") or (m.group(1) if m else None)
                if not fb_tag:
                    continue
                gcal_id = item["id"]
                canon = canonical.get(fb_tag)
                if not canon:
                    continue
                canon_id, canon_cal = canon
                if gcal_id != canon_id:
                    # This is an orphan — the DB references a different ID for this fb tag
                    summary = item.get("summary", "?")[:50]
                    start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date", "?")
                    print(f"[dedup] {'DRY' if dry_run else 'DELETE'} orphan [{fb_tag}] '{summary}' {start[:10]} gcal_id={gcal_id}")
                    if not dry_run:
                        try:
                            svc.events().delete(calendarId=cal_id, eventId=gcal_id).execute()
                            total_deleted += 1
                            time.sleep(0.05)
                        except Exception as e:
                            print(f"  → delete failed: {e}")

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    print(f"\n[dedup] {'would delete' if dry_run else 'deleted'} {total_deleted} orphan event(s)")

if __name__ == "__main__":
    dry = "--delete" not in sys.argv
    if dry:
        print("[dedup] DRY RUN — pass --delete to actually remove")
    run(dry_run=dry)
