"""
email-sync main loop.

Runs two sync passes on a schedule:
  - Email:    every EMAIL_POLL_INTERVAL_SECS (default 300 = 5 min)
  - Calendar: every CALENDAR_POLL_INTERVAL_SECS (default 900 = 15 min)

All account configs come from personal.email_account (DB).
No hardcoded accounts — add rows to personal.email_account to connect inboxes.
"""
import os
import time
import threading
from datetime import datetime, timezone

from .db import get_enabled_accounts
from . import gmail as gmail_mod
from . import outlook as outlook_mod
from . import financial_processor as fin_mod
from . import bill_calendar as billcal_mod

INGESTOR_URL            = os.environ.get("INGESTOR_URL", "http://ingestor:4001")
EMAIL_POLL_INTERVAL     = int(os.environ.get("EMAIL_POLL_INTERVAL_SECS", "300"))    # 5 min
CALENDAR_POLL_INTERVAL  = int(os.environ.get("CALENDAR_POLL_INTERVAL_SECS", "900")) # 15 min


def run_email_sync() -> None:
    accounts = get_enabled_accounts()
    gmail_accounts   = [a for a in accounts if a["provider"] == "gmail"   and a.get("sync_email")]
    outlook_accounts = [a for a in accounts if a["provider"] == "outlook" and a.get("sync_email")]

    total = 0
    for acct in gmail_accounts:
        print(f"[email-sync] Gmail email: {acct['email_address']}")
        n = gmail_mod.sync_email(acct, INGESTOR_URL)
        print(f"[email-sync]   → {n} messages ingested")
        total += n

    for acct in outlook_accounts:
        print(f"[email-sync] Outlook email: {acct['email_address']}")
        n = outlook_mod.sync_email(acct, INGESTOR_URL)
        print(f"[email-sync]   → {n} messages ingested")
        total += n

    print(f"[email-sync] Email pass complete — {total} total messages ingested")

    # File financial documents and schedule bill calendar events
    try:
        all_accounts = get_enabled_accounts()
        n = fin_mod.process_financial_emails(all_accounts)
        if n:
            print(f"[email-sync] Financial processor saved {n} document(s)")
        billcal_mod.sync_bill_calendar(all_accounts)
        billcal_mod.enrich_bill_calendar(all_accounts)
    except Exception as e:
        print(f"[email-sync] Financial processor error: {e}")


def run_calendar_sync() -> None:
    accounts = get_enabled_accounts()
    gmail_accounts   = [a for a in accounts if a["provider"] == "gmail"   and a.get("sync_calendar")]
    outlook_accounts = [a for a in accounts if a["provider"] == "outlook" and a.get("sync_calendar")]

    total = 0

    # Sync Gmail → personal.event, mirror to Outlook
    for acct in gmail_accounts:
        print(f"[email-sync] Gmail calendar: {acct['email_address']}")
        n = gmail_mod.sync_calendar(acct, mirror_accounts=outlook_accounts, ingestor_url=INGESTOR_URL)
        print(f"[email-sync]   → {n} events synced")
        total += n

    # Sync Outlook → personal.event, mirror to Gmail
    for acct in outlook_accounts:
        print(f"[email-sync] Outlook calendar: {acct['email_address']}")
        n = outlook_mod.sync_calendar(acct, mirror_accounts=gmail_accounts, ingestor_url=INGESTOR_URL)
        print(f"[email-sync]   → {n} events synced")
        total += n

    print(f"[email-sync] Calendar pass complete — {total} total events synced")


def email_loop() -> None:
    while True:
        try:
            run_email_sync()
        except Exception as e:
            print(f"[email-sync] Email loop error: {e}")
        time.sleep(EMAIL_POLL_INTERVAL)


def calendar_loop() -> None:
    while True:
        try:
            run_calendar_sync()
        except Exception as e:
            print(f"[email-sync] Calendar loop error: {e}")
        time.sleep(CALENDAR_POLL_INTERVAL)


if __name__ == "__main__":
    print(f"[email-sync] Starting — email every {EMAIL_POLL_INTERVAL}s, calendar every {CALENDAR_POLL_INTERVAL}s")
    print(f"[email-sync] Ingestor: {INGESTOR_URL}")

    # Run first pass immediately on startup
    try:
        run_email_sync()
    except Exception as e:
        print(f"[email-sync] Initial email sync error: {e}")

    try:
        run_calendar_sync()
    except Exception as e:
        print(f"[email-sync] Initial calendar sync error: {e}")

    # Start background loops
    t_email = threading.Thread(target=email_loop, daemon=True, name="email-loop")
    t_cal   = threading.Thread(target=calendar_loop, daemon=True, name="calendar-loop")
    t_email.start()
    t_cal.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[email-sync] Shutting down")
