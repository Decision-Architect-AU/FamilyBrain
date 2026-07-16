"""
email-sync main loop.

Runs two sync passes on a schedule:
  - Email:    every EMAIL_POLL_INTERVAL_SECS (default 300 = 5 min)
  - Calendar: every CALENDAR_POLL_INTERVAL_SECS (default 900 = 15 min)

Architecture: calendar/email → graph (enriched + deduped) → appointment_updater → GCal.
GCal is the write output. Calendar sources (Gmail, Outlook) feed the graph as inputs,
not mirrors. Enrichment (person names, locations) and dedup happen before GCal write.

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
from . import email_decomposer as decompose_mod
from . import appointment_updater as appt_mod

INGESTOR_URL            = os.environ.get("INGESTOR_URL", "http://ingestor:4001")
EMAIL_POLL_INTERVAL     = int(os.environ.get("EMAIL_POLL_INTERVAL_SECS", "300"))    # 5 min
CALENDAR_POLL_INTERVAL  = int(os.environ.get("CALENDAR_POLL_INTERVAL_SECS", "900")) # 15 min
FINANCIAL_POLL_INTERVAL = int(os.environ.get("FINANCIAL_POLL_INTERVAL_SECS", "300")) # 5 min


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

    all_accounts = get_enabled_accounts()

    # Decompose emails into typed items (calendar events, payments, tasks, observations)
    try:
        n = decompose_mod.decompose_emails(all_accounts)
        if n:
            print(f"[email-sync] Decomposed {n} email(s)")
    except Exception as e:
        print(f"[email-sync] Email decomposer error: {e}")

    # Push pending/changed events to Google Calendar
    try:
        n = appt_mod.run_appointment_updater(all_accounts)
        if n:
            print(f"[email-sync] Appointment updater wrote {n} event(s) to GCal")
    except Exception as e:
        print(f"[email-sync] Appointment updater error: {e}")


def run_calendar_sync() -> None:
    accounts = get_enabled_accounts()
    gmail_accounts   = [a for a in accounts if a["provider"] == "gmail"  and a.get("sync_calendar")]
    outlook_accounts = [a for a in accounts if a["provider"] == "outlook" and a.get("sync_calendar")]
    all_accounts     = accounts

    for acct in gmail_accounts:
        try:
            mirrors = [a for a in all_accounts if a["id"] != acct["id"]]
            n = gmail_mod.sync_calendar(acct, mirrors, INGESTOR_URL)
            if n:
                print(f"[cal-sync] Gmail {acct['email_address']}: {n} event(s)")
        except Exception as e:
            print(f"[cal-sync] Gmail calendar error for {acct['email_address']}: {e}")

    for acct in outlook_accounts:
        try:
            mirrors = [a for a in all_accounts if a["id"] != acct["id"]]
            n = outlook_mod.sync_calendar(acct, mirrors, INGESTOR_URL)
            if n:
                print(f"[cal-sync] Outlook {acct['email_address']}: {n} event(s)")
        except Exception as e:
            print(f"[cal-sync] Outlook calendar error for {acct['email_address']}: {e}")


def email_loop() -> None:
    while True:
        try:
            run_email_sync()
        except Exception as e:
            print(f"[email-sync] Email loop error: {e}")
        time.sleep(EMAIL_POLL_INTERVAL)


def run_financial_sync() -> None:
    accounts = get_enabled_accounts()
    try:
        n = fin_mod.process_financial_emails(accounts)
        if n:
            print(f"[email-sync] Financial processor saved {n} document(s)")
        billcal_mod.sync_bill_calendar(accounts)
        billcal_mod.enrich_bill_calendar(accounts)
    except Exception as e:
        print(f"[email-sync] Financial processor error: {e}")


def calendar_loop() -> None:
    while True:
        try:
            run_calendar_sync()
        except Exception as e:
            print(f"[email-sync] Calendar loop error: {e}")
        time.sleep(CALENDAR_POLL_INTERVAL)


def financial_loop() -> None:
    # Stagger by half the email interval so LLM calls don't overlap with decompose_emails
    time.sleep(EMAIL_POLL_INTERVAL // 2)
    while True:
        try:
            run_financial_sync()
        except Exception as e:
            print(f"[email-sync] Financial loop error: {e}")
        time.sleep(FINANCIAL_POLL_INTERVAL)


if __name__ == "__main__":
    print(f"[email-sync] Starting — email every {EMAIL_POLL_INTERVAL}s, calendar every {CALENDAR_POLL_INTERVAL}s, financial every {FINANCIAL_POLL_INTERVAL}s")
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
    t_email = threading.Thread(target=email_loop,     daemon=True, name="email-loop")
    t_cal   = threading.Thread(target=calendar_loop,  daemon=True, name="calendar-loop")
    t_fin   = threading.Thread(target=financial_loop, daemon=True, name="financial-loop")
    t_email.start()
    t_cal.start()
    t_fin.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[email-sync] Shutting down")
