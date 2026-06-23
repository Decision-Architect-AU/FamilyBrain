"""
Delete all Outlook calendar events starting on or after 2026-07-01
via Microsoft Graph API.
"""
import os, sys, time
sys.path.insert(0, "/app")

import psycopg2, psycopg2.extras
import msal
import requests
from datetime import datetime, timezone, timedelta

DB_URL        = os.environ["DATABASE_URL"]
CLIENT_ID     = os.environ["MICROSOFT_CLIENT_ID"]
CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
TENANT_ID     = os.environ.get("MICROSOFT_TENANT_ID", "consumers")
GRAPH_BASE    = "https://graph.microsoft.com/v1.0"
CUTOFF        = "2026-06-30T14:00:00Z"  # midnight AEST (UTC+10)

SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Calendars.ReadWrite",
]


def get_token(account: dict) -> str:
    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    )
    result = app.acquire_token_by_refresh_token(
        refresh_token=account["refresh_token"],
        scopes=SCOPES,
    )
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token refresh failed: {result.get('error_description')}")
    return result["access_token"]


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def delete_future_outlook_events(account: dict) -> int:
    token = get_token(account)
    h = headers(token)
    deleted = 0
    skip = 0
    page_size = 100

    url = (
        f"{GRAPH_BASE}/me/calendarView"
        f"?startDateTime={CUTOFF}&endDateTime=2030-01-01T00:00:00Z"
        f"&$select=id,subject,start&$top={page_size}"
    )

    while url:
        for attempt in range(5):
            try:
                resp = requests.get(url, headers=h, timeout=30)
                if resp.status_code == 401:
                    token = get_token(account)
                    h = headers(token)
                    resp = requests.get(url, headers=h, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"  list error (attempt {attempt+1}): {e} — retrying")
                time.sleep(3 * (attempt + 1))

        items = data.get("value", [])
        for ev in items:
            ev_id   = ev["id"]
            subject = ev.get("subject", "")
            for attempt in range(5):
                try:
                    r = requests.delete(f"{GRAPH_BASE}/me/events/{ev_id}", headers=h, timeout=15)
                    if r.status_code in (204, 404, 410):
                        deleted += 1
                        break
                    r.raise_for_status()
                    deleted += 1
                    break
                except Exception as e:
                    if attempt == 4:
                        print(f"  failed to delete '{subject}': {e}")
                        skip += 1
                        break
                    time.sleep(2 * (attempt + 1))

        if deleted % 50 == 0 and deleted > 0:
            print(f"  {deleted} deleted so far")

        url = data.get("@odata.nextLink")

    return deleted, skip


def main():
    conn = psycopg2.connect(DB_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM personal.email_account WHERE provider='outlook' AND enabled=true"
        )
        accounts = cur.fetchall()
    conn.close()

    if not accounts:
        print("No enabled Outlook accounts found.")
        return

    total_deleted = 0
    for acct in accounts:
        print(f"\nProcessing: {acct['email_address']}")
        deleted, skipped = delete_future_outlook_events(acct)
        print(f"  → {deleted} deleted, {skipped} failed")
        total_deleted += deleted

    print(f"\nTotal Outlook events deleted: {total_deleted}")
    print("Done.")


if __name__ == "__main__":
    main()
