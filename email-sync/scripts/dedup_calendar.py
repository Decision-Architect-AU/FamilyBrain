"""
Deduplicate Google Calendar events across all synced calendars.

Groups events by (date, normalised_title) — if two or more match, deletes all
but the one created earliest.  Only scans a rolling window (past 60 days →
future 180 days) so it runs fast even with large calendars full of recurring events.

Run with:
    docker compose run --rm email-sync python -m scripts.dedup_calendar
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import re

from src.db import get_enabled_accounts
from src.gmail import _gmail_service
from googleapiclient.discovery import build


def _cal_service(account: dict):
    svc = _gmail_service(account)
    creds = svc._http.credentials
    return build("calendar", "v3", credentials=creds)


def _normalise(summary: str) -> str:
    s = summary.strip().lower()
    s = re.sub(r"\s*\([\d:apm\s]+\)$", "", s)
    return s


def dedup_calendar(cal_svc, calendar_id: str, cal_name: str = "") -> int:
    """
    Fetch events in a rolling window, find exact duplicates by (date, title),
    delete all but the earliest-created copy. Returns number deleted.
    """
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=60)).isoformat()
    time_max = (now + timedelta(days=180)).isoformat()

    all_events: list[dict] = []
    page_token = None
    while True:
        kwargs = dict(
            calendarId=calendar_id,
            maxResults=2500,
            singleEvents=True,
            showDeleted=False,
            timeMin=time_min,
            timeMax=time_max,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        resp = cal_svc.events().list(**kwargs).execute()
        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"[dedup] {cal_name or calendar_id}: {len(all_events)} events in window")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in all_events:
        if ev.get("status") == "cancelled":
            continue
        date  = (ev.get("start") or {}).get("date") or (ev.get("start") or {}).get("dateTime", "")[:10]
        title = _normalise(ev.get("summary") or "")
        if date and title:
            groups[(date, title)].append(ev)

    deleted = 0
    for (date, title), events in groups.items():
        if len(events) <= 1:
            continue

        events.sort(key=lambda e: e.get("created", e.get("id", "")))
        keeper = events[0]
        dupes  = events[1:]

        print(f"[dedup]   '{title}' on {date}: keeping {keeper['id']}, removing {len(dupes)}")

        for ev in dupes:
            try:
                cal_svc.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()
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
        print(f"\n[dedup] Processing: {acct['email_address']}")
        try:
            svc = _cal_service(acct)
            cal_list = svc.calendarList().list().execute()
            calendars = cal_list.get("items", [])

            print(f"[dedup] {len(calendars)} calendar(s) found")
            for cal in calendars:
                cal_id   = cal["id"]
                cal_name = cal.get("summary", cal_id)
                if cal.get("accessRole") not in ("owner", "writer"):
                    continue
                try:
                    n = dedup_calendar(svc, cal_id, cal_name)
                    total_deleted += n
                    if n:
                        print(f"[dedup] {cal_name}: removed {n} duplicate(s)")
                except Exception as e:
                    print(f"[dedup] {cal_name}: ERROR — {e}")

        except Exception as e:
            print(f"[dedup] Failed for {acct['email_address']}: {e}")

    print(f"\n[dedup] Done — {total_deleted} total removed")


if __name__ == "__main__":
    main()
