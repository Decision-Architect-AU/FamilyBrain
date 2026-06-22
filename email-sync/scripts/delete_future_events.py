"""
Delete all calendar events after 2026-07-01 from GCal and the DB.
"""
import os, sys
sys.path.insert(0, "/app")

import psycopg2
import psycopg2.extras
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

DB_URL = os.environ["DATABASE_URL"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
CUTOFF = "2026-07-01"


def get_gcal_service(refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT refresh_token FROM personal.email_account
        WHERE provider = 'gmail' AND sync_calendar = true
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        print("No Gmail account with calendar sync found")
        return
    service = get_gcal_service(row["refresh_token"])

    cur.execute("""
        SELECT id, gcal_event_id, gcal_calendar_id, title, starts_at::date as day
        FROM personal.event
        WHERE starts_at >= %s AND gcal_event_id IS NOT NULL
        ORDER BY gcal_calendar_id, gcal_event_id
    """, (CUTOFF,))
    events = cur.fetchall()

    print(f"Deleting {len(events)} events from GCal...")
    deleted = 0
    failed  = 0
    for ev in events:
        cal_id = ev["gcal_calendar_id"] or "primary"
        try:
            service.events().delete(calendarId=cal_id, eventId=ev["gcal_event_id"]).execute()
            deleted += 1
            if deleted % 10 == 0:
                print(f"  {deleted}/{len(events)} deleted...")
        except HttpError as e:
            if e.resp.status == 410:
                deleted += 1  # already gone
            else:
                print(f"  FAILED {ev['gcal_event_id']} ({ev['title']} {ev['day']}): {e}")
                failed += 1

    print(f"GCal: {deleted} deleted, {failed} failed")

    # Delete all from DB regardless of gcal_event_id
    cur.execute("DELETE FROM personal.event WHERE starts_at >= %s", (CUTOFF,))
    db_deleted = cur.rowcount
    conn.commit()
    print(f"DB: {db_deleted} rows deleted")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
