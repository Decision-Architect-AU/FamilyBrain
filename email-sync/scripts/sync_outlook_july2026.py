"""
One-off: pull Outlook calendar events for July 2026 into personal.event.
Does NOT use the delta cursor — fixed date range only.
appointment_updater will then push them to GCal on next email-sync run.
"""
import os, sys
sys.path.insert(0, "/app")

import msal, requests, psycopg2, psycopg2.extras
from datetime import datetime, timezone, timedelta, date as date_type

from src import db
from src.outlook import _parse_dt, _classify_event
from src.filters import should_ingest

DB_URL        = os.environ["DATABASE_URL"]
CLIENT_ID     = os.environ["MICROSOFT_CLIENT_ID"]
TENANT_ID     = os.environ.get("MICROSOFT_TENANT_ID", "consumers")
INGESTOR_URL  = os.environ.get("INGESTOR_URL", "http://ingestor:4001")
GRAPH         = "https://graph.microsoft.com/v1.0"
SCOPES        = ["https://graph.microsoft.com/Calendars.ReadWrite"]

# AEST midnight 1 Jul → 31 Jul inclusive (add 1 day buffer each side for UTC drift)
START = "2026-06-30T14:00:00Z"   # midnight AEST 1 Jul
END   = "2026-07-31T14:00:00Z"   # midnight AEST 1 Aug


def get_token(account):
    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    )
    result = app.acquire_token_by_refresh_token(account["refresh_token"], scopes=SCOPES)
    if "access_token" not in result:
        raise RuntimeError(f"MSAL failed: {result.get('error_description')}")
    return result["access_token"]


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM personal.email_account WHERE provider='outlook' AND enabled=true")
        accounts = cur.fetchall()
    conn.close()

    for acct in accounts:
        print(f"\nSyncing July 2026 from {acct['email_address']}")
        token = get_token(acct)
        h = {"Authorization": f"Bearer {token}"}

        url = (
            f"{GRAPH}/me/calendarView"
            f"?startDateTime={START}&endDateTime={END}"
            f"&$select=id,subject,start,end,body,isAllDay&$top=100"
        )

        synced = 0
        while url:
            resp = requests.get(url, headers=h, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for ev in data.get("value", []):
                provider_id = ev["id"]
                summary     = ev.get("subject", "(no title)")
                is_all_day  = ev.get("isAllDay", False)
                description = ev.get("body", {}).get("content", "")[:500]

                if is_all_day:
                    raw_start = ev.get("start", {}).get("dateTime", "")[:10]
                    raw_end   = ev.get("end",   {}).get("dateTime", "")[:10]
                    starts_at = date_type.fromisoformat(raw_start) if raw_start else None
                    ends_at   = date_type.fromisoformat(raw_end)   if raw_end   else None
                else:
                    starts_at = _parse_dt(ev.get("start", {}).get("dateTime"), ev.get("start", {}).get("timeZone"))
                    ends_at   = _parse_dt(ev.get("end",   {}).get("dateTime"), ev.get("end",   {}).get("timeZone"))

                if not starts_at:
                    continue

                cal_key    = f"outlook:{acct['email_address']}:{provider_id}"
                event_type = _classify_event(summary)

                event_id = db.upsert_event(
                    title=summary,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    event_type=event_type,
                    calendar_source=f"outlook:{acct['email_address']}",
                    calendar_event_id=cal_key,
                    notes=description,
                    ingestor_url=INGESTOR_URL,
                )
                print(f"  [{starts_at}] {summary} → event {event_id}")
                synced += 1

            url = data.get("@odata.nextLink")

        print(f"  → {synced} events upserted")

    print("\nDone. Start email-sync to push to GCal.")


if __name__ == "__main__":
    main()
