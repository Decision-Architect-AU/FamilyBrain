"""
One-off: delete all Outlook calendar events from 2026-06-25 forward,
except the 1pm strategy appointment on 2026-07-02.

Run inside the email-sync container:
    python -m src.purge_outlook_calendar
"""
import os
import requests
from datetime import datetime, timezone

from .db import get_enabled_accounts
from .outlook import _token, _headers, GRAPH_BASE


def purge():
    accounts = get_enabled_accounts()
    outlook_acct = next((a for a in accounts if a["provider"] == "outlook"), None)
    if not outlook_acct:
        print("[purge-ol] no Outlook account found")
        return

    # Fetch all calendars
    r = requests.get(f"{GRAPH_BASE}/me/calendars", headers=_headers(outlook_acct), timeout=30)
    r.raise_for_status()
    calendars = r.json().get("value", [])
    print(f"[purge-ol] found {len(calendars)} calendar(s)")

    keep_count  = 0
    delete_count = 0
    error_count  = 0

    for cal in calendars:
        cal_id   = cal["id"]
        cal_name = cal.get("name", cal_id)

        url = (
            f"{GRAPH_BASE}/me/calendars/{cal_id}/calendarView"
            f"?startDateTime=2026-06-25T00:00:00Z"
            f"&endDateTime=2027-01-01T00:00:00Z"
            f"&$top=100"
            f"&$select=id,subject,start,end,isOrganizer"
        )

        while url:
            r = requests.get(url, headers=_headers(outlook_acct), timeout=30)
            r.raise_for_status()
            data = r.json()

            for ev in data.get("value", []):
                ev_id   = ev["id"]
                subject = ev.get("subject", "")
                start   = ev.get("start", {}).get("dateTime", "")

                # Parse start time to AEST for comparison
                try:
                    # Graph returns UTC or local depending on timezone field
                    tz_str = ev.get("start", {}).get("timeZone", "UTC")
                    if tz_str == "UTC":
                        dt_utc = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    else:
                        import pytz
                        local_tz = pytz.timezone(tz_str)
                        dt_naive = datetime.fromisoformat(start)
                        dt_utc = local_tz.localize(dt_naive).astimezone(timezone.utc)

                    import pytz
                    aest = pytz.timezone("Australia/Brisbane")
                    dt_aest = dt_utc.astimezone(aest)
                except Exception:
                    dt_aest = None

                # Keep: strategy appointment today at 1pm AEST
                is_keeper = (
                    dt_aest is not None
                    and dt_aest.date().isoformat() == "2026-07-02"
                    and dt_aest.hour == 13
                )

                if is_keeper:
                    print(f"[purge-ol]   KEEP: {subject[:60]} @ {dt_aest}")
                    keep_count += 1
                    continue

                del_url = f"{GRAPH_BASE}/me/events/{ev_id}"
                dr = requests.delete(del_url, headers=_headers(outlook_acct), timeout=30)
                if dr.status_code in (204, 404):
                    delete_count += 1
                    print(f"[purge-ol]   deleted: {subject[:60]}")
                else:
                    error_count += 1
                    print(f"[purge-ol]   error {dr.status_code} deleting: {subject[:60]}")

            url = data.get("@odata.nextLink")

    print(f"[purge-ol] done — deleted {delete_count}, kept {keep_count}, errors {error_count}")


if __name__ == "__main__":
    purge()
