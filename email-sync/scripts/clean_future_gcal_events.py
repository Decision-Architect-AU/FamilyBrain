"""
Delete all GCal events starting on or after 2026-07-01 from all calendars,
then delete the matching rows from personal.event.
"""
import os, sys, time
sys.path.insert(0, "/app")

import psycopg2, psycopg2.extras
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

DB_URL               = os.environ["DATABASE_URL"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
CUTOFF               = "2026-06-30T14:00:00Z"  # midnight AEST (UTC+10)

CALENDARS = [
    "primary",
    "family16899356601510423351@group.calendar.google.com",
    "f3b4bfc09d286d5e5e7f70ad558ed7a1b100c71189c5e005cdebb50c0761c827@group.calendar.google.com",
]


def get_service():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT refresh_token FROM personal.email_account "
            "WHERE provider='gmail' AND sync_calendar=true LIMIT 1"
        )
        row = cur.fetchone()
    conn.close()
    creds = Credentials(
        token=None, refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds)


def delete_future_from_cal(svc, cal_id: str):
    deleted = 0
    page_token = None
    while True:
        for attempt in range(5):
            try:
                resp = svc.events().list(
                    calendarId=cal_id,
                    timeMin=CUTOFF,
                    singleEvents=True,
                    maxResults=100,
                    pageToken=page_token,
                ).execute()
                break
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"  list error (attempt {attempt+1}): {e} — retrying")
                time.sleep(3 * (attempt + 1))

        items = resp.get("items", [])
        for ev in items:
            for attempt in range(5):
                try:
                    svc.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
                    deleted += 1
                    break
                except HttpError as e:
                    if e.resp.status == 410:  # already deleted
                        deleted += 1
                        break
                    if attempt == 4:
                        print(f"  failed to delete {ev['id']}: {e}")
                        break
                    time.sleep(2 * (attempt + 1))
                except Exception as e:
                    if attempt == 4:
                        print(f"  error deleting {ev['id']}: {e}")
                        break
                    time.sleep(2 * (attempt + 1))

        if deleted % 50 == 0 and deleted > 0:
            print(f"  [{cal_id[:20]}] {deleted} deleted so far")

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return deleted


def main():
    print("Connecting to GCal...")
    svc = get_service()

    total = 0
    for cal_id in CALENDARS:
        print(f"\nClearing future events from: {cal_id[:40]}")
        n = delete_future_from_cal(svc, cal_id)
        print(f"  → {n} events deleted")
        total += n

    print(f"\nGCal: {total} events deleted across all calendars")

    # Now delete from personal.event
    conn = psycopg2.connect(DB_URL)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM personal.event WHERE starts_at >= '2026-07-01' RETURNING id")
        db_deleted = cur.rowcount
    conn.commit()
    conn.close()
    print(f"DB: {db_deleted} rows deleted from personal.event")
    print("\nDone. Restart email-sync to begin fresh resync.")


if __name__ == "__main__":
    main()
