"""
Dedup calendar events after 2026-07-01.
For each (title, date) group with duplicates, keep the one with the longest notes
and delete the rest from GCal + DB.
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
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Get Gmail account credentials
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

    # Find IDs to delete: for each (title, date) group, keep highest note_len
    cur.execute("""
        SELECT e.id, e.gcal_event_id, e.gcal_calendar_id, e.title, e.starts_at::date as day
        FROM personal.event e
        WHERE e.starts_at >= %s
          AND (e.title, e.starts_at::date) IN (
            SELECT title, starts_at::date
            FROM personal.event
            WHERE starts_at >= %s
            GROUP BY title, starts_at::date
            HAVING COUNT(*) > 1
          )
          AND e.id NOT IN (
            SELECT DISTINCT ON (title, starts_at::date) id
            FROM personal.event
            WHERE starts_at >= %s
            ORDER BY title, starts_at::date, length(notes) DESC NULLS LAST, id ASC
          )
        ORDER BY e.gcal_calendar_id, e.gcal_event_id
    """, (CUTOFF, CUTOFF, CUTOFF))
    to_delete = cur.fetchall()

    print(f"Found {len(to_delete)} duplicate events to delete")

    deleted_gcal = 0
    failed_gcal  = 0
    db_ids_to_delete = []

    for ev in to_delete:
        db_ids_to_delete.append(ev["id"])
        cal_id = ev["gcal_calendar_id"] or "primary"
        gcal_id = ev["gcal_event_id"]
        if gcal_id:
            try:
                service.events().delete(calendarId=cal_id, eventId=gcal_id).execute()
                deleted_gcal += 1
                print(f"  deleted GCal {gcal_id} ({ev['title']} on {ev['day']})")
            except HttpError as e:
                if e.resp.status == 410:
                    # Already deleted
                    deleted_gcal += 1
                    print(f"  already gone: {gcal_id}")
                else:
                    print(f"  FAILED GCal delete {gcal_id}: {e}")
                    failed_gcal += 1

    # Delete from DB
    if db_ids_to_delete:
        cur.execute(
            "DELETE FROM personal.event WHERE id = ANY(%s)",
            (db_ids_to_delete,)
        )
        deleted_db = cur.rowcount
        conn.commit()
        print(f"\nDeleted {deleted_gcal} from GCal, {deleted_db} from DB ({failed_gcal} GCal failures)")
    else:
        print("Nothing to delete")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
