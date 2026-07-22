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
import traceback
import psycopg2
import psycopg2.extras
import requests as req

from datetime import datetime, timezone, date, timedelta

# Pre-extract meeting links from raw email body before any truncation or stripping.
# These are preserved separately and stored in personal.event.meeting_url.
_MEETING_URL_RE = re.compile(
    r'https?://\S*(?:'
    r'zoom\.us/j/'
    r'|teams\.microsoft\.com/l/meetup-join/'
    r'|meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}'
    r'|webex\.com/meet/'
    r'|gotomeeting\.com/join/'
    r'|whereby\.com/'
    r'|bluejeans\.com/'
    r'|around\.co/'
    r')\S*',
    re.I
)

DB_URL      = os.environ["DATABASE_URL"]
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL = os.environ.get("MODEL_PARSER_2ND", os.environ.get("AGENT_MODEL", "qwen2.5:14b"))
INGESTOR_URL = os.environ.get("INGESTOR_URL", "")

_BATCH = 20   # emails per run


def _extract_meeting_url(body: str) -> str | None:
    """Pull the first meeting join URL from the raw body before any truncation."""
    m = _MEETING_URL_RE.search(body)
    return m.group(0).rstrip(")>\"'.,") if m else None


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
        f'Use {received_date[:10]} as the anchor date to resolve relative expressions '
        f'like "+ 4 weeks", "in 6 weeks", "next Monday", "review in 3 months" — calculate the absolute date. '
        f'Must NOT be the email received date ({received_date[:10]}) unless the event genuinely falls on that day. '
        'Null only if there is truly no date reference at all in the content.\n'
        '  "time": HH:MM (24h) if a specific time is mentioned, else null\n'
        '  "relative_to": title of the parent event this date is relative to (e.g. "Initial Session"), or null if it is an anchor event\n'
        '  "relative_offset_days": integer number of days after the parent event, or null if not relative\n'
        '  "relative_anchor": human-readable description of the dependency (e.g. "4 weeks after initial session"), or null\n'
        "  -- extra fields for specific types:\n"
        '  calendar_event: "end_date": YYYY-MM-DD if multi-day, "location": string or null, "meeting_url": the full video/conference join URL (Zoom/Teams/Meet/Webex etc.) if present in the email, else null\n'
        '  payment: "amount": exact dollar amount as it appears in the email or null, "biller": who to pay, "reference": invoice/ref number as it appears or null\n'
        '  task: "priority": "high"|"normal"\n\n'
        "Type selection rules:\n"
        "- calendar_event: a scheduled appointment, meeting, booking, deadline, or ANY document/script/plan "
        "  that references a date — including relative dates like '+ 4 weeks', 'review in 6 weeks', 'next session'. "
        "  Each distinct date in a document becomes its own calendar_event.\n"
        "- payment: ONLY use when the email is an unpaid invoice, bill, or explicit payment request "
        "  with a real amount and biller stated in the email body. "
        "  Do NOT use for booking confirmations (payment already made), receipts, or anything without a clear 'please pay' instruction. "
        "  Leave amount/reference/biller null if not explicitly stated — never guess or infer them.\n"
        "- observation: a fact, decision, or piece of information worth remembering. "
        "  Use this for: booking confirmations, receipts, birthday/anniversary mentions, policy updates, notifications, "
        "  confirmations of things already done, and anything informational with no action required\n"
        "- task: ONLY use when the email explicitly asks YOU to do something specific and actionable "
        "  (e.g. 'please sign and return', 'action required: renew by Friday'). "
        "  Do NOT create tasks for birthday greetings, passive reminders, or general information.\n\n"
        "General rules:\n"
        "- Only extract real items — skip marketing, unsubscribe footers, auto-replies\n"
        "- A payment reminder and a meeting invite in the same email = two separate items\n"
        "- A therapy script or medical plan with multiple dated steps = one calendar_event per step\n"
        "- CRITICAL: Do NOT invent, infer, or guess any field values. Only use values explicitly present in the email text. "
        "  If a field value is not in the email, set it to null — never substitute a placeholder.\n"
        "- If nothing worth capturing: return {\"items\": []}"
    )
    def _call_llm(extra_tokens: int = 0) -> list:
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False,
                  "options": {"num_predict": 2048 + extra_tokens}},
            timeout=180,
        )
        raw = resp.json().get("response", "")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return []
        text = m.group()
        try:
            return json.loads(text).get("items", [])
        except json.JSONDecodeError:
            # Truncated JSON — try patching the tail so partial items aren't lost
            patched = re.sub(r',\s*\{[^}]*$', '', text).rstrip(",") + "]}"
            try:
                return json.loads(patched).get("items", [])
            except json.JSONDecodeError:
                raise

    try:
        items = _call_llm()
        if isinstance(items, list):
            return items
    except json.JSONDecodeError:
        # Response was cut off — retry with more tokens
        try:
            print(f"[decompose] JSON truncated, retrying with more tokens")
            items = _call_llm(extra_tokens=2048)
            if isinstance(items, list):
                return items
        except Exception as e:
            print(f"[decompose] LLM retry failed: {e}")
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
    row = cur.fetchone()
    return row["id"] if row else None


# Event types that are context-only (don't commit person time)
_CONTEXT_TYPES = {"SCHOOL_HOLIDAY", "PUBLIC_HOLIDAY", "HOLIDAY", "LEAVE"}


def _resolve_person_id(text: str) -> int | None:
    """Try to resolve a person_id by matching known person names against event text.

    Tries full-name match first, then first-name-only for individuals (no organisation)
    whose first name is unique in the person table — handles titles like "Olivia Physio…"
    where the full name "Olivia West" doesn't appear.
    """
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                cur.execute("SELECT id, name, organisation FROM personal.person")
                persons = cur.fetchall()
        text_lower = text.lower()

        # Full name match
        for person in persons:
            name = (person.get("name") or "").lower()
            if name and len(name) > 2 and name in text_lower:
                return person["id"]

        # First-name match — individuals only, unique first name required
        first_name_map: dict[str, list[int]] = {}
        for person in persons:
            if person.get("organisation"):
                continue  # skip orgs/providers — first names like "Centre" would false-match
            name = (person.get("name") or "").strip()
            first = name.split()[0].lower() if name else ""
            if first and len(first) > 2:
                first_name_map.setdefault(first, []).append(person["id"])

        for first, ids in first_name_map.items():
            if len(ids) == 1 and first in text_lower:
                return ids[0]

    except Exception:
        pass
    return None


def _supersede_placeholder(cur, slot_key: str, new_event_id: int,
                            incoming_rank: int) -> dict | None:
    """
    If a generated placeholder exists for this slot_key with lower/equal rank,
    supersede it and return the superseded event row (includes gen_asset_id for
    asset enrichment). Returns None if no placeholder was found or rank too low.
    """
    cur.execute("""
        SELECT id, precedence_rank, gen_asset_id FROM personal.event
        WHERE slot_key = %s
          AND status = 'generated'
          AND provenance = 'rule'
        ORDER BY precedence_rank DESC
        LIMIT 1
    """, (slot_key,))
    row = cur.fetchone()
    if row and incoming_rank >= row["precedence_rank"]:
        cur.execute("""
            UPDATE personal.event
            SET status = 'superseded', superseded_by_event_id = %s
            WHERE id = %s
        """, (new_event_id, row["id"]))
        return dict(row)
    return None


def _enrich_asset_from_confirmed(cur, asset_id: int, confirmed_item: dict,
                                  confirmed_event_id: int) -> None:
    """Write ground-truth fields from a confirmed event back into the source asset.

    Called when a confirmed calendar event (rank >= generated) supersedes a routine
    placeholder — the slot match is the confidence signal (equivalent to ~90%+).
    Enriches asset.facts with confirmed time/location/provider and appends a note.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    title    = confirmed_item.get("title") or ""
    notes    = confirmed_item.get("detail") or ""
    location = confirmed_item.get("location") or ""
    time_str = confirmed_item.get("time") or ""        # "08:00" or "8:00am"
    date_str = confirmed_item.get("date") or ""

    # Normalise time to HH:MM
    confirmed_time = None
    if time_str:
        try:
            for fmt in ("%I:%M%p", "%I:%M %p", "%H:%M", "%I%p"):
                try:
                    confirmed_time = _dt.strptime(time_str.strip().upper(), fmt.upper()).strftime("%H:%M")
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    # Try to resolve provider from event text against person table
    provider_person_id = None
    try:
        cur.execute("SELECT id, name FROM personal.person WHERE organisation IS NOT NULL")
        providers = cur.fetchall()
        text_lower = f"{title} {notes} {location}".lower()
        for p in providers:
            name = (p["name"] or "").lower()
            if name and len(name) > 3 and name in text_lower:
                provider_person_id = p["id"]
                break
    except Exception:
        pass

    # Build fact patch — only fields we actually have
    fact_patch: dict = {"last_confirmed_date": date_str} if date_str else {}
    if confirmed_time:
        fact_patch["confirmed_time"] = confirmed_time
    if location:
        fact_patch["confirmed_location"] = location

    note_line = f"Confirmed {date_str}: {title}"
    if location:
        note_line += f" @ {location}"

    try:
        cur.execute("""
            UPDATE personal.asset
            SET facts    = facts || %s::jsonb,
                notes    = CASE
                             WHEN notes IS NULL OR notes = '' THEN %s
                             WHEN notes NOT LIKE '%%' || %s || '%%' THEN notes || E'\n' || %s
                             ELSE notes
                           END,
                provider_person_id = COALESCE(provider_person_id, %s)
            WHERE id = %s
        """, (
            _json.dumps(fact_patch),
            note_line, date_str, note_line,
            provider_person_id,
            asset_id,
        ))
        print(f"[decompose] enriched asset {asset_id} from confirmed event {confirmed_event_id}"
              + (f" — provider person {provider_person_id}" if provider_person_id else ""))
    except Exception as e:
        print(f"[decompose] asset enrichment failed for asset {asset_id}: {e}")


def _create_calendar_event(cur, item: dict, calendar_source: str, email_id: int,
                             ingestor_url: str, received_date: str = "",
                             title_to_event_id: dict | None = None,
                             pre_extracted_meeting_url: str | None = None,
                             email_meta: dict | None = None) -> int | None:
    """Create a calendar event. Returns the new event id, or None on failure."""
    from .db import upsert_event
    title    = item.get("title", "")
    detail   = item.get("detail", "")
    date_str = item.get("date")
    time_str = item.get("time")
    end_str  = item.get("end_date")
    location = item.get("location")
    # LLM-extracted URL takes precedence; pre-extracted regex URL is the fallback
    meeting_url = item.get("meeting_url") or pre_extracted_meeting_url
    relative_to     = item.get("relative_to")
    offset_days     = item.get("relative_offset_days")
    relative_anchor = item.get("relative_anchor")

    if not date_str:
        return None

    # Reject all-day events where the LLM defaulted to the email received date
    if not time_str and date_str == received_date[:10]:
        return None

    try:
        if time_str:
            starts_at = datetime.fromisoformat(f"{date_str}T{time_str}:00+10:00")
            ends_at   = starts_at + timedelta(hours=1)
        else:
            starts_at = date.fromisoformat(date_str)
            ends_at   = date.fromisoformat(end_str) + timedelta(days=1) if end_str else None

        cal_id = f"decompose:{email_id}:{re.sub(r'[^a-z0-9]', '', title.lower()[:30])}:{date_str}"

        # Build notes with LLM detail + source provenance footer
        notes_parts = [detail[:500]] if detail else []
        if email_meta:
            src_lines = []
            if email_meta.get("account_email"):
                src_lines.append(f"Source: {email_meta['account_email']}")
            if email_meta.get("from_address"):
                src_lines.append(f"From: {email_meta['from_address']}")
            if email_meta.get("received_at"):
                src_lines.append(f"Received: {str(email_meta['received_at'])[:10]}")
            if src_lines:
                notes_parts.append("\n".join(src_lines))
        notes = "\n\n".join(notes_parts)

        event_id = upsert_event(
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            event_type="inferred",
            calendar_source=calendar_source,
            calendar_event_id=cal_id,
            notes=notes,
            ingestor_url=ingestor_url or None,
        )

        if not event_id:
            return None

        # Stage 2: set provenance/status and attempt slot override
        event_type   = item.get("event_type", "inferred").upper()
        effective_dt = date.fromisoformat(date_str)

        # Resolve person from title + detail
        person_id = _resolve_person_id(f"{title} {detail}")

        # Classify incoming event
        from .slot_classify import classify as _classify_slot
        slot_class, blocks_person, rank = _classify_slot(event_type)
        slot_key = f"{person_id}:{effective_dt}:{slot_class}" if person_id else None
        needs_review = False

        # Update event with provenance/status and slot fields.
        # Status stays 'confirmed' — events go to GCal regardless of whether we resolved
        # a person (concerts, family events, etc. have no person but are still valid).
        cur.execute("""
            UPDATE personal.event
            SET provenance      = 'email',
                status          = 'confirmed',
                slot_key        = %s,
                slot_class      = %s,
                blocks_person   = %s,
                precedence_rank = %s,
                person_id       = COALESCE(person_id, %s),
                occurrence_date = %s
            WHERE id = %s
        """, (slot_key, slot_class, blocks_person, rank,
              person_id, effective_dt, event_id))

        # Attempt override: supersede a generated placeholder in the same slot
        if slot_key and event_type not in _CONTEXT_TYPES:
            superseded_row = _supersede_placeholder(cur, slot_key, event_id, rank)
            if superseded_row:
                print(f"[decompose] overrode generated placeholder for slot {slot_key}")
                if superseded_row.get("gen_asset_id"):
                    _enrich_asset_from_confirmed(cur, superseded_row["gen_asset_id"], item, event_id)

        # Store meeting_url, location, and relative dependency
        if meeting_url or location or relative_to or offset_days or relative_anchor:
            parent_id = (title_to_event_id or {}).get(relative_to.lower().strip()) if relative_to else None
            cur.execute(
                """UPDATE personal.event
                   SET parent_event_id = %s,
                       relative_offset_days = %s,
                       relative_anchor = %s,
                       meeting_url = COALESCE(%s, meeting_url),
                       location    = COALESCE(%s, location)
                   WHERE id = %s""",
                (parent_id, offset_days, relative_anchor, meeting_url, location, event_id),
            )

        if title_to_event_id is not None:
            title_to_event_id[title.lower().strip()] = event_id

        return event_id
    except Exception as e:
        print(f"[decompose] calendar event failed for '{title}': {e!r}")
        traceback.print_exc()
        return None


_PLACEHOLDER_PATTERNS = re.compile(
    r'\b(123\s*main|example\.com|123456789|bsb\s*:\s*123|acct?\s*:\s*123|'
    r'your\s+(name|address|bsb|account)|placeholder|lorem\s+ipsum|'
    r'xx+|00000|11111|99999)\b',
    re.IGNORECASE,
)


def _looks_fabricated(item: dict) -> bool:
    """Return True if a payment item contains hallucinated placeholder values."""
    check_fields = [
        item.get("biller", ""),
        item.get("reference", ""),
        item.get("detail", ""),
        item.get("amount", ""),
    ]
    combined = " ".join(str(f) for f in check_fields if f)
    return bool(_PLACEHOLDER_PATTERNS.search(combined))


def _create_payment_note(cur, item: dict, email_id: int, received_at=None) -> None:
    """Create a financial_doc note so bill_calendar picks it up."""
    if _looks_fabricated(item):
        print(f"[decompose] rejected fabricated payment item: {item.get('title', '')}")
        return
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


def _fetch_attachment_text_for_email(account: dict, provider_msg_id: str) -> str:
    """
    Fetch attachment bytes from any email with no text body, then POST to the
    ingestor's /ingest/extract endpoint (which has Tesseract OCR) to get text.
    Handles both Gmail and Outlook.
    """
    import base64
    try:
        provider = account.get("provider", "")
        if provider == "outlook":
            from .financial_processor import _outlook_attachments
            attachments, _ = _outlook_attachments(account, provider_msg_id)
        elif provider == "gmail":
            from .financial_processor import _gmail_attachments
            attachments, _ = _gmail_attachments(account, provider_msg_id)
        else:
            return ""

        parts = []
        for fname, data in attachments:
            try:
                resp = req.post(
                    f"{INGESTOR_URL}/ingest/extract",
                    json={"content_b64": base64.b64encode(data).decode(), "filename": fname},
                    timeout=60,
                )
                text = resp.json().get("text", "") if resp.ok else ""
                if text.strip():
                    parts.append(text)
            except Exception as ex:
                print(f"[decompose] ingestor extract failed for {fname}: {ex}")
        return "\n\n".join(parts).replace("\x00", "")
    except Exception as e:
        print(f"[decompose] attachment fetch failed for {provider_msg_id}: {e}")
        return ""


def _store_note_body(email_id: int, body: str) -> int:
    """Insert a note for the given email body text and link it to the email. Returns note id."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personal.note (source, body, item_type, source_email_id)
                VALUES ('email_attachment', %s, 'observation', %s)
                RETURNING id
                """,
                (body, email_id),
            )
            note_id = cur.fetchone()["id"]
            cur.execute(
                "UPDATE personal.email_message SET note_id = %s WHERE id = %s",
                (note_id, email_id),
            )
        c.commit()
    return note_id


def decompose_emails(accounts: list[dict]) -> int:
    """
    Process a batch of ingested emails that haven't been decomposed yet.
    Returns number of emails processed.
    """
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    # Use a neutral source so appointment_updater picks these up and writes them to GCal.
    # (gmail:... source is skipped by the updater as it assumes those events already exist in GCal)
    calendar_source = "email:decompose"

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT em.id, em.subject, em.from_address, em.received_at,
                       em.account_id, em.provider_msg_id, em.note_id,
                       n.body AS note_body
                FROM   personal.email_message em
                LEFT   JOIN personal.note n ON n.id = em.note_id
                WHERE  em.email_decomposed = false
                  AND  em.ingest_status = 'ingested'
                  AND  em.category NOT IN ('junk', 'marketing', 'newsletter', 'notification')
                  -- Outlook/Exchange meeting-response auto-notifications (Declined:/Accepted:/
                  -- Tentative:/Canceled:) carry no new information to extract — they're RSVP
                  -- echoes of an existing calendar invite. A single recurring meeting set decades
                  -- into the future can generate hundreds of these, one per occurrence, each
                  -- burning a full LLM call for nothing. Filter at the SQL level so they never
                  -- reach the LLM at all — the same discipline as the junk/marketing exclusion.
                  AND  em.subject !~* '^(declined|accepted|tentative|cancel+ed):'
                ORDER  BY
                  -- Recent emails (< 48 h) always surface first — prevents new activity
                  -- dates being starved by the historical backlog
                  CASE WHEN em.received_at > now() - INTERVAL '48 hours' THEN 0 ELSE 1 END,
                  -- Within each recency band, time-sensitive categories come first
                  CASE WHEN em.category IN ('finance', 'health', 'medical', 'ndis',
                                            'insurance', 'legal', 'school') THEN 0 ELSE 1 END,
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
        email_id     = email["id"]
        subject      = email["subject"] or ""
        body         = email["note_body"] or ""
        received_at  = str(email["received_at"] or "")
        account_id   = email["account_id"]
        provider_id  = email["provider_msg_id"] or ""
        acct         = next((a for a in accounts if a["id"] == account_id), None)
        email_meta   = {
            "account_email": acct["email_address"] if acct else None,
            "from_address":  email.get("from_address") or email.get("from_name"),
            "received_at":   email["received_at"],
        }

        # If no body text and email has no note, try extracting text from attachments (any provider)
        if not body.strip() and not email["note_id"] and provider_id:
            acct = next((a for a in accounts if a["id"] == account_id), None)
            if acct:
                att_text = _fetch_attachment_text_for_email(acct, provider_id)
                att_text = att_text.replace("\x00", "")  # Postgres rejects NUL bytes
                if att_text.strip():
                    print(f"[decompose] extracted {len(att_text)} chars from attachments for email {email_id}")
                    _store_note_body(email_id, att_text)
                    body = att_text

        try:
            # Pre-extract meeting URL from raw body before truncation — used as fallback
            # if the LLM misses it or the link is buried below the 3000-char prompt window.
            pre_meeting_url = _extract_meeting_url(body)

            items = _extract_items(subject, body, received_at)

            if items:
                print(f"[decompose] '{subject[:60]}': {len(items)} item(s)")
                if pre_meeting_url:
                    print(f"[decompose] pre-extracted meeting URL: {pre_meeting_url[:80]}")

            # Process each item in its own transaction so wconn locks release between
            # items — prevents self-deadlock when upsert_event (second connection) dedup-
            # checks a row that wconn already holds a lock on from a previous item.
            title_to_event_id: dict = {}
            for item in items:
                itype = item.get("type")
                title = item.get("title", "")
                detail = item.get("detail", "")

                with psycopg2.connect(DB_URL) as wconn:
                    with wconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as wcur:
                        if itype == "calendar_event":
                            _create_calendar_event(wcur, item, calendar_source,
                                                    email_id, INGESTOR_URL,
                                                    received_date=received_at,
                                                    title_to_event_id=title_to_event_id,
                                                    pre_extracted_meeting_url=pre_meeting_url,
                                                    email_meta=email_meta)

                        elif itype == "payment":
                            _create_payment_note(wcur, item, email_id, received_at)

                        elif itype in ("observation", "task"):
                            tags = ["task"] if itype == "task" else []
                            priority = item.get("priority", "normal")
                            if itype == "task" and priority == "high":
                                tags.append("urgent")
                            _create_note(wcur, email_id, title, detail, itype, tags, received_at)
                    wconn.commit()

            with psycopg2.connect(DB_URL) as wconn:
                with wconn.cursor() as wcur:
                    wcur.execute(
                        "UPDATE personal.email_message SET email_decomposed = true WHERE id = %s",
                        (email_id,),
                    )
                wconn.commit()

            processed += 1

        except Exception as e:
            err_str = str(e).lower()
            # Network/API errors — leave email_decomposed = false so it retries next cycle
            is_transient = any(x in err_str for x in (
                "name or service not known", "unable to find the server",
                "nameresolutionerror", "connectionerror", "connection reset",
                "timeout", "timed out", "max retries",
            ))
            print(f"[decompose] {'transient failure, will retry' if is_transient else 'failed'} "
                  f"for email {email_id} '{subject[:40]}': {e!r}")
            traceback.print_exc()
            if not is_transient:
                # Only mark done for non-network failures (parse errors, malformed content)
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
