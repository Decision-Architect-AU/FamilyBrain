"""
One-off script: find and delete GCal events that our system once pushed
but whose gcal_event_id is no longer in personal.event (orphaned).

Run inside the email-sync container:
    python -m src.purge_orphan_gcal
"""
import os
import psycopg2
import psycopg2.extras
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .db import get_enabled_accounts
from .gmail import _gmail_service

DB_URL = os.environ["DATABASE_URL"]

# Calendars our system writes to — match calendar_router logic
# We'll enumerate all calendars on the primary account and check each
def _cal_service(acct):
    svc = _gmail_service(acct)
    return build("calendar", "v3", credentials=svc._http.credentials)


def _load_known_gcal_ids() -> set:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT gcal_event_id FROM personal.event WHERE gcal_event_id IS NOT NULL")
            return {r["gcal_event_id"] for r in cur.fetchall()}


def purge_orphans():
    accounts = get_enabled_accounts()
    gmail_acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),
    )
    if not gmail_acct:
        print("[purge] no Gmail account found")
        return

    cal_svc = _cal_service(gmail_acct)
    known_ids = _load_known_gcal_ids()
    print(f"[purge] {len(known_ids)} gcal_event_id(s) currently in DB")

    # List all calendars the user has
    cal_list = cal_svc.calendarList().list().execute()
    calendars = cal_list.get("items", [])
    print(f"[purge] scanning {len(calendars)} calendar(s)")

    total_deleted = 0
    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("summary", cal_id)

        # Only scan calendars we own (not read-only subscriptions)
        access = cal.get("accessRole", "")
        if access not in ("owner", "writer"):
            print(f"[purge]   skip read-only calendar: {cal_name}")
            continue

        page_token = None
        cal_deleted = 0
        while True:
            resp = cal_svc.events().list(
                calendarId=cal_id,
                pageToken=page_token,
                singleEvents=True,
                maxResults=2500,
                timeMin="2026-01-01T00:00:00Z",
                timeMax="2027-01-01T00:00:00Z",
            ).execute()

            for ev in resp.get("items", []):
                ev_id = ev["id"]
                if ev_id not in known_ids:
                    # Only delete events that FamilyBrain itself wrote — identified by
                    # either the fb_id extended property OR the #familybrain description tag.
                    # Never touch events the user created manually, even if we're the creator.
                    ext_props = ev.get("extendedProperties", {}).get("private", {})
                    fb_id     = ext_props.get("fb_id", "")
                    desc      = ev.get("description", "") or ""
                    is_fb     = bool(fb_id) or "#familybrain" in desc

                    if not is_fb:
                        continue

                    try:
                        cal_svc.events().delete(calendarId=cal_id, eventId=ev_id).execute()
                        cal_deleted += 1
                        total_deleted += 1
                        print(f"[purge]   deleted orphan: {ev.get('summary', '?')[:60]} ({ev_id})")
                    except HttpError as e:
                        if e.resp.status in (404, 410):
                            pass  # already gone
                        else:
                            print(f"[purge]   error deleting {ev_id}: {e}")

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if cal_deleted:
            print(f"[purge] {cal_name}: deleted {cal_deleted} orphan event(s)")

    print(f"[purge] done — {total_deleted} orphan GCal event(s) deleted")


if __name__ == "__main__":
    purge_orphans()
