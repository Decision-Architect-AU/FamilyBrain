"""
Delete all Google Calendar events starting on or after a cutoff date,
across all writable calendars, then clear sync state so they resync fresh.

Run with:
    docker compose run --rm email-sync python -m scripts.reset_calendar_after
"""
from datetime import datetime, timezone, date
import sys

CUTOFF_DATE = date(2026, 7, 2)   # delete events on/after this date (AEST July 2)

from src.db import get_enabled_accounts, conn
from src.gmail import _gmail_service
from googleapiclient.discovery import build


def _cal_service(account: dict):
    svc = _gmail_service(account)
    creds = svc._http.credentials
    return build("calendar", "v3", credentials=creds)


def delete_events_after(cal_svc, calendar_id: str, cal_name: str, cutoff: date) -> int:
    time_min = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc).isoformat()

    all_events = []
    page_token = None
    while True:
        kwargs = dict(calendarId=calendar_id, maxResults=2500, singleEvents=True,
                      showDeleted=False, timeMin=time_min)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = cal_svc.events().list(**kwargs).execute()
        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"[reset] {cal_name}: {len(all_events)} events on/after {cutoff}")
    deleted = 0
    for ev in all_events:
        if ev.get("status") == "cancelled":
            continue
        try:
            cal_svc.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()
            deleted += 1
        except Exception as e:
            print(f"[reset]   ERROR deleting '{ev.get('summary')}': {e}")

    return deleted


def clear_sync_state(cutoff: date):
    """
    Clear target_cal_provider_id, mirror_provider_id and calendar sync cursors
    so everything resyncs fresh. Also clears last_etag so changed-event detection resets.
    """
    with conn() as c:
        with c.cursor() as cur:
            # Clear target+mirror IDs for events on/after cutoff so they get re-written
            cur.execute("""
                UPDATE personal.calendar_sync_map
                SET target_cal_provider_id = NULL,
                    mirror_provider_id     = NULL,
                    last_etag              = NULL,
                    sync_status            = 'pending'
                WHERE event_id IN (
                    SELECT id FROM personal.event
                    WHERE effective_date >= %s
                )
            """, (cutoff,))
            rows = cur.rowcount
            print(f"[reset] cleared sync state for {rows} calendar_sync_map rows")

            # Reset calendar sync cursors so full re-delivery happens
            cur.execute("""
                UPDATE personal.email_account
                SET calendar_sync_cursor = NULL
                WHERE sync_calendar = true
            """)
            print(f"[reset] cleared calendar sync cursors for {cur.rowcount} account(s)")

        c.commit()


def main():
    accounts = get_enabled_accounts()
    gmail_accounts = [a for a in accounts if a["provider"] == "gmail"]

    total_deleted = 0
    for acct in gmail_accounts:
        print(f"\n[reset] Account: {acct['email_address']}")
        try:
            svc = _cal_service(acct)
            cal_list = svc.calendarList().list().execute()
            for cal in cal_list.get("items", []):
                if cal.get("accessRole") not in ("owner", "writer"):
                    continue
                cal_id   = cal["id"]
                cal_name = cal.get("summary", cal_id)
                n = delete_events_after(svc, cal_id, cal_name, CUTOFF_DATE)
                total_deleted += n
                if n:
                    print(f"[reset] {cal_name}: deleted {n}")
        except Exception as e:
            print(f"[reset] Failed for {acct['email_address']}: {e}")

    print(f"\n[reset] {total_deleted} events deleted from Google Calendar")
    clear_sync_state(CUTOFF_DATE)
    print("[reset] Done — restart email-sync to resync")


if __name__ == "__main__":
    main()
