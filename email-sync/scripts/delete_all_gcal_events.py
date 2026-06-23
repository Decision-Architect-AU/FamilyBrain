"""Delete ALL events from all Google Calendars (primary + shared cals)."""
import os, sys
sys.path.insert(0, "/app")

import psycopg2, psycopg2.extras
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

DB_URL               = os.environ["DATABASE_URL"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

CALENDARS = [
    "primary",
    "family16899356601510423351@group.calendar.google.com",
    "f3b4bfc09d286d5e5e7f70ad558ed7a1b100c71189c5e005cdebb50c0761c827@group.calendar.google.com",
]


def get_service():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT refresh_token FROM personal.email_account WHERE provider='gmail' AND sync_calendar=true LIMIT 1")
        row = cur.fetchone()
    conn.close()
    creds = Credentials(
        token=None, refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def delete_all_events(service, cal_id):
    import time
    deleted = 0
    page_token = None
    while True:
        # Retry list with backoff
        for attempt in range(5):
            try:
                resp = service.events().list(
                    calendarId=cal_id, pageToken=page_token,
                    maxResults=100, singleEvents=True,
                ).execute()
                break
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"  list retry {attempt+1}: {e}")
                time.sleep(3 * (attempt + 1))

        events = resp.get("items", [])
        if not events:
            break
        for ev in events:
            for attempt in range(4):
                try:
                    service.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
                    deleted += 1
                    break
                except HttpError as e:
                    if e.resp.status in (404, 410):
                        deleted += 1   # already gone
                        break
                    if attempt == 3:
                        print(f"  gave up on {ev['id']}: {e}")
                    else:
                        time.sleep(2)
                except Exception as e:
                    if attempt == 3:
                        print(f"  gave up on {ev['id']}: {e}")
                    else:
                        time.sleep(3 * (attempt + 1))
        print(f"  [{cal_id[:25]}] {deleted} deleted")
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return deleted


def main():
    service = get_service()
    total = 0
    for cal_id in CALENDARS:
        print(f"\nClearing: {cal_id}")
        n = delete_all_events(service, cal_id)
        print(f"  → {n} deleted")
        total += n

    # Clear DB too
    conn = psycopg2.connect(DB_URL)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM personal.event")
        db_deleted = cur.rowcount
    conn.commit()
    conn.close()

    # Reset sync cursors
    conn = psycopg2.connect(DB_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE personal.email_account SET calendar_sync_cursor = NULL")
    conn.commit()
    conn.close()

    print(f"\nTotal: {total} GCal events deleted, {db_deleted} DB rows deleted, cursors reset.")


if __name__ == "__main__":
    main()
