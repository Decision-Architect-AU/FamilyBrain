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
    # LLM placeholder values that should be treated as "not found"
    _FAKE_AMOUNTS = {"$1,234.56", "1234.56", "$123.45", "123.45",
                     "$456.78", "456.78", "$1,000,000.00", "$0.00", "0.00"}
    _FAKE_DATES   = {"2023-04-15", "2023-10-15", "2024-10-01"}  # hallucinated template dates

    prompt = (
        "Extract payment details from this financial document. "
        "Reply with ONLY a valid JSON object — no prose, no markdown, no explanation.\n\n"
        f"Subject: {subject}\n"
        f"Document text (first 2000 chars):\n{body[:2000]}\n\n"
        "Return JSON with exactly these fields:\n"
        '  "biller": string — the company or person sending the bill/invoice,\n'
        '  "amount_due": string — the EXACT dollar amount from the document, or null if not found. '
        'Do NOT invent or guess an amount.\n'
        '  "due_date": string — payment due date in YYYY-MM-DD format (use invoice date if no due date found, null if unknown),\n'
        '  "for_what": string — what property address, person name, or asset this bill relates to,\n'
        '  "invoice_ref": string — invoice number or reference if present, else null,\n'
        '  "how_to_pay": string — payment instructions: BSB, account number, BPAY biller code, reference, or payment link. null if not found,\n'
        '  "payment_status": string — EXACTLY "pending" (standalone invoice needing payment) or '
        '"paid_via_statement" (expense already deducted in an owner/rental statement).\n\n'
        "IMPORTANT: only return values that actually appear in the document. "
        "Use null for any field you cannot find. Never fabricate amounts or dates."
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

            # Scrub known LLM placeholder/hallucinated values
            amt = str(data.get("amount_due") or "").strip()
            if not amt or amt.lower() in ("null", "none") or amt in _FAKE_AMOUNTS:
                data["amount_due"] = ""

            # Validate / normalise due_date; reject hallucinated template dates
            due = data.get("due_date")
            if due and str(due) in _FAKE_DATES:
                due = None
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


def _find_existing_event(cal_svc, calendar_id: str, biller: str, due_date: str) -> str | None:
    """
    Search the Bills calendar for an existing event with a matching biller name
    on or near due_date (±7 days).  Returns the event id if found, else None.
    """
    try:
        from datetime import timedelta
        d = datetime.strptime(due_date, "%Y-%m-%d")
        time_min = (d - timedelta(days=7)).isoformat() + "Z"
        time_max = (d + timedelta(days=7)).isoformat() + "Z"
        result = cal_svc.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            q=biller[:40],          # search by biller name
            singleEvents=True,
            maxResults=10,
        ).execute()
        for ev in result.get("items", []):
            summary = ev.get("summary", "").lower()
            if biller.lower()[:20] in summary:
                return ev["id"]
    except Exception as e:
        print(f"[billcal] event search failed: {e}")
    return None


def _build_event_body(details: dict, entity_tag: str) -> dict:
    """Build the Google Calendar event dict from extracted payment details."""
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

    return {
        "summary": title,
        "description": "\n".join(lines),
        "start": {"date": date},
        "end":   {"date": date},
        "colorId": color_id,
    }


def _create_event(cal_svc, calendar_id: str, details: dict,
                  entity_tag: str, note_id: int) -> str:
    """
    Create or update an all-day event in the Bills calendar. Returns event id.
    Checks for an existing event with the same biller name near the due date
    before inserting, to avoid tripling up on re-runs.
    """
    biller = details.get("biller") or "Unknown"
    body   = _build_event_body(details, entity_tag)

    existing_id = _find_existing_event(cal_svc, calendar_id, biller, details["due_date"])
    if existing_id:
        print(f"[billcal] updating existing event {existing_id} for '{biller}'")
        cal_svc.events().patch(
            calendarId=calendar_id, eventId=existing_id, body=body
        ).execute()
        return existing_id

    result = cal_svc.events().insert(calendarId=calendar_id, body=body).execute()
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
            patch   = _build_event_body(details, entity_tag)
            title   = patch["summary"]
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

            print(f"[billcal] enriched '{title}' [{entity_tag.strip('{}')}]")
            updated += 1
        except Exception as e:
            print(f"[billcal] enrich failed for note {note_id}: {e}")

    return updated


# ── Dedup existing events ─────────────────────────────────────────────────────

def deduplicate_bill_calendar(accounts: list[dict]) -> int:
    """
    Scan the Bills calendar for duplicate events (same biller + same date).
    Keeps the event with the most description content, deletes the rest,
    and updates personal.note.bill_event_id to point to the surviving event.
    Returns number of duplicates deleted.
    """
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    if not gmail_acct:
        return 0
    bills_cal_id = gmail_acct.get("bills_calendar_id")
    if not bills_cal_id:
        return 0

    try:
        cal_svc = _cal_service(gmail_acct)
    except Exception as e:
        print(f"[billcal] calendar service failed: {e}")
        return 0

    # Fetch all events from the Bills calendar (paginate)
    all_events: list[dict] = []
    page_token = None
    while True:
        kwargs: dict = dict(calendarId=bills_cal_id, maxResults=250, singleEvents=True)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = cal_svc.events().list(**kwargs).execute()
        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"[billcal] dedup scan: {len(all_events)} total events")

    # Group by (date, normalised_biller)
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in all_events:
        date = (ev.get("start") or {}).get("date", "")
        summary = ev.get("summary", "")
        # Strip prefix (PAY: / ✓ PAID (stmt):) and amount to get biller key
        biller_raw = re.sub(r"^(✓ PAID \(stmt\):|PAY:)\s*", "", summary)
        biller_key = re.sub(r"\s*—.*$", "", biller_raw).strip().lower()[:40]
        if date and biller_key:
            groups[(date, biller_key)].append(ev)

    deleted = 0
    for (date, biller_key), events in groups.items():
        if len(events) <= 1:
            continue
        # Keep the event with the longest description (most info)
        events.sort(key=lambda e: len(e.get("description") or ""), reverse=True)
        keeper   = events[0]
        dupes    = events[1:]
        dupe_ids = {e["id"] for e in dupes}

        print(f"[billcal] dedup '{biller_key}' on {date}: keeping {keeper['id']}, removing {len(dupes)}")

        # Update any note rows pointing to a dupe to point to keeper
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE personal.note SET bill_event_id = %s WHERE bill_event_id = ANY(%s)",
                    (keeper["id"], list(dupe_ids)),
                )
            conn.commit()

        for ev in dupes:
            try:
                cal_svc.events().delete(
                    calendarId=bills_cal_id, eventId=ev["id"]
                ).execute()
                deleted += 1
            except Exception as e:
                print(f"[billcal] delete failed {ev['id']}: {e}")

    print(f"[billcal] dedup complete: {deleted} duplicate(s) removed")
    return deleted


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
