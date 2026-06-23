"""
Email decomposer.

Reads each ingested email and extracts ALL distinct items using an LLM:
  - calendar_event  → creates personal.event + Google Calendar entry
  - payment         → creates personal.note (financial_doc) for bill_calendar to schedule
  - observation     → creates personal.note
  - task            → creates personal.note with item_type='task'

Runs after ingest, marks email_decomposed = true when done.
Financial processor still handles structured attachments (PDFs, invoices).
"""
import json
import os
import re
import psycopg2
import psycopg2.extras
import requests as req

from datetime import datetime, timezone, date, timedelta

DB_URL      = os.environ["DATABASE_URL"]
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen2.5:14b")
INGESTOR_URL = os.environ.get("INGESTOR_URL", "")

_BATCH = 20   # emails per run


def _extract_items(subject: str, body: str, received_date: str) -> list[dict]:
    """
    LLM: decompose an email into typed items.
    Returns list of dicts, each with 'type' and type-specific fields.
    """
    prompt = (
        "Parse this email and extract ALL distinct actionable or notable items. "
        "An email might have zero items worth capturing, or several — return only real items.\n"
        "Reply with ONLY valid JSON — no prose, no markdown.\n\n"
        f"Email received: {received_date[:10]}\n"
        f"Subject: {subject}\n"
        f"Body (first 3000 chars):\n{body[:3000]}\n\n"
        'Return JSON: {"items": [...]}\n'
        "Each item has:\n"
        '  "type": one of "calendar_event" | "payment" | "observation" | "task"\n'
        '  "title": short descriptive title (max 80 chars)\n'
        '  "detail": full context — what/who/where/how much/reference numbers etc.\n'
        '  "date": YYYY-MM-DD of the actual event/due date. '
        f'Must NOT be the email received date ({received_date[:10]}) unless the event genuinely falls on that day. '
        'Null if no specific future or past date is stated in the content.\n'
        '  "time": HH:MM (24h) if a specific time is mentioned, else null\n'
        "  -- extra fields for specific types:\n"
        '  calendar_event: "end_date": YYYY-MM-DD if multi-day, "location": string or null\n'
        '  payment: "amount": exact dollar amount or null, "biller": who to pay, "reference": invoice/ref or null\n'
        '  task: "priority": "high"|"normal"\n\n'
        "Type selection rules:\n"
        "- calendar_event: a scheduled appointment, event, meeting, booking, or deadline with a specific date\n"
        "- payment: an invoice, bill, or payment request — even without an attachment\n"
        "- observation: a fact, decision, or piece of information worth remembering. "
        "  Use this for: birthday/anniversary mentions, policy updates, notifications, "
        "  confirmations of things already done, and anything informational with no action required\n"
        "- task: ONLY use when the email explicitly asks YOU to do something specific and actionable "
        "  (e.g. 'please sign and return', 'action required: renew by Friday'). "
        "  Do NOT create tasks for birthday greetings, passive reminders, or general information.\n\n"
        "General rules:\n"
        "- Only extract real items — skip marketing, unsubscribe footers, auto-replies\n"
        "- A payment reminder and a meeting invite in the same email = two separate items\n"
        "- Do NOT invent dates, amounts, or names not present in the text\n"
        "- If nothing worth capturing: return {\"items\": []}"
    )
    try:
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        raw = resp.json().get("response", "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            outer = json.loads(m.group())
            items = outer.get("items", [])
            if isinstance(items, list):
                return items
    except Exception as e:
        print(f"[decompose] LLM failed: {e}")
    return []


def _doc_date(received_at):
    """Brisbane-local date of the source email — stored on the note, not derived at query time."""
    try:
        import pytz
        from dateutil.parser import parse as dtparse
        return dtparse(str(received_at)).astimezone(pytz.timezone("Australia/Brisbane")).date()
    except Exception:
        return None


def _create_note(cur, email_id: int, title: str, body: str,
                  item_type: str, tags: list[str], received_at=None) -> int:
    cur.execute(
        """
        INSERT INTO personal.note (source, body, tags, item_type, source_email_id, document_date)
        VALUES ('email_decompose', %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (f"{title}\n\n{body}", tags, item_type, email_id, _doc_date(received_at)),
    )
    return cur.fetchone()[0]


def _create_calendar_event(cur, item: dict, calendar_source: str, email_id: int,
                             ingestor_url: str, received_date: str = "") -> None:
    from .db import upsert_event
    title    = item.get("title", "")
    detail   = item.get("detail", "")
    date_str = item.get("date")
    time_str = item.get("time")
    end_str  = item.get("end_date")

    if not date_str:
        return  # can't place on calendar without a date

    # Reject all-day events where the LLM defaulted to the email received date —
    # that almost always means no real date was found in the content.
    if not time_str and date_str == received_date[:10]:
        return

    try:
        if time_str:
            starts_at = datetime.fromisoformat(f"{date_str}T{time_str}:00+10:00")
            ends_at   = starts_at + timedelta(hours=1)
        else:
            starts_at = date.fromisoformat(date_str)
            ends_at   = date.fromisoformat(end_str) + timedelta(days=1) if end_str else None

        # Use a synthetic calendar_event_id so it deduplicates properly
        cal_id = f"decompose:{email_id}:{re.sub(r'[^a-z0-9]', '', title.lower()[:30])}:{date_str}"

        upsert_event(
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            event_type="inferred",
            calendar_source=calendar_source,
            calendar_event_id=cal_id,
            notes=detail[:500],
            ingestor_url=ingestor_url or None,
        )
    except Exception as e:
        print(f"[decompose] calendar event failed for '{title}': {e}")


def _create_payment_note(cur, item: dict, email_id: int, received_at=None) -> None:
    """Create a financial_doc note so bill_calendar picks it up."""
    biller  = item.get("biller") or item.get("title", "Unknown")
    amount  = item.get("amount") or ""
    ref     = item.get("reference") or ""
    detail  = item.get("detail", "")
    date_s  = item.get("date", "")

    body_parts = [f"Biller: {biller}"]
    if amount:
        body_parts.append(f"Amount: {amount}")
    if date_s:
        body_parts.append(f"Due: {date_s}")
    if ref:
        body_parts.append(f"Reference: {ref}")
    if detail:
        body_parts.append(f"\n{detail}")

    cur.execute(
        """
        INSERT INTO personal.note (source, body, item_type, source_email_id, document_date)
        VALUES ('financial_doc', %s, 'payment', %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        ("\n".join(body_parts), email_id, _doc_date(received_at)),
    )


def decompose_emails(accounts: list[dict]) -> int:
    """
    Process a batch of ingested emails that haven't been decomposed yet.
    Returns number of emails processed.
    """
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    calendar_source = f"gmail:{gmail_acct['email_address']}" if gmail_acct else "email"

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT em.id, em.subject, em.from_address, em.received_at,
                       n.body AS note_body
                FROM   personal.email_message em
                LEFT   JOIN personal.note n ON n.id = em.note_id
                WHERE  em.email_decomposed = false
                  AND  em.ingest_status = 'ingested'
                  AND  em.category NOT IN ('junk', 'marketing', 'newsletter', 'notification')
                ORDER  BY
                  CASE WHEN em.category IN ('finance', 'health', 'medical', 'ndis', 'insurance', 'legal') THEN 0 ELSE 1 END,
                  em.received_at DESC
                LIMIT  %s
                """,
                (_BATCH,),
            )
            emails = list(cur.fetchall())

    if not emails:
        return 0

    print(f"[decompose] processing {len(emails)} email(s)")
    processed = 0

    for email in emails:
        email_id    = email["id"]
        subject     = email["subject"] or ""
        body        = email["note_body"] or ""
        received_at = str(email["received_at"] or "")

        try:
            items = _extract_items(subject, body, received_at)

            if items:
                print(f"[decompose] '{subject[:60]}': {len(items)} item(s)")

            with psycopg2.connect(DB_URL) as wconn:
                with wconn.cursor() as wcur:
                    for item in items:
                        itype = item.get("type")
                        title = item.get("title", "")
                        detail = item.get("detail", "")

                        if itype == "calendar_event":
                            _create_calendar_event(wcur, item, calendar_source,
                                                    email_id, INGESTOR_URL,
                                                    received_date=received_at)

                        elif itype == "payment":
                            _create_payment_note(wcur, item, email_id, received_at)

                        elif itype in ("observation", "task"):
                            tags = ["task"] if itype == "task" else []
                            priority = item.get("priority", "normal")
                            if itype == "task" and priority == "high":
                                tags.append("urgent")
                            _create_note(wcur, email_id, title, detail, itype, tags, received_at)

                    wcur.execute(
                        "UPDATE personal.email_message SET email_decomposed = true WHERE id = %s",
                        (email_id,),
                    )
                wconn.commit()

            processed += 1

        except Exception as e:
            print(f"[decompose] failed for email {email_id} '{subject[:40]}': {e}")
            try:
                with psycopg2.connect(DB_URL) as ec:
                    with ec.cursor() as ecur:
                        ecur.execute(
                            "UPDATE personal.email_message SET email_decomposed = true WHERE id = %s",
                            (email_id,),
                        )
                    ec.commit()
            except Exception:
                pass

    return processed
