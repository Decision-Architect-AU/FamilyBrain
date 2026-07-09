"""
Targeted cleanup: delete all GCal events titled exactly "Speech therapy"
that were incorrectly restored by restore_gcal_trash.py.
These are FamilyBrain-generated events that have no DB backing.

Run inside the email-sync container:
    python -m src.purge_speech_therapy
"""
import os
from googleapiclient.errors import HttpError

from .db import get_enabled_accounts
from .gmail import _calendar_service

_TARGET_TITLE = "Speech therapy"


def purge():
    accounts  = get_enabled_accounts()
    gmail_acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),
    )
    if not gmail_acct:
        print("[purge-st] no Gmail account found")
        return

    cal_svc  = _calendar_service(gmail_acct)
    cal_list = cal_svc.calendarList().list().execute()
    calendars = [c for c in cal_list.get("items", [])
                 if c.get("accessRole") in ("owner", "writer")]

    print(f"[purge-st] scanning {len(calendars)} calendar(s) for '{_TARGET_TITLE}'")
    deleted = skipped = 0

    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("summary", cal_id)
        page_token = None

        while True:
            resp = cal_svc.events().list(
                calendarId=cal_id,
                pageToken=page_token,
                q=_TARGET_TITLE,
                singleEvents=True,
                maxResults=2500,
                timeMin="2020-01-01T00:00:00Z",
                timeMax="2030-01-01T00:00:00Z",
            ).execute()

            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled":
                    continue
                summary = (ev.get("summary") or "").strip()
                if summary != _TARGET_TITLE:
                    skipped += 1
                    continue  # partial match from GCal search — skip
                ev_id = ev["id"]
                try:
                    cal_svc.events().delete(calendarId=cal_id, eventId=ev_id).execute()
                    deleted += 1
                    if deleted % 50 == 0:
                        print(f"[purge-st]   {deleted} deleted so far...")
                except HttpError as e:
                    if e.resp.status in (404, 410):
                        pass
                    else:
                        print(f"[purge-st]   error deleting {ev_id}: {e}")

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if deleted:
            print(f"[purge-st] {cal_name}: pass complete")

    print(f"[purge-st] done — {deleted} deleted, {skipped} skipped (title mismatch)")


if __name__ == "__main__":
    purge()
