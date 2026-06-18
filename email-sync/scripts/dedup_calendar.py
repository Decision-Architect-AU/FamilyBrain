"""
One-time script: deduplicate all Google Calendar events across all synced calendars.

Groups events by (date, normalised_title) and deletes extras, keeping the one
that was created first (lowest event ID sort). Logs every deletion.

Run with:
    docker compose run --rm email-sync python -m scripts.dedup_calendar
"""
import os
import sys
from collections import defaultdict

from src.db import get_enabled_accounts
from src.gmail import _gmail_service
from googleapiclient.discovery import build


def _cal_service(account: dict):
    svc = _gmail_service(account)
    creds = svc._http.credentials
    return build("calendar", "v3", credentials=creds)


def _normalise(summary: str) -> str:
    """Strip trailing numbers/times that might differ between copies."""
    import re
    s = summary.strip().lower()
    # Remove trailing parenthetical time like "(10:00 AM)"
    s = re.sub(r"\s*\([\d:apm\s]+\)$", "", s)
    return s


def dedup_calendar(cal_svc, calendar_id: str, cal_name: str = "") -> int:
    """
    Fetch all events from a calendar, find duplicates by (date, title),
    delete all but the earliest-created copy. Returns number deleted.
    """
    all_events: list[dict] = []
    page_token = None
    while True:
        kwargs = dict(calendarId=calendar_id, maxResults=2500, singleEvents=True,
                      showDeleted=False)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = cal_svc.events().list(**kwargs).execute()
        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"[dedup] {cal_name or calendar_id}: {len(all_events)} events total")

    # Group by (date, normalised_title)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in all_events:
        if ev.get("status") == "cancelled":
            continue
        date    = (ev.get("start") or {}).get("date") or (ev.get("start") or {}).get("dateTime", "")[:10]
        title   = _normalise(ev.get("summary") or "")
        if date and title:
            groups[(date, title)].append(ev)

    deleted = 0
    for (date, title), events in groups.items():
        if len(events) <= 1:
            continue

        # Keep the one created earliest (or first in list if no created time)
        events.sort(key=lambda e: e.get("created", e.get("id", "")))
        keeper = events[0]
        dupes  = events[1:]

        print(f"[dedup]   '{title}' on {date}: keeping {keeper['id']}, "
              f"removing {len(dupes)} duplicate(s)")

        for ev in dupes:
            try:
                cal_svc.events().delete(
                    calendarId=calendar_id, eventId=ev["id"]
                ).execute()
                deleted += 1
            except Exception as e:
                print(f"[dedup]   ERROR deleting {ev['id']}: {e}")

    return deleted


def main():
    accounts = get_enabled_accounts()
    gmail_accounts = [a for a in accounts if a["provider"] == "gmail"]

    if not gmail_accounts:
        print("[dedup] No Gmail accounts found")
        return

    total_deleted = 0
    for acct in gmail_accounts:
        print(f"\n[dedup] Processing account: {acct['email_address']}")
        try:
            svc = _cal_service(acct)

            # Get all calendars for this account
            cal_list = svc.calendarList().list().execute()
            calendars = cal_list.get("items", [])

            print(f"[dedup] Found {len(calendars)} calendar(s)")
            for cal in calendars:
                cal_id   = cal["id"]
                cal_name = cal.get("summary", cal_id)
                access   = cal.get("accessRole", "")
                # Only dedup calendars we own/can write to
                if access not in ("owner", "writer"):
                    print(f"[dedup] Skipping read-only calendar: {cal_name}")
                    continue
                try:
                    n = dedup_calendar(svc, cal_id, cal_name)
                    total_deleted += n
                    if n:
                        print(f"[dedup] {cal_name}: {n} duplicate(s) removed")
                except Exception as e:
                    print(f"[dedup] {cal_name}: ERROR — {e}")

        except Exception as e:
            print(f"[dedup] Failed for {acct['email_address']}: {e}")

    print(f"\n[dedup] Done — {total_deleted} total duplicate(s) removed across all calendars")


if __name__ == "__main__":
    main()
