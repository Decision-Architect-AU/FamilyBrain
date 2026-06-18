"""
Bill calendar sync.

For each financial document note that hasn't been scheduled yet, extract
payment details (payee, amount, due date) via LLM and create an all-day
event in the Gmail Bills calendar.

Triggered after financial_processor runs.
"""
import json
import os
import re
import psycopg2
import psycopg2.extras
import requests as req

from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build

DB_URL      = os.environ["DATABASE_URL"]
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:3b")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cal_service(gmail_account: dict):
    """Build a Google Calendar service from the Gmail account credentials."""
    from src.gmail import _gmail_service
    gmail_svc = _gmail_service(gmail_account)
    creds = gmail_svc._http.credentials
    return build("calendar", "v3", credentials=creds)


_STATEMENT_SUBJECT_KW = [
    "rental income statement", "owner statement", "ownership statement",
    "income statement", "rental statement", "disbursement statement",
    "property statement", "owner's statement",
]


def _is_statement_email(subject: str) -> bool:
    """True when the email is an owner/rental statement — expenses are already settled."""
    s = subject.lower()
    return any(kw in s for kw in _STATEMENT_SUBJECT_KW)


def _extract_payment_details(subject: str, body: str, received_date: str) -> dict:
    """
    Use LLM to extract full payment details from a financial document.
    Falls back to safe defaults if extraction fails.
    """
    prompt = (
        "Extract payment details from this financial document. "
        "Reply with ONLY a valid JSON object — no prose, no markdown, no explanation.\n\n"
        f"Subject: {subject}\n"
        f"Document text (first 2000 chars):\n{body[:2000]}\n\n"
        "Return JSON with exactly these fields:\n"
        '  "biller": string — the company or person sending the bill/invoice,\n'
        '  "amount_due": string — the total amount to pay (e.g. "$1,234.56"),\n'
        '  "due_date": string — payment due date in YYYY-MM-DD format (use invoice date if no due date found, null if unknown),\n'
        '  "for_what": string — what property address, person name, or asset this bill relates to (e.g. "14 Nandina Ct Strathdale VIC" or "Olivia West"),\n'
        '  "invoice_ref": string — invoice number or reference (e.g. "INV-2456" or "OWN06368"),\n'
        '  "how_to_pay": string — payment instructions: BSB, account number, BPAY biller code, reference, or payment link. Leave empty string if not found,\n'
        '  "payment_status": string — EXACTLY "pending" (standalone invoice needing payment) or '
        '"paid_via_statement" (expense already deducted in an owner/rental statement).\n\n'
        "If a due date is not found, use the invoice/statement date. "
        "Keep each value concise — one line each."
    )
    try:
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        raw = resp.json().get("response", "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            # Validate / normalise due_date
            due = data.get("due_date")
            if due:
                try:
                    datetime.strptime(str(due), "%Y-%m-%d")
                except (ValueError, TypeError):
                    due = None
            if not due:
                try:
                    due = (datetime.fromisoformat(received_date[:10]) + timedelta(days=14)).strftime("%Y-%m-%d")
                except Exception:
                    due = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
            data["due_date"] = due
            # Subject-level override: statement emails always = already settled
            if _is_statement_email(subject):
                data["payment_status"] = "paid_via_statement"
            return data
    except Exception as e:
        print(f"[billcal] LLM extract failed: {e}")

    # Safe fallback
    try:
        fallback_date = (datetime.fromisoformat(received_date[:10]) + timedelta(days=14)).strftime("%Y-%m-%d")
    except Exception:
        fallback_date = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
    return {
        "biller": subject[:60],
        "amount_due": "",
        "due_date": fallback_date,
        "for_what": "",
        "invoice_ref": "",
        "how_to_pay": "",
        "payment_status": "paid_via_statement" if _is_statement_email(subject) else "pending",
    }


def _create_event(cal_svc, calendar_id: str, details: dict,
                  entity_tag: str, note_id: int) -> str:
    """Create an all-day event in the Bills calendar. Returns event id."""
    biller      = details.get("biller") or "Unknown"
    amount      = details.get("amount_due") or ""
    date        = details["due_date"]
    for_what    = details.get("for_what") or ""
    invoice_ref = details.get("invoice_ref") or ""
    how_to_pay  = details.get("how_to_pay") or ""
    entity      = entity_tag.strip("{}")
    is_paid     = details.get("payment_status") == "paid_via_statement"

    # Title prefix and color differ for paid-via-statement vs outstanding
    prefix   = "✓ PAID (stmt):" if is_paid else "PAY:"
    color_id = "2"  # Sage green for already-paid items
    if not is_paid:
        color_id = "11"  # Tomato for outstanding bills

    title = f"{prefix} {biller}"
    if amount:
        title += f" — {amount}"

    lines = []
    if for_what:
        lines.append(f"For: {for_what}")
    if invoice_ref:
        lines.append(f"Ref: {invoice_ref}")
    if amount:
        lines.append(f"Amount: {amount}")
    if is_paid:
        lines.append("Status: Paid — deducted via owner/rental statement")
    elif how_to_pay:
        lines.append(f"How to pay: {how_to_pay}")
    lines.append(f"Entity: {entity}")

    event = {
        "summary": title,
        "description": "\n".join(lines),
        "start": {"date": date},
        "end":   {"date": date},
        "colorId": color_id,
    }
    result = cal_svc.events().insert(calendarId=calendar_id, body=event).execute()
    return result["id"]


# ── Enrich existing events ───────────────────────────────────────────────────

def enrich_bill_calendar(accounts: list[dict]) -> int:
    """
    Re-run LLM extraction on notes that already have a bill_event_id but were
    created without enrichment (amount_due is blank in the event summary).
    Updates the Google Calendar event in-place.
    Returns number of events updated.
    """
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    if not gmail_acct:
        return 0

    bills_cal_id = gmail_acct.get("bills_calendar_id")
    if not bills_cal_id:
        return 0

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.id, n.body, n.tags, n.created_at,
                          n.bill_event_id,
                          em.subject, em.received_at
                   FROM   personal.note n
                   LEFT   JOIN personal.email_message em ON em.note_id = n.id
                   WHERE  n.source = 'financial_doc'
                     AND  n.bill_event_id IS NOT NULL
                     AND  n.bill_event_enriched IS NOT TRUE
                   ORDER  BY n.id"""
            )
            notes = list(cur.fetchall())

    if not notes:
        print("[billcal] no events to enrich")
        return 0

    print(f"[billcal] enriching {len(notes)} existing event(s)")

    try:
        cal_svc = _cal_service(gmail_acct)
    except Exception as e:
        print(f"[billcal] calendar service failed: {e}")
        return 0

    updated = 0
    for note in notes:
        note_id    = note["id"]
        event_id   = note["bill_event_id"]
        body       = note["body"] or ""
        tags       = note["tags"] or []
        entity_tag = tags[0] if tags else "Personal"
        received_at = str(note["received_at"] or note["created_at"] or "")
        subject    = note["subject"] or body.split("\n")[0][:80]

        try:
            details = _extract_payment_details(subject, body, received_at)

            biller      = details.get("biller") or "Unknown"
            amount      = details.get("amount_due") or ""
            date        = details["due_date"]
            for_what    = details.get("for_what") or ""
            invoice_ref = details.get("invoice_ref") or ""
            how_to_pay  = details.get("how_to_pay") or ""
            entity      = entity_tag.strip("{}")
            is_paid     = details.get("payment_status") == "paid_via_statement"

            prefix   = "✓ PAID (stmt):" if is_paid else "PAY:"
            color_id = "2" if is_paid else "11"
            title    = f"{prefix} {biller}"
            if amount:
                title += f" — {amount}"

            lines = []
            if for_what:
                lines.append(f"For: {for_what}")
            if invoice_ref:
                lines.append(f"Ref: {invoice_ref}")
            if amount:
                lines.append(f"Amount: {amount}")
            if is_paid:
                lines.append("Status: Paid — deducted via owner/rental statement")
            elif how_to_pay:
                lines.append(f"How to pay: {how_to_pay}")
            lines.append(f"Entity: {entity}")

            patch = {
                "summary": title,
                "description": "\n".join(lines),
                "start": {"date": date},
                "end":   {"date": date},
                "colorId": color_id,
            }
            cal_svc.events().patch(
                calendarId=bills_cal_id, eventId=event_id, body=patch
            ).execute()

            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.note SET bill_event_enriched = true WHERE id = %s",
                        (note_id,),
                    )
                conn.commit()

            print(f"[billcal] enriched '{title}' [{entity}]")
            updated += 1
        except Exception as e:
            print(f"[billcal] enrich failed for note {note_id}: {e}")

    return updated


# ── Main entry ────────────────────────────────────────────────────────────────

def sync_bill_calendar(accounts: list[dict]) -> int:
    """
    Create Bills calendar events for financial notes that don't have one yet.
    Returns number of events created.
    """
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    if not gmail_acct:
        print("[billcal] no Gmail account — skipping")
        return 0

    bills_cal_id = gmail_acct.get("bills_calendar_id")
    if not bills_cal_id:
        print("[billcal] no bills_calendar_id configured — skipping")
        return 0

    # Load unscheduled financial notes
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n.id, n.body, n.tags, n.created_at,
                          em.subject, em.received_at
                   FROM   personal.note n
                   LEFT   JOIN personal.email_message em ON em.note_id = n.id
                   WHERE  n.source = 'financial_doc'
                     AND  n.bill_event_id IS NULL
                   ORDER  BY n.id"""
            )
            notes = list(cur.fetchall())

    if not notes:
        print("[billcal] no unscheduled financial notes")
        return 0

    print(f"[billcal] scheduling {len(notes)} note(s) into Bills calendar")

    try:
        cal_svc = _cal_service(gmail_acct)
    except Exception as e:
        print(f"[billcal] calendar service failed: {e}")
        return 0

    created = 0
    for note in notes:
        note_id      = note["id"]
        body         = note["body"] or ""
        tags         = note["tags"] or []
        entity_tag   = tags[0] if tags else "Personal"
        received_at  = str(note["received_at"] or note["created_at"] or "")
        subject      = note["subject"] or body.split("\n")[0][:80]

        try:
            details  = _extract_payment_details(subject, body, received_at)
            event_id = _create_event(cal_svc, bills_cal_id, details, entity_tag, note_id)

            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.note SET bill_event_id = %s WHERE id = %s",
                        (event_id, note_id),
                    )
                conn.commit()

            print(f"[billcal] created '{details.get('biller')} {details.get('amount_due')}' on {details['due_date']} [{entity_tag}]")
            created += 1
        except Exception as e:
            print(f"[billcal] failed for note {note_id}: {e}")

    return created
