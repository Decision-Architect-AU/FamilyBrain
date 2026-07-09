"""
Emergency restore: undelete all events in GCal trash that do NOT have
a FamilyBrain fb_id extended property (i.e. events we shouldn't have deleted).

Run inside the email-sync container:
    python -m src.restore_gcal_trash
"""
import os
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .db import get_enabled_accounts
from .gmail import _gmail_service

def _cal_service(acct):
    svc = _gmail_service(acct)
    return build("calendar", "v3", credentials=svc._http.credentials)


def restore():
    accounts = get_enabled_accounts()
    gmail_acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),
    )
    if not gmail_acct:
        print("[restore] no Gmail account found")
        return

    cal_svc  = _cal_service(gmail_acct)
    cal_list = cal_svc.calendarList().list().execute()
    calendars = [c for c in cal_list.get("items", [])
                 if c.get("accessRole") in ("owner", "writer")]

    print(f"[restore] scanning trash across {len(calendars)} calendar(s)")
    restored = skipped_fb = skipped_other = 0

    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("summary", cal_id)
        page_token = None

        while True:
            resp = cal_svc.events().list(
                calendarId=cal_id,
                pageToken=page_token,
                showDeleted=True,
                singleEvents=True,
                maxResults=2500,
                timeMin="2026-01-01T00:00:00Z",
                timeMax="2027-06-01T00:00:00Z",
            ).execute()

            for ev in resp.get("items", []):
                if ev.get("status") != "cancelled":
                    continue  # not deleted

                ev_id   = ev["id"]
                summary = ev.get("summary", "?")[:60]

                # Check if this is a FamilyBrain-managed event
                ext_props = ev.get("extendedProperties", {}).get("private", {})
                fb_id     = ext_props.get("fb_id", "")
                desc      = ev.get("description", "") or ""
                is_fb     = bool(fb_id) or "#familybrain" in desc

                if is_fb:
                    skipped_fb += 1
                    continue  # our event, purge was correct

                # Restore it
                try:
                    cal_svc.events().patch(
                        calendarId=cal_id, eventId=ev_id,
                        body={"status": "confirmed"},
                    ).execute()
                    restored += 1
                    print(f"[restore]   ✓ restored: {summary} ({ev_id}) [{cal_name}]")
                except HttpError as e:
                    if e.resp.status == 404:
                        skipped_other += 1
                    else:
                        print(f"[restore]   error restoring {ev_id}: {e}")

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    print(f"[restore] done — restored {restored}, skipped {skipped_fb} FB-managed, {skipped_other} not found")


if __name__ == "__main__":
    restore()
