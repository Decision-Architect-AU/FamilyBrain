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
GRAPH_API_URL = os.environ.get("GRAPH_API_URL", "http://graph-api:4003")

_BATCH = 20   # emails per run


class DecomposeUnavailable(Exception):
    """The extraction LLM couldn't run — the email must stay queued, not be marked done."""


def _extract_meeting_url(body: str) -> str | None:
    """Pull the first meeting join URL from the raw body before any truncation."""
    m = _MEETING_URL_RE.search(body)
    return m.group(0).rstrip(")>\"'.,") if m else None


_FORWARD_MARKER_RE = re.compile(
    r'-{2,}\s*forwarded message\s*-{2,}|'
    r'-{2,}\s*original message\s*-{2,}|'
    r'^begin forwarded message:|'
    r'^on .+ wrote:',
    re.I | re.M,
)
_ANNOTATION_DATE_RE = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b')
_MONTHS = {m: i + 1 for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
     'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
# "28 October 2026" / "28th Oct" / "October 28, 2026" — year optional
_ANNOTATION_WORD_DATE_RE = re.compile(
    r'\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})|([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?)'
    r',?\s*(\d{4})?\b', re.I)


def _annotation_with_date(body: str, received_iso: str | None = None) -> tuple[str, str] | None:
    """
    Return (annotation_text, iso_date) if the text above a forwarded/quoted
    marker (or the first ~200 chars, if there's no marker) contains a date —
    DD/MM/YYYY-style or written out ("28 October 2026"). A written date with
    no year resolves to its next occurrence on/after the email's received
    date (so "vacate by 28 October" in a September email means this year).

    Ground-truth safety net: a date in a short human annotation on a forward
    (e.g. "Loganholme to vacate on 28/10/2026") is easy for the LLM to bury
    under a vague dateless summary of the forwarded boilerplate below it.
    """
    m = _FORWARD_MARKER_RE.search(body)
    annotation = body[:m.start()].strip() if m else body[:200].strip()
    if not annotation:
        return None
    dm = _ANNOTATION_DATE_RE.search(annotation)
    if dm:
        day, month, year = dm.groups()
        if len(year) == 2:
            year = f"20{year}"
        try:
            return annotation, date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
    for wm in _ANNOTATION_WORD_DATE_RE.finditer(annotation):
        d1, mon1, mon2, d2, year = wm.groups()
        mon_word, day = (mon1, d1) if mon1 else (mon2, d2)
        month = _MONTHS.get((mon_word or '')[:3].lower())
        if not month:
            continue
        try:
            if year:
                return annotation, date(int(year), month, int(day)).isoformat()
            if received_iso:
                anchor = date.fromisoformat(received_iso[:10])
                cand = date(anchor.year, month, int(day))
                if cand < anchor:
                    cand = date(anchor.year + 1, month, int(day))
                return annotation, cand.isoformat()
        except ValueError:
            continue
    return None


def _ensure_annotation_date_captured(items: list, subject: str, body: str,
                                     received_date: str | None = None) -> list:
    """
    If a forwarded-message annotation has a real date and none of the LLM's
    extracted CALENDAR items already carries that date, append a
    calendar_event for it directly — belt-and-braces against the LLM
    classifying the whole email as a dateless "task" and dropping the actual
    deadline.

    Only calendar_event items count as "captured": tasks/observations become
    notes, not personal.event + Google Calendar rows, so an LLM "task" that
    happens to carry the date would otherwise suppress this net while the
    date still never reaches the calendar (the vacate-notice failure mode).
    """
    found = _annotation_with_date(body, received_date)
    if not found:
        return items
    annotation, iso_date = found
    already_captured = any(
        isinstance(it, dict) and it.get("type") == "calendar_event"
        and it.get("date") == iso_date for it in items
    )
    if already_captured:
        return items
    print(f"[decompose] annotation date safety net: adding calendar_event for {iso_date}")
    items.append({
        "type": "calendar_event",
        "title": subject[:80] or "Dated item from forwarded email",
        "detail": annotation[:500],
        "date": iso_date,
        "time": None,
        "relative_to": None,
        "relative_offset_days": None,
        "relative_anchor": None,
    })
    return items


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
        "  A meeting must be actually booked/agreed with a date. NOT calendar_events: an offer to meet, "
        "  'book a time using this link', a proposed time awaiting a reply, or a meeting that already "
        "  happened ('thanks for your time yesterday') — use observation for those.\n"
        "  An email that re-confirms or changes logistics of an appointment (assistant confirming the "
        "  booking, 'switching to Zoom') describes the SAME meeting: use exactly the day and time it "
        "  states — 'the 21st' means day 21 — never shift it.\n"
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
        "- Pay close attention to the first 1-2 lines of the body — often a short "
        "human-written note added above forwarded/quoted content (e.g. \"Loganholme to "
        "vacate on 28/10/2026\"). That note is frequently the single most important fact "
        "in the email and must produce a calendar_event with its exact date, even if the "
        "forwarded content below it reads as a generic legal notice or document. "
        "A 'Notice to Leave' / vacate notice with a vacate-by date is a calendar_event for "
        "that date — not a vague task like 'review notice'.\n"
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
        if not isinstance(items, list):
            items = []
    except json.JSONDecodeError:
        # Response was cut off — retry with more tokens
        try:
            print(f"[decompose] JSON truncated, retrying with more tokens")
            items = _call_llm(extra_tokens=2048)
            if not isinstance(items, list):
                items = []
        except Exception as e:
            print(f"[decompose] LLM retry failed: {e}")
            raise DecomposeUnavailable(str(e)) from e
    except Exception as e:
        # An unreachable or erroring LLM is not "this email has no items": the
        # caller marks the email decomposed and never looks at it again, which
        # is how a urology appointment ("Appointment Date: 29th September
        # 2026") was lost while Ollama was down. Signal it instead so the
        # email stays queued for the next cycle.
        print(f"[decompose] LLM unavailable, will retry: {e}")
        raise DecomposeUnavailable(str(e)) from e

    return _ensure_annotation_date_captured(items, subject, body, received_date)


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


def _create_task_event(cur, email_id: int, title: str, detail: str,
                        date_str: str | None, priority: str = "normal") -> int:
    """
    Increment 5 — task items become personal.event(event_type='task') rows,
    not personal.note. Confirmed live before this change: the channel/sync
    infrastructure this and the Google Tasks channel need
    (channel_resolver.materialise(), calendar_sync_map) is Event-only in
    practice — materialise() does a literal `UPDATE personal.event`, nothing
    for notes, despite its own docstring claiming both. Building task
    routing against Note would have meant duplicating that whole system a
    second time. Historical task-notes created before this change are left
    alone; only new tasks use this path.

    Dateless by default (starts_at/effective_date both NULL) unless the LLM
    extracted an actual due date — same pattern already proven for
    obligation/investigation/maintenance_request event types. task_status
    is a dedicated column, not the shared `status` column (see
    postgres/init/50_google_tasks.sql — status's CHECK constraint is
    calendar-lifecycle-specific and shared across every event type).
    """
    effective_date = None
    starts_at = None
    if date_str:
        try:
            effective_date = date.fromisoformat(date_str)
            starts_at = datetime.combine(effective_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    display_title = f"[URGENT] {title}" if priority == "high" else title

    cur.execute(
        """
        INSERT INTO personal.event
            (title, event_type, status, provenance, calendar_source, notes,
             task_status, starts_at, effective_date, source_email_id)
        VALUES (%s, 'task', 'confirmed', 'email', 'email:decompose', %s,
                'open', %s, %s, %s)
        RETURNING id
        """,
        (display_title, detail[:2000], starts_at, effective_date, email_id),
    )
    event_id = cur.fetchone()["id"]
    # channel_resolver.materialise() opens its own separate connection and
    # updates by id — the insert above must be visible to it first, or its
    # UPDATE silently matches zero rows (no error, just a permanently NULL
    # next_update_at). Confirmed live: this exact ordering bug happened on
    # the very first test of this function.
    cur.connection.commit()

    from . import channel_resolver
    channel_resolver.materialise(event_id, item_type="task", effective_date=effective_date)

    return event_id


def _resolve_task_to_product(cur, title: str, detail: str) -> dict | None:
    """
    Fuzzy-match a task item's text against existing personal.product names.
    Products are few per household (a handful of warrantied
    appliances/fixtures, not hundreds), so direct pg_trgm similarity across
    the whole table is the simplest correct v1 approach — no need to first
    infer an asset/property context the way asset_matcher does for asset
    *events*, since we're matching a much smaller, flatter set.
    """
    text = f"{title} {detail}".strip()
    if not text:
        return None
    cur.execute(
        """
        SELECT id, asset_id, name, category, install_date, cost,
               vendor_org_ref, warranty_period_months, warranty_expiry_date,
               source_doc_ref, similarity(name, %(text)s) AS sim
        FROM personal.product
        WHERE similarity(name, %(text)s) > 0.25
        ORDER BY sim DESC
        LIMIT 1
        """,
        {"text": text},
    )
    return cur.fetchone()


def _apply_familybrain_label(acct: dict, provider_msg_id: str, value: str) -> None:
    """
    Apply a FamilyBrain/<value> tag to a message — reuses the exact existing
    per-provider label functions (gmail.py/outlook.py), same namespace as
    every other tag this system applies, rather than a new label-writing
    path. Used for all three Increment 4 investigation labels
    (knowledge-identified, investigation-pending, investigation-resolved).
    Best-effort: a labeling failure shouldn't stop the underlying lifecycle
    transition from taking effect.
    """
    if not acct or not provider_msg_id:
        return
    try:
        if acct.get("provider") == "gmail":
            from . import gmail as gmail_mod
            svc = gmail_mod._gmail_service(acct)
            gmail_mod.apply_ingested_label(acct, svc, provider_msg_id, value)
        elif acct.get("provider") == "outlook":
            from . import outlook as outlook_mod
            outlook_mod.apply_ingested_category(acct, provider_msg_id, value)
    except Exception as e:
        print(f"[decompose] FamilyBrain/{value} label failed for {provider_msg_id}: {e}")


def _check_product_investigation(cur, email_id: int, acct: dict | None,
                                   provider_msg_id: str, title: str, detail: str) -> None:
    """
    Increment 4, 4b: when a task item resolves to an existing Product, ask
    graph-api's check_product_completeness primitive (pure lookup, no LLM)
    whether a matching investigation_rule finds a documented gap (e.g. no
    warranty_period_months on file). On a gap: create a lightweight
    'maintenance_request' event for the task (mirrors wa-agent's
    obligations.py precedent of a direct, non-calendar-facing INSERT — this
    event never reaches Google Calendar, same invariant as event_type=
    'obligation'), then an 'investigation' event linked via the existing
    parent_event_id column (the same Postgres-FK mechanism already used for
    relative-event linking and obligations — not a new AGE graph edge; the
    interrogation primitives already query these Postgres columns directly).
    Tags the source email with FamilyBrain/knowledge-identified immediately,
    regardless of which follow_up_action the matched rule specifies — that
    decision belongs to 4c, not here.
    """
    product = _resolve_task_to_product(cur, title, detail)
    if not product:
        return

    try:
        resp = req.get(
            f"{GRAPH_API_URL}/interrogate/check_product_completeness",
            params={"product_id": product["id"], "trigger_event_type": "maintenance_request"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()["result"]
    except Exception as e:
        print(f"[decompose] check_product_completeness failed for product {product['id']}: {e}")
        return

    if result.get("is_complete", True):
        return

    rule = result.get("rule_matched") or {}
    reason = rule.get("reason_template") or f"missing {result.get('missing_field')}"

    cur.execute(
        """
        INSERT INTO personal.event
            (title, event_type, status, provenance, calendar_source, notes, product_id)
        VALUES (%s, 'maintenance_request', 'confirmed', 'email', 'investigation', %s, %s)
        RETURNING id
        """,
        (title, detail[:500], product["id"]),
    )
    maintenance_event_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO personal.event
            (title, event_type, status, provenance, calendar_source, notes,
             product_id, parent_event_id, investigation_status, investigation_status_changed_at)
        VALUES (%s, 'investigation', 'confirmed', 'rule', 'investigation', %s,
                %s, %s, 'needs_review', now())
        RETURNING id
        """,
        (f"Investigation: {product['name']}", reason, product["id"], maintenance_event_id),
    )
    investigation_event_id = cur.fetchone()["id"]

    print(f"[decompose] product completeness gap on {product['name']!r} (product {product['id']}) — "
          f"created investigation event {investigation_event_id} (maintenance event {maintenance_event_id}): {reason}")

    _apply_familybrain_label(acct, provider_msg_id, "knowledge-identified")

    # 4c — draft the follow-up per the matched rule's follow_up_action.
    follow_up_action = rule.get("follow_up_action", "notify_only")
    _create_followup(cur, investigation_event_id, product, reason, result.get("evidence", {}),
                      follow_up_action, acct)


def _resolve_gmail_draft_account() -> dict | None:
    """
    All Increment 4 follow-up drafts go through the household's primary
    Gmail account regardless of which connected account received the
    original invoice — same convention weekly_digest.py already uses
    (main.py: provider == 'gmail' and is_primary_calendar).
    """
    from .db import get_enabled_accounts
    accounts = get_enabled_accounts()
    return next((a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")), None)


# Language the spec explicitly forbids a follow-up from ever containing —
# stating a conclusion about coverage rather than asking about it.
_CONCLUSION_LANGUAGE = ("expired", "has lapsed", "no longer covered", "not covered", "is void", "voided")


def _compose_evidence_note(product: dict, reason: str, evidence: dict) -> str:
    """
    Evidence-quoting only — states what's documented, asks the specific
    missing-field question, never concludes a warranty/coverage status
    (spec 4c: *"installed [date] per invoice [ref], no warranty period on
    file — can you confirm coverage?"*, never *"your warranty has likely
    expired."*). Tries the model for natural phrasing; falls back to a
    template built directly from the evidence (no model call, can't
    hallucinate) whenever the model's own output slips into forbidden
    conclusion-language or the call fails outright.

    This mirrors wa-agent/src/synthesis.py's grounded/always-have-a-safe-
    fallback design principle without importing that module across the
    email-sync/wa-agent service boundary — its 7-section, glyph-marked
    WhatsApp digest contract doesn't fit a one-paragraph vendor email or
    internal note, so this is a small, deliberately separate composer for
    that different shape, not a second implementation of the same one.
    """
    bits = []
    if evidence.get("install_date"):
        bits.append(f"installed {evidence['install_date']}")
    if evidence.get("source_doc_ref"):
        bits.append(f"per {evidence['source_doc_ref']}")
    evidence_str = ", ".join(bits) if bits else "on file, but with limited detail"
    template = f"{product['name']} was {evidence_str}. {reason} Can you confirm?"

    try:
        prompt = (
            "Write ONE short, polite paragraph (2-3 sentences) for a follow-up email or internal note "
            "about a product. State ONLY the evidence given below as fact — do not add any detail not "
            "listed. Ask the specific question implied by the gap. Never claim or imply a warranty or "
            "coverage has expired, lapsed, or is void — state only what is documented and ask, do not "
            "conclude.\n\n"
            f"Product: {product['name']}\n"
            f"Evidence on file: {evidence_str}\n"
            f"Gap: {reason}"
        )
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.3}},
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        if text and not any(b in text.lower() for b in _CONCLUSION_LANGUAGE):
            return text
        print(f"[decompose] evidence-note contained forbidden conclusion-language or was empty — using template")
    except Exception as e:
        print(f"[decompose] evidence-note composition failed, using template: {e}")
    return template


def _create_followup(cur, investigation_event_id: int, product: dict, reason: str,
                      evidence: dict, follow_up_action: str, acct: dict | None) -> None:
    """4c — dispatch the matched investigation_rule's follow_up_action."""
    if follow_up_action == "draft_vendor_email" and not product.get("vendor_org_ref"):
        print(f"[decompose] draft_vendor_email requested but product {product['id']} has no vendor_org_ref — falling back to draft_internal_note")
        follow_up_action = "draft_internal_note"

    if follow_up_action == "draft_vendor_email":
        gmail_acct = _resolve_gmail_draft_account()
        if not gmail_acct:
            print("[decompose] no primary Gmail account available for vendor draft — falling back to draft_internal_note")
            follow_up_action = "draft_internal_note"
        else:
            body = _compose_evidence_note(product, reason, evidence)
            try:
                from . import gmail as gmail_mod
                draft_id, thread_id, message_id = gmail_mod.create_draft(
                    gmail_acct, product["vendor_org_ref"],
                    f"Follow-up: {product['name']}", body,
                )
                cur.execute(
                    """UPDATE personal.event
                       SET investigation_status = 'awaiting_reply', investigation_status_changed_at = now(),
                           draft_thread_id = %s
                       WHERE id = %s""",
                    (thread_id, investigation_event_id),
                )
                _apply_familybrain_label(gmail_acct, message_id, "investigation-pending")
                print(f"[decompose] vendor draft created (draft {draft_id}, thread {thread_id}) for investigation {investigation_event_id}")
            except Exception as e:
                print(f"[decompose] vendor draft creation failed for investigation {investigation_event_id}: {e}")
            return

    if follow_up_action == "draft_internal_note":
        body = _compose_evidence_note(product, reason, evidence)
        cur.execute(
            """INSERT INTO personal.note (source, body, tags, item_type, document_date)
               VALUES ('investigation', %s, %s, 'task', CURRENT_DATE)""",
            (f"Investigation follow-up: {product['name']}\n\n{body}", ["investigation"]),
        )
        cur.execute(
            """UPDATE personal.event
               SET investigation_status = 'drafted', investigation_status_changed_at = now()
               WHERE id = %s""",
            (investigation_event_id,),
        )
        print(f"[decompose] internal note drafted for investigation {investigation_event_id}")
        return

    # notify_only — the investigation event itself is already
    # dashboard/query-visible, which serves as the notification. No draft,
    # no reply to wait for, so investigation_status stays 'needs_review'.


# Same fixed allowlist as graph-api's check_product_completeness.py —
# duplicated deliberately (small, cross-service, same established tolerance)
# rather than trusting an interpolated column name from an HTTP response.
_PRODUCT_FIELD_TYPES = {
    "warranty_period_months": "int",
    "warranty_expiry_date": "date",
    "install_date": "date",
    "cost": "numeric",
    "vendor_org_ref": "text",
}


def _extract_missing_field(body: str, field_name: str):
    """
    Lightweight, single-field extraction from a reply body — deliberately
    narrow (ask for exactly the one value check_product_completeness said
    was missing) rather than a general structured-extraction pass. Returns
    None (leave the investigation awaiting_reply) if the field genuinely
    isn't answered yet — never guesses.
    """
    if field_name not in _PRODUCT_FIELD_TYPES:
        return None
    prompt = (
        f'Extract the value of "{field_name}" from this email reply. '
        f"Reply with ONLY the value — a plain number, a date as YYYY-MM-DD, or short text — "
        f"or the single word NONE if it isn't actually answered.\n\n{body[:1500]}"
    )
    try:
        resp = req.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        if not raw or raw.upper().startswith("NONE"):
            return None
        ftype = _PRODUCT_FIELD_TYPES[field_name]
        if ftype == "int":
            m = re.search(r"\d+", raw)
            return int(m.group()) if m else None
        if ftype == "date":
            m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
            return m.group() if m else None
        if ftype == "numeric":
            m = re.search(r"[\d.]+", raw)
            return float(m.group()) if m else None
        return raw[:500]
    except Exception as e:
        print(f"[decompose] field extraction failed for {field_name!r}: {e}")
        return None


def _resolve_investigation(cur, investigation_id: int, acct: dict | None, reply_msg_id: str) -> None:
    cur.execute(
        """UPDATE personal.event
           SET investigation_status = 'resolved', investigation_status_changed_at = now()
           WHERE id = %s""",
        (investigation_id,),
    )
    if acct and acct.get("provider") == "gmail" and reply_msg_id:
        try:
            from . import gmail as gmail_mod
            svc = gmail_mod._gmail_service(acct)
            gmail_mod.swap_ingested_label(acct, svc, reply_msg_id, "investigation-pending", "investigation-resolved")
        except Exception as e:
            print(f"[decompose] investigation-resolved label swap failed: {e}")
    elif acct and acct.get("provider") == "outlook" and reply_msg_id:
        # No category-removal path exists for Outlook yet — additive only,
        # same limitation noted for the pending label. Investigation status
        # in Postgres is the source of truth regardless.
        _apply_familybrain_label(acct, reply_msg_id, "investigation-resolved")


def _check_investigation_reply(cur, acct: dict | None, provider_msg_id: str,
                                 thread_id: str | None, body: str) -> None:
    """
    4d — a reply's thread_id matching an awaiting_reply investigation's
    draft_thread_id is the resolution trigger: a plain equality check, the
    first real use of thread_id anywhere in this codebase (captured on every
    email_message row all along, never joined/filtered on before this
    increment — deliberately chosen over the title-token-overlap heuristic
    used for calendar dedup, since here we already know exactly which
    thread we're waiting on).
    """
    if not thread_id:
        return
    cur.execute(
        """
        SELECT id, product_id FROM personal.event
        WHERE draft_thread_id = %s AND investigation_status = 'awaiting_reply'
        """,
        (thread_id,),
    )
    inv = cur.fetchone()
    if not inv:
        return

    try:
        resp = req.get(
            f"{GRAPH_API_URL}/interrogate/check_product_completeness",
            params={"product_id": inv["product_id"], "trigger_event_type": "maintenance_request"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()["result"]
    except Exception as e:
        print(f"[decompose] resolution completeness check failed for investigation {inv['id']}: {e}")
        return

    missing_field = result.get("missing_field")
    if not missing_field:
        # Already complete via some other path (e.g. manual dashboard edit) —
        # just close the investigation out.
        print(f"[decompose] investigation {inv['id']} already complete — resolving")
        _resolve_investigation(cur, inv["id"], acct, provider_msg_id)
        return

    extracted = _extract_missing_field(body, missing_field)
    if extracted is None:
        print(f"[decompose] reply for investigation {inv['id']} didn't answer {missing_field} — leaving awaiting_reply")
        return

    cur.execute(
        f"UPDATE personal.product SET {missing_field} = %s, updated_at = now() WHERE id = %s",
        (extracted, inv["product_id"]),
    )
    print(f"[decompose] investigation {inv['id']} resolved — {missing_field} = {extracted!r}")
    _resolve_investigation(cur, inv["id"], acct, provider_msg_id)


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


def _resolve_routine_asset(text: str, person_id: int | None) -> tuple[int | None, int | None]:
    """
    Match event text against a routine asset's name or synonyms — this is
    what ties an email like "Beginner Strings Blue - Term 3" or "Melodies
    Choir - Upcoming Performances" back to the right routine even when it
    doesn't land on the exact date of a generated placeholder (the only
    other linkage mechanism, via slot_key in _supersede_placeholder/
    _enrich_asset_from_confirmed — informational/schedule emails usually
    don't hit that path at all).
    Longest matching name/synonym wins so a more specific match (e.g.
    "beginner strings blue") beats a shorter one (e.g. "strings").

    Returns (asset_id, asset_person_id) — an event whose own text never names
    a person ("Gold Coast Eisteddfod" alone says nothing about who's
    attending) can still inherit one from the routine it just matched, since
    an event that isn't tied to a person/entity/property is close to
    meaningless on its own — something has to be the subject of it.
    """
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                if person_id:
                    cur.execute(
                        "SELECT id, name, synonyms, person_id FROM personal.asset "
                        "WHERE asset_type = 'routine' AND status = 'active' "
                        "AND (person_id = %s OR person_id IS NULL)",
                        (person_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, name, synonyms, person_id FROM personal.asset "
                        "WHERE asset_type = 'routine' AND status = 'active'"
                    )
                routines = cur.fetchall()
        text_lower = text.lower()

        best_id, best_person_id, best_len = None, None, 0
        for routine in routines:
            candidates = [routine["name"]] + list(routine.get("synonyms") or [])
            for cand in candidates:
                cand_lower = (cand or "").lower().strip()
                if len(cand_lower) > 2 and cand_lower in text_lower and len(cand_lower) > best_len:
                    best_id, best_person_id, best_len = routine["id"], routine.get("person_id"), len(cand_lower)
        return best_id, best_person_id
    except Exception:
        return None, None


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


def _find_similar_existing_event(cur, title: str, effective_dt, exclude_id: int) -> int | None:
    """Does an existing event already describe the same real-world thing?

    Same shape as ingestor's triage.py _content_already_processed, applied on
    the calendar-create side: upsert_event's own dedup only catches
    exact/substring title matches, which misses e.g. LLM-extracted "Vacate
    Notice" against a manually-created "Vacate 215a Drews Road, Loganholme
    (Notice to Leave)" for the same date — the exact pair that produced a
    live duplicate (event 1119044 vs 1118795). Require significant token
    overlap (unique [a-z0-9]{4,} tokens, threshold ~all-but-one) AND the same
    effective_date — title alone or date alone both over-match (two distinct
    property events on a busy day; two unrelated docs sharing address
    tokens). Fail toward returning None (no supersede, event stands as-is)
    on any uncertainty — a missed duplicate costs a stray calendar entry, a
    wrong match silently buries a real distinct event.
    """
    tokens = list(dict.fromkeys(re.findall(r'[a-z0-9]{4,}', title.lower())))
    if not tokens:
        return None
    need = max(2, len(tokens) - 1) if len(tokens) >= 2 else 1
    cur.execute(
        """
        SELECT id FROM personal.event
        WHERE id != %(exclude_id)s
          AND effective_date = %(eff)s
          AND status NOT IN ('cancelled', 'superseded')
          AND (SELECT count(*) FROM unnest(%(toks)s::text[]) t
               WHERE position(t IN lower(title)) > 0) >= %(need)s
        ORDER BY created_at ASC
        LIMIT 1
        """,
        {"exclude_id": exclude_id, "eff": effective_dt, "toks": tokens, "need": need},
    )
    row = cur.fetchone()
    return row["id"] if row else None


# Commercial show/event advertising — a season or a session we were told
# about, not a booking we hold. Deliberately venue/box-office flavoured so a
# school concert or recital (which the family really does attend) is untouched.
_PROMO_KW = re.compile(
    r'\b(the musical|now showing|opening night|box office|matinee|cabaret|'
    r'on sale now|tickets? (?:from|on sale)|book now|limited season|'
    r'playing (?:now|until|from|\d)|final weeks?|world premiere|'
    r'live in concert|comedy festival|touring)\b', re.I)
# Evidence we actually hold a seat for it.
# Note: deliberately NOT a bare "your tickets" — promo copy says "get your
# tickets now", so that phrase is advertising, not proof of a booking.
_BOOKED_KW = re.compile(
    r'\b(booking (?:reference|number|confirmed)|your (?:booking|reservation)|'
    r'your tickets? (?:are|is|have been|for)|order #|confirmation (?:number|code)|'
    r'e-?ticket|tickets? attached|reservation confirmed|seat [a-z]?\d+)\b', re.I)


def _is_unbooked_promo(title: str, detail: str, body: str) -> bool:
    """Is this a show being advertised to us rather than one we've booked?

    "Menopause The Musical ... Playing 30 Sept" came out of a promotional
    email and was written straight into the Family calendar as though it were
    an appointment. Those belong on Tentative until a booking exists.
    """
    text = f"{title} {detail}"
    if not _PROMO_KW.search(text) and not _PROMO_KW.search(body[:2000]):
        return False
    return not _BOOKED_KW.search(f"{text} {body[:2000]}")


# Things that genuinely run for days. Everything else with a long span is the
# LLM having paired a start date with an unrelated later date in the email.
_MULTI_DAY_KW = re.compile(
    r'\b(holiday|holidays|school break|break|vacation|camp|cruise|trip|tour|'
    r'festival|conference|retreat|term \d|semester|leave|exhibition|'
    r'stay|away|season|program(?:me)?|championships?|carnival)\b', re.I)
# A deadline is a moment, never a range, however many dates surround it.
_DEADLINE_KW = re.compile(
    r'\b(deadline|due|closes?|closing|expires?|expiry|cut[- ]?off|'
    r'last day|final day|rsvp|submit by)\b', re.I)
_MAX_UNEXPLAINED_SPAN_DAYS = 3


def _sane_end_date(title: str, detail: str, date_str: str, end_str):
    """Drop an end_date that would smear a one-off event across the calendar.

    "Applications ... open until Friday 11th September 2026" became an all-day
    event from 11 Sep to 17 Oct — a five-week banner sitting across every day
    in Google Calendar, still visible weeks after the deadline passed, because
    the model paired the deadline with an unrelated later date in the email.
    Long spans are kept only when the text actually describes something
    multi-day (a holiday, a camp, a conference).
    """
    if not end_str:
        return end_str
    try:
        span = (date.fromisoformat(end_str) - date.fromisoformat(date_str)).days
    except (TypeError, ValueError):
        return None
    if span <= 0:
        return None
    text = f"{title} {detail}"
    if _DEADLINE_KW.search(title) or (
            span > _MAX_UNEXPLAINED_SPAN_DAYS and not _MULTI_DAY_KW.search(text)):
        print(f"[decompose] dropped end_date {end_str} for '{title[:40]}' "
              f"({span}d span, nothing says it runs that long) — single-day event")
        return None
    return end_str


_FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.com.au", "icloud.com", "me.com",
    "bigpond.com", "bigpond.net.au", "optusnet.com.au",
}


def _find_same_sender_event(cur, from_address: str | None, effective_dt,
                            time_str: str | None, exclude_id: int) -> int | None:
    """Is this the same meeting an organisation already told us about?

    A booking tends to arrive several times from one counterparty — the
    booking-system confirmation, then an assistant's "just confirming Joe will
    meet you at 11:00am on the 21st", then a "switching to Zoom" note — each
    extracted under a different title ("Discovery Call" vs "Meeting with Joe"),
    so the title-token check in _find_similar_existing_event never matches
    them. Same sender organisation + same date is the stronger signal:
      - both timed and the times agree → same meeting
      - incoming untimed → same meeting only if that sender rarely produces
        events (≤3 in ±30 days), so a school sending several same-day items
        doesn't get collapsed
    Free-mail senders are excluded — the domain says nothing about who they are.
    """
    if not from_address or "@" not in from_address:
        return None
    domain = from_address.rsplit("@", 1)[1].strip().lower().rstrip(">")
    if not domain or domain in _FREE_MAIL_DOMAINS:
        return None
    pattern = f"%From: %@{domain}%"
    cur.execute(
        """
        SELECT id,
               to_char(starts_at AT TIME ZONE 'Australia/Brisbane', 'HH24:MI') AS hhmm,
               starts_at::time <> '00:00'::time AS timed
        FROM personal.event
        WHERE id != %(exclude_id)s
          AND effective_date = %(eff)s
          AND status NOT IN ('cancelled', 'superseded')
          AND notes ILIKE %(pat)s
        ORDER BY created_at ASC
        """,
        {"exclude_id": exclude_id, "eff": effective_dt, "pat": pattern},
    )
    same_day = cur.fetchall()
    if not same_day:
        return None
    if time_str:
        for row in same_day:
            if row["timed"] and row["hhmm"] == time_str:
                return row["id"]
        return None
    if len(same_day) != 1:
        return None
    cur.execute(
        """
        SELECT count(*) AS n FROM personal.event
        WHERE status NOT IN ('cancelled', 'superseded')
          AND notes ILIKE %(pat)s
          AND effective_date BETWEEN %(eff)s - 30 AND %(eff)s + 30
        """,
        {"pat": pattern, "eff": effective_dt},
    )
    return same_day[0]["id"] if cur.fetchone()["n"] <= 3 else None


_DAY_SUFFIX = r'(?:st|nd|rd|th)'
_MONTH_WORD = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
# Explicit day-of-month mentions: "the 21st", "21 September", "September 21",
# "21/09". Each yields (day, month-or-None, match span).
_EXPLICIT_DAY_RES = [
    re.compile(rf'\b(\d{{1,2}})\s*{_DAY_SUFFIX}?\s+(?:of\s+)?{_MONTH_WORD}', re.I),
    re.compile(rf'\b{_MONTH_WORD}\s+(\d{{1,2}}){_DAY_SUFFIX}?\b', re.I),
    re.compile(rf'\b(\d{{1,2}}){_DAY_SUFFIX}\b', re.I),
    re.compile(r'\b(\d{1,2})[/.](\d{1,2})(?:[/.]\d{2,4})?\b'),
]
_RELATIVE_DATE_RE = re.compile(
    r'\b(today|tonight|tomorrow|next (?:week|month)|this (?:week|weekend)|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'in \d+ (?:days?|weeks?|months?)|\+\s*\d+\s*(?:days?|weeks?))\b', re.I)
_TIME_NEAR_RE = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(\d{1,2}):(\d{2})\b', re.I)


def _explicit_day_refs(text: str) -> list[tuple[int, int | None, tuple[int, int]]]:
    refs = []
    for i, rx in enumerate(_EXPLICIT_DAY_RES):
        for m in rx.finditer(text):
            try:
                if i == 0:
                    day, month = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()]
                elif i == 1:
                    day, month = int(m.group(2)), _MONTHS[m.group(1)[:3].lower()]
                elif i == 2:
                    day, month = int(m.group(1)), None
                else:
                    day, month = int(m.group(1)), int(m.group(2))
            except (KeyError, ValueError):
                continue
            if 1 <= day <= 31 and (month is None or 1 <= month <= 12):
                refs.append((day, month, m.span()))
    return refs


def _ground_event_date(item: dict, body: str) -> None:
    """Correct an LLM calendar date that disagrees with a day the email states.

    The 14b model will turn "Joe will meet you at 11:00am on the 21st" into
    date 2026-09-22 with no time. When the email names days explicitly and the
    LLM's day isn't one of them, snap to the single stated day within ±3 days
    (picking up an adjacent time like "11:00am" if the LLM dropped it).
    Left alone when the date is relative/derived, when the email mentions
    relative days ("next Tuesday") we can't verify, or when the evidence is
    ambiguous — this only fixes clear misreads, it never discards events.
    """
    date_str = item.get("date")
    if not date_str or item.get("relative_to") or item.get("relative_offset_days"):
        return
    try:
        llm_date = date.fromisoformat(date_str)
    except ValueError:
        return
    text = body[:4000]
    refs = _explicit_day_refs(text)
    if not refs:
        return
    if any(d == llm_date.day and (mo is None or mo == llm_date.month) for d, mo, _ in refs):
        return
    if _RELATIVE_DATE_RE.search(text):
        return
    nearby = {}
    for d, mo, span in refs:
        try:
            cand = date(llm_date.year, mo or llm_date.month, d)
        except ValueError:
            continue
        if abs((cand - llm_date).days) <= 3:
            nearby.setdefault(cand, span)
    if len(nearby) != 1:
        return
    (cand, span), = nearby.items()
    print(f"[decompose] date grounding: '{item.get('title', '')[:40]}' {date_str} → {cand} (stated in email)")
    item["date"] = cand.isoformat()
    if not item.get("time"):
        window = text[max(0, span[0] - 40):span[1] + 40]
        tm = _TIME_NEAR_RE.search(window)
        if tm:
            if tm.group(3):
                h, mi = int(tm.group(1)), int(tm.group(2) or 0)
                h = h % 12 + (12 if tm.group(3).lower() == "pm" else 0)
            else:
                h, mi = int(tm.group(4)), int(tm.group(5))
            if 0 <= h < 24 and 0 <= mi < 60:
                item["time"] = f"{h:02d}:{mi:02d}"


def _create_calendar_event(cur, item: dict, calendar_source: str, email_id: int,
                             ingestor_url: str, received_date: str = "",
                             title_to_event_id: dict | None = None,
                             pre_extracted_meeting_url: str | None = None,
                             email_meta: dict | None = None,
                             email_body: str = "") -> int | None:
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

    end_str = _sane_end_date(title, detail, date_str, end_str)

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
            location=location,
        )

        if not event_id:
            return None

        effective_dt = date.fromisoformat(date_str)
        dup_of = (_find_similar_existing_event(cur, title, effective_dt, exclude_id=event_id)
                  or _find_same_sender_event(cur, (email_meta or {}).get("from_address"),
                                             effective_dt, time_str, exclude_id=event_id))
        if dup_of:
            cur.execute(
                "UPDATE personal.event SET status = 'superseded', superseded_by_event_id = %s WHERE id = %s",
                (dup_of, event_id),
            )
            # A follow-up about the same meeting often carries the newest
            # logistics (Zoom link, venue) — keep them on the surviving event
            if meeting_url or location:
                cur.execute(
                    """UPDATE personal.event
                       SET meeting_url = COALESCE(%s, meeting_url),
                           location    = COALESCE(%s, location)
                       WHERE id = %s""",
                    (meeting_url, location, dup_of),
                )
            print(f"[decompose] '{title[:40]!r}' on {date_str} duplicates existing event {dup_of} — superseded {event_id}")
            if title_to_event_id is not None:
                title_to_event_id[title.lower().strip()] = dup_of
            return dup_of

        # Stage 2: set provenance/status and attempt slot override
        event_type   = item.get("event_type", "inferred").upper()

        # Resolve person from title + detail
        person_id = _resolve_person_id(f"{title} {detail}")

        # Resolve routine asset by name/synonym match — the only other route to
        # this (slot_key override in _supersede_placeholder below) requires
        # landing on the exact date of a generated placeholder, which
        # informational/schedule emails ("Beginner Strings Blue - Term 3",
        # "Melodies Choir - Upcoming Performances") usually never do.
        routine_asset_id, routine_person_id = _resolve_routine_asset(f"{title} {detail}", person_id)
        # An event whose text names no person ("Gold Coast Eisteddfod" alone
        # says nothing about who's attending) inherits one from the routine —
        # every event should trace back to a person/entity/property, not float
        # unattached.
        person_id = person_id or routine_person_id

        # Classify incoming event
        from .slot_classify import classify as _classify_slot
        slot_class, blocks_person, rank = _classify_slot(event_type)
        slot_key = f"{person_id}:{effective_dt}:{slot_class}" if person_id else None
        needs_review = False

        # Update event with provenance/status and slot fields.
        # Status stays 'confirmed' — events go to GCal regardless of whether we resolved
        # a person (concerts, family events, etc. have no person but are still valid).
        # Exception: a show we were merely advertised goes in suspended, which
        # the calendar writer routes to Tentative with the reason attached.
        if _is_unbooked_promo(title, detail, email_body or ""):
            ev_status, susp_reason = "suspended", "advertised show — no booking held"
            print(f"[decompose] '{title[:40]}' looks advertised, not booked — routing to Tentative")
        else:
            ev_status, susp_reason = "confirmed", None
        cur.execute("""
            UPDATE personal.event
            SET provenance      = 'email',
                status          = %s,
                suspended_reason = %s,
                slot_key        = %s,
                slot_class      = %s,
                blocks_person   = %s,
                precedence_rank = %s,
                person_id       = COALESCE(person_id, %s),
                asset_id        = COALESCE(asset_id, %s),
                occurrence_date = %s
            WHERE id = %s
        """, (ev_status, susp_reason, slot_key, slot_class, blocks_person, rank,
              person_id, routine_asset_id, effective_dt, event_id))
        if routine_asset_id:
            print(f"[decompose] linked event {event_id} ({title[:40]!r}) to routine asset {routine_asset_id}")

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


def _process_one_email(email: dict, accounts: list[dict], calendar_source: str) -> list[int]:
    """
    Decompose a single already-fetched email row (as returned by the SELECT in
    decompose_emails / decompose_email_by_id) into notes/events. Returns the ids
    of any personal.event rows created (via calendar_event items) — empty list
    if nothing was created or the email failed to process.

    Never raises — mirrors decompose_emails' original per-email try/except so
    one bad email doesn't take down a batch run.
    """
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

    # 4d — is this email a reply to a drafted investigation follow-up?
    # Independent of decomposition below: runs whether or not the LLM
    # extracts any items, and regardless of body/attachment presence.
    try:
        with psycopg2.connect(DB_URL) as rconn:
            with rconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as rcur:
                _check_investigation_reply(rcur, acct, provider_id, email.get("thread_id"), body)
            rconn.commit()
    except Exception as e:
        print(f"[decompose] investigation-reply check failed for email {email_id}: {e}")

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

    title_to_event_id: dict = {}
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
        for item in items:
            itype = item.get("type")
            title = item.get("title", "")
            detail = item.get("detail", "")

            if itype == "calendar_event":
                _ground_event_date(item, body)
                # A meeting dated before the email arrived already happened
                # ("thanks for your time on Zoom yesterday") — it's a record,
                # not something to put in the calendar
                if item.get("date") and received_at and item["date"] < received_at[:10]:
                    print(f"[decompose] '{title[:40]}' dated {item['date']} is before the email "
                          f"({received_at[:10]}) — keeping as observation, not an event")
                    itype = "observation"

            with psycopg2.connect(DB_URL) as wconn:
                with wconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as wcur:
                    if itype == "calendar_event":
                        _create_calendar_event(wcur, item, calendar_source,
                                                email_id, INGESTOR_URL,
                                                received_date=received_at,
                                                title_to_event_id=title_to_event_id,
                                                pre_extracted_meeting_url=pre_meeting_url,
                                                email_meta=email_meta,
                                                email_body=body)

                    elif itype == "payment":
                        _create_payment_note(wcur, item, email_id, received_at)

                    elif itype == "observation":
                        _create_note(wcur, email_id, title, detail, itype, [], received_at)

                    elif itype == "task":
                        priority = item.get("priority", "normal")
                        task_event_id = _create_task_event(wcur, email_id, title, detail,
                                                            item.get("date"), priority)
                        # Increment 4, 4b — does this task concern an existing
                        # Product with a documented knowledge gap (e.g. no
                        # warranty on file)? Same transaction/connection as
                        # the event write above.
                        _check_product_investigation(wcur, email_id, acct, provider_id, title, detail)
                wconn.commit()

        with psycopg2.connect(DB_URL) as wconn:
            with wconn.cursor() as wcur:
                wcur.execute(
                    "UPDATE personal.email_message SET email_decomposed = true WHERE id = %s",
                    (email_id,),
                )
            wconn.commit()

        return list(title_to_event_id.values())

    except Exception as e:
        err_str = str(e).lower()
        # Network/API errors — leave email_decomposed = false so it retries next cycle
        is_transient = isinstance(e, DecomposeUnavailable) or any(x in err_str for x in (
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
        return list(title_to_event_id.values())


def decompose_email_by_id(email_id: int, accounts: list[dict]) -> list[int]:
    """
    Targeted decompose for item_review — processes exactly one email regardless
    of its received_at/category/backlog position, so a just-recovered (possibly
    months-old) email is handled immediately instead of waiting behind the
    normal batch ordering. Returns ids of any personal.event rows created.
    """
    calendar_source = "email:decompose"
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT em.id, em.subject, em.from_address, em.received_at,
                       em.account_id, em.provider_msg_id, em.note_id, em.thread_id,
                       n.body AS note_body
                FROM   personal.email_message em
                LEFT   JOIN personal.note n ON n.id = em.note_id
                WHERE  em.id = %s
                """,
                (email_id,),
            )
            email = cur.fetchone()

    if not email:
        return []
    return _process_one_email(email, accounts, calendar_source)


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
                       em.account_id, em.provider_msg_id, em.note_id, em.thread_id,
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
        _process_one_email(email, accounts, calendar_source)
        processed += 1

    return processed
