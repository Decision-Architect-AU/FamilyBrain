"""
Bulk cleanup: delete all GCal events belonging to recurring series that were
incorrectly restored by restore_gcal_trash.py.

Pass --dry-run to see what would be deleted without deleting anything.
Pass --discover YYYY-MM-DD to list all events on a date (find unknown series titles).

Run inside the email-sync container:
    python -m src.purge_restored_series
    python -m src.purge_restored_series --dry-run
    python -m src.purge_restored_series --discover 2026-07-27
"""
import sys
from googleapiclient.errors import HttpError

from .db import get_enabled_accounts
from .gmail import _calendar_service

# Exact titles to purge (case-sensitive).
# FamilyBrain-generated events (Olivia Speech Therapy, etc.) are distinct — safe.
_PURGE_TITLES = {
    "Speech therapy",
    "Music Ensemble",
    "music ensemble",
    "Elliana Music Ensemble",
    "Ellie Music Ensemble",
    "Ellies music ensemble",
    "Physio",
    "Physiotherapy",
    "Physio appointment",
}


def _get_cal_svc():
    accounts = get_enabled_accounts()
    acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),
    )
    if not acct:
        raise RuntimeError("no Gmail account found")
    return _calendar_service(acct)


def discover(date_str: str):
    """List all events on a given date so we can identify flooding series titles."""
    cal_svc = _get_cal_svc()
    cal_list = cal_svc.calendarList().list().execute()
    calendars = [c for c in cal_list.get("items", [])
                 if c.get("accessRole") in ("owner", "writer")]

    # Search ±1 day in UTC to capture AEST events (UTC+10)
    from datetime import date, timedelta
    d = date.fromisoformat(date_str)
    time_min = f"{d - timedelta(days=1)}T12:00:00Z"
    time_max = f"{d + timedelta(days=1)}T12:00:00Z"
    print(f"\n[discover] events on {date_str}:")

    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("summary", cal_id)
        resp = cal_svc.events().list(
            calendarId=cal_id,
            singleEvents=True,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=500,
        ).execute()
        from collections import Counter
        titles = Counter((e.get("summary") or "?") for e in resp.get("items", [])
                         if e.get("status") != "cancelled")
        for title, count in titles.most_common():
            print(f"  [{cal_name}] x{count}  '{title}'")


def purge(dry_run: bool = False):
    cal_svc = _get_cal_svc()
    cal_list = cal_svc.calendarList().list().execute()
    calendars = [c for c in cal_list.get("items", [])
                 if c.get("accessRole") in ("owner", "writer")]

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"[purge-series] {mode} — scanning {len(calendars)} calendar(s)")
    print(f"[purge-series] target titles: {sorted(_PURGE_TITLES)}")

    deleted = skipped = 0

    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("summary", cal_id)
        page_token = None

        while True:
            resp = cal_svc.events().list(
                calendarId=cal_id,
                pageToken=page_token,
                singleEvents=True,
                maxResults=2500,
                timeMin="2026-06-01T00:00:00Z",
                timeMax="2030-01-01T00:00:00Z",
            ).execute()

            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled":
                    continue

                summary = (ev.get("summary") or "").strip()
                if summary not in _PURGE_TITLES:
                    skipped += 1
                    continue

                ev_id = ev["id"]

                if dry_run:
                    start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", "?")
                    print(f"[purge-series]  would delete '{summary}' {start[:10]} [{cal_name}]")
                    deleted += 1
                    continue

                for attempt in range(2):
                    try:
                        cal_svc.events().delete(calendarId=cal_id, eventId=ev_id).execute()
                        deleted += 1
                        if deleted % 100 == 0:
                            print(f"[purge-series]  {deleted} deleted...")
                        break
                    except HttpError as e:
                        if e.resp.status not in (404, 410):
                            print(f"[purge-series]   error {ev_id}: {e}")
                        break
                    except Exception:
                        if attempt == 0:
                            # Refresh credentials and retry once
                            cal_svc = _get_cal_svc()
                        else:
                            raise

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    verb = "would delete" if dry_run else "deleted"
    print(f"\n[purge-series] done — {verb} {deleted} event(s)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--discover" in args:
        idx = args.index("--discover")
        date = args[idx + 1] if idx + 1 < len(args) else None
        if not date:
            print("usage: --discover YYYY-MM-DD")
            sys.exit(1)
        discover(date)
    else:
        purge(dry_run="--dry-run" in args)
