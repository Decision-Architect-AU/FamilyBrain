"""
Appointment updater.

The single process that writes events to Google Calendar.
All sources (Gmail sync, Outlook sync, email decomposer, bill calendar, voice)
write to personal.event + the knowledge graph. This updater then pushes to GCal.

Poll condition (any of):
  - gcal_event_id IS NULL              → never written yet
  - updated_at > calendar_written_at   → source event changed
  - next_update_at <= now()            → scheduled re-evaluation

After writing, sets:
  - calendar_written_at = now()
  - next_update_at = <rule-based schedule> or NULL

next_update_at rules:
  - Event is in the future + more than 7 days away  → re-check 3 days before
  - Event is tomorrow or today                       → re-check day-of (final enrichment)
  - Event is in the past                             → NULL (done)
  - Bill event with no amount                        → tomorrow (retry enrichment)
"""
import os
import re
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, date, timedelta
from googleapiclient.discovery import build

_JOIN_LINK_RE = re.compile(
    r'https?://\S*(?:teams\.microsoft\.com|zoom\.us/j|meet\.google\.com|webex\.com/meet|gotomeeting\.com|whereby\.com|bluejeans\.com)\S*',
    re.IGNORECASE,
)

from .db import get_enabled_accounts, conn
from .gmail import _gmail_service, _fmt_cal_dt
from .calendar_router import (
    classify_event, target_calendar_id, tag_family_event,
    expand_holiday_days, load_routing, _TAG_COLORS,
    partner_event_involves_owner,
)

DB_URL      = os.environ["DATABASE_URL"]
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://172.23.96.1:11434")
AGENT_MODEL = os.environ.get("MODEL_PARSER_2ND", os.environ.get("AGENT_MODEL", "qwen2.5:14b"))
_BATCH      = 50

_PARTNER_NAMES = [n.strip().lower() for n in os.environ.get("PARTNER_NAMES", "").split(",") if n.strip()]

# Titles that are "thin" — worth trying to enrich via graph lookup
_THIN_TITLE = re.compile(
    r'\b(dr|doctor|dentist|specialist|appointment|appt|physio|'
    r'physiotherapy|speech|ot|therapy|chiro|optometrist|checkup|check.up|'
    r'consult|consultation|review|meeting|catch.?up)\b',
    re.I,
)


def _graph_context_for_event(title: str, notes: str) -> str:
    """
    Search personal_graph notes for context relevant to this event.
    Returns a text block of relevant snippets, or empty string.
    """
    query = f"{title} {notes}".strip()[:300]
    try:
        import psycopg2, psycopg2.extras

        # Embed the query
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": os.environ.get("EMBED_MODEL", "nomic-embed-text"), "prompt": query},
            timeout=15,
        )
        resp.raise_for_status()
        vec = resp.json()["embedding"]
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"

        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    SELECT body FROM personal.note
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT 5
                    """,
                    (vec_str,),
                )
                rows = cur.fetchall()
        return "\n---\n".join(r["body"][:400] for r in rows if r["body"])
    except Exception as e:
        print(f"[appt] graph context lookup failed: {e}")
        return ""


def _llm_enrich(title: str, notes: str, context: str, starts_at) -> tuple[str, str]:
    """
    Use LLM to suggest an enriched title and description for a thin appointment.
    Returns (enriched_title, enriched_description). Falls back to originals on error.
    """
    date_str = starts_at.strftime("%A %d %B %Y") if hasattr(starts_at, "strftime") else str(starts_at)
    prompt = f"""You are enriching a calendar appointment using information from a personal knowledge graph.

Appointment: "{title}"
Date: {date_str}
Current notes: {notes or "(none)"}

Relevant context from knowledge graph:
{context or "(none)"}

Task: Write an enriched calendar title and description using the context above.
- Title: specific and informative (include person name, practitioner name, clinic if known). Max 60 chars.
- Description: 1-3 sentences with useful details (location, what to bring, purpose). Leave blank if nothing useful to add.
- Only include information you are confident about from the context. Do not invent details.
- If context has nothing relevant, keep the original title and leave description blank.

Reply in this exact format:
TITLE: <enriched title>
DESCRIPTION: <description or blank>"""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": AGENT_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 150}},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        new_title = title
        new_desc  = notes
        # Preserve any meeting join links from the original notes
        join_links = _JOIN_LINK_RE.findall(notes)
        for line in text.splitlines():
            if line.upper().startswith("TITLE:"):
                t = line[6:].strip()
                if t and len(t) <= 80:
                    new_title = t
            elif line.upper().startswith("DESCRIPTION:"):
                new_desc = line[12:].strip()
        if join_links:
            link_block = "\n".join(join_links)
            new_desc = f"{new_desc}\n\n{link_block}".strip() if new_desc else link_block
        return new_title, new_desc
    except Exception as e:
        print(f"[appt] LLM enrich failed: {e}")
        return title, notes


def _try_enrich(title: str, notes: str, starts_at) -> tuple[str, str]:
    """
    If the title looks thin, attempt graph-backed LLM enrichment.
    Returns (title, notes) — possibly unchanged.
    """
    if not _THIN_TITLE.search(title):
        return title, notes
    context = _graph_context_for_event(title, notes)
    if not context:
        return title, notes
    return _llm_enrich(title, notes, context, starts_at)


def _load_people() -> dict[int, str]:
    """Return {person_id: first_name} for all persons."""
    with psycopg2.connect(DB_URL) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM personal.person")
            return {r["id"]: r["name"].split()[0] for r in cur.fetchall()}


def _names_from_env(var: str) -> list[str]:
    raw = os.environ.get(var, "")
    return [n.strip() for n in raw.split(",") if n.strip()]

# Keywords that belong to a specific child — used for title enrichment
# even when person_id isn't set (e.g. after a fresh calendar resync)
_CHILD2_FIRST = (_names_from_env("CHILD2_NAMES") or [""])[0]

_CHILD2_TITLE_KW = [
    "physio", "physiotherapy", "speech therapy", "speech pathology",
    "occupational therapy", "weekly ot",
]


def _load_school_year_map() -> dict[int, str]:
    """
    Return {school_year: first_name} for all persons with a school_year set.
    Used to derive child tags from year-level mentions in event titles.
    e.g. {3: "Elliana", 1: "Olivia"}
    """
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT name, school_year FROM personal.person WHERE school_year IS NOT NULL"
                )
                return {r["school_year"]: r["name"].split()[0] for r in cur.fetchall()}
    except Exception:
        return {}


def _child_from_school_year(title: str, school_year_map: dict[int, str]) -> str | None:
    """
    If title contains 'Year N' or 'Yr N', return the child's first name for that year.
    e.g. "Year 3 Assembly" + {3: "Elliana"} → "Elliana"
    """
    m = re.search(r'\b(?:year|yr)\s*(\d+)\b', title, re.I)
    if m:
        return school_year_map.get(int(m.group(1)))
    return None


def _enrich_title(title: str, person_name: str | None, notes: str,
                  school_year_map: dict[int, str] | None = None) -> str:
    """
    Prefix title with person name when the event belongs to a specific person.
    Priority: explicit person_id → school year derivation → therapy keyword fallback.
    e.g. "Year 3 Assembly" → "Ellie Year 3 Assembly" (derived from school_year_map)
         "Physio"          → "Olivia Physio"          (therapy keyword)
    """
    tl = title.lower()

    name = person_name
    if not name and school_year_map:
        name = _child_from_school_year(title, school_year_map)
    if not name:
        child2 = _CHILD2_FIRST
        if child2 and any(kw in tl for kw in _CHILD2_TITLE_KW):
            name = child2

    if not name:
        return title
    if tl.startswith(name.lower()):
        return title   # already prefixed
    return f"{name} {title}"


# ── Google Calendar helpers ───────────────────────────────────────────────────

def _cal_service(gmail_account: dict):
    svc   = _gmail_service(gmail_account)
    creds = svc._http.credentials
    return build("calendar", "v3", credentials=creds)


def _patch_outlook_source(ev_id: int, title: str, notes: str, outlook_accounts: list[dict]) -> bool:
    """
    Patch the original Outlook calendar event with the enriched title + description.
    Looks up the Outlook provider_id from calendar_sync_map, then calls PATCH via Graph API.
    Returns True if the patch succeeded, False if the event no longer exists or had no source.
    """
    try:
        from .outlook import _headers, GRAPH_BASE
        import psycopg2, psycopg2.extras, os, requests as req
        with psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT csm.source_provider_id, ea.id as account_id
                       FROM personal.calendar_sync_map csm
                       JOIN personal.email_account ea ON ea.id = csm.source_account_id
                       WHERE csm.event_id = %s AND ea.provider = 'outlook'
                       LIMIT 1""",
                    (ev_id,),
                )
                row = c.cursor().fetchone() if False else cur.fetchone()
        if not row:
            return False
        acct = next((a for a in outlook_accounts if a["id"] == row["account_id"]), None)
        if not acct:
            return False
        provider_id = row["source_provider_id"]
        body = {
            "subject": title,
            "body": {"contentType": "text", "content": notes or ""},
        }
        resp = req.patch(
            f"{GRAPH_BASE}/me/events/{provider_id}",
            headers={**_headers(acct), "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        if resp.status_code == 404:
            return False  # original event deleted — caller should create a mirror instead
        if not resp.ok:
            print(f"[appt] outlook patch failed for event {ev_id}: {resp.status_code}")
        return resp.ok
    except Exception as e:
        print(f"[appt] outlook source patch error for event {ev_id}: {e}")
        return False


def _write_outlook(outlook_acct: dict, ev: dict, outlook_ac=None, route: str = "default") -> str | None:
    """
    Create an event in the Outlook calendar via Microsoft Graph. Returns the Outlook event id.
    Routes to the appropriate Outlook calendar (Family, Bills, or default) using the same
    routing logic as GCal — both accounts are peers driven by the same channel rules.
    """
    from datetime import date as date_type
    try:
        from .outlook import _headers, GRAPH_BASE
        from .calendar_router import target_calendar_id
        import requests as req

        starts = ev["starts_at"]
        ends   = _effective_end(starts, ev.get("ends_at"))

        def _fmt(dt):
            if isinstance(dt, date_type) and not isinstance(dt, datetime):
                return {"dateTime": f"{dt}T00:00:00", "timeZone": "Australia/Brisbane"}
            if hasattr(dt, "isoformat"):
                return {"dateTime": dt.isoformat(), "timeZone": "Australia/Brisbane"}
            return {"dateTime": str(dt), "timeZone": "Australia/Brisbane"}

        fb_id        = _tracking_id(ev)
        outlook_body = _outlook_body_with_tag(ev.get("notes") or "", fb_id) if fb_id else (ev.get("notes") or "")

        body = {
            "subject": ev["title"],
            "body": {"contentType": "html", "content": outlook_body},
            "start": _fmt(starts),
            "end":   _fmt(ends),
        }

        cal_id = target_calendar_id(outlook_ac, route) if outlook_ac else None
        url = (f"{GRAPH_BASE}/me/calendars/{cal_id}/events" if cal_id and cal_id != "primary"
               else f"{GRAPH_BASE}/me/events")

        resp = req.post(
            url,
            headers={**_headers(outlook_acct), "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if resp.ok:
            return resp.json().get("id")
        print(f"[appt] outlook write failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"[appt] outlook write error: {e}")
    return None


def _patch_outlook_mirror(outlook_acct: dict, outlook_event_id: str, ev: dict) -> None:
    """Patch an existing Outlook mirror event with updated title/notes/times."""
    from datetime import date as date_type
    try:
        from .outlook import _headers, GRAPH_BASE
        import requests as req

        starts = ev["starts_at"]
        ends   = _effective_end(starts, ev.get("ends_at"))

        def _fmt(dt):
            if isinstance(dt, date_type) and not isinstance(dt, datetime):
                return {"dateTime": f"{dt}T00:00:00", "timeZone": "Australia/Brisbane"}
            if hasattr(dt, "isoformat"):
                return {"dateTime": dt.isoformat(), "timeZone": "Australia/Brisbane"}
            return {"dateTime": str(dt), "timeZone": "Australia/Brisbane"}

        fb_id        = _tracking_id(ev)
        outlook_body = _outlook_body_with_tag(ev.get("notes") or "", fb_id) if fb_id else (ev.get("notes") or "")

        body = {
            "subject": ev["title"],
            "body": {"contentType": "html", "content": outlook_body},
            "start": _fmt(starts),
            "end":   _fmt(ends),
        }
        resp = req.patch(
            f"{GRAPH_BASE}/me/events/{outlook_event_id}",
            headers={**_headers(outlook_acct), "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if resp.status_code == 404:
            return "NOT_FOUND"
        if not resp.ok:
            print(f"[appt] outlook mirror patch failed: {resp.status_code}")
    except Exception as e:
        print(f"[appt] outlook mirror patch error: {e}")
    return None


def _effective_end(starts, ends) -> datetime:
    """Return ends, defaulting to starts + 1 hour when ends is missing or zero-duration."""
    effective = ends or starts
    if effective == starts:
        if isinstance(starts, datetime):
            return starts + timedelta(hours=1)
        # date-only: treat as end-of-day (next day for all-day events is handled by _fmt_cal_dt)
        return starts
    return effective


def _stable_gcal_id(event_id: int) -> str:
    """Deterministic GCal event ID from DB row ID.
    GCal requires base32hex chars [a-v0-9], 5–1024 chars long.
    Hex is a valid subset of base32hex."""
    return f"fb{event_id:012x}"


# ── FamilyBrain tracking ID ────────────────────────────────────────────────────
# Format:
#   Routine occurrence : r{gen_asset_id}/{YYYYMMDD}        e.g. r65/20260716
#   Routine sub-event  : r{gen_asset_id}/{YYYYMMDD}-{suf}  e.g. r65/20260716-a
#   Standalone event   : e{event_id}                       e.g. e550
#   Linked sub-event   : e{parent_id}-{suf}                e.g. e550-a
#
# GCal  → stored in extendedProperties.private.fb_id (invisible to users)
# Outlook → stored as an HTML comment in the body (invisible in rendered view)
# Debug mode (FB_TAG_DEBUG=true) → also appended visibly to description on both

_FB_TAG_DEBUG = os.environ.get("FB_TAG_DEBUG", "").lower() in ("1", "true", "yes")


def _tracking_id(ev: dict, suffix: str = "") -> str:
    """Build the structured tracking ID for this event."""
    from datetime import date as date_type
    suf = f"-{suffix}" if suffix else ""
    gen_asset = ev.get("gen_asset_id")
    starts    = ev.get("starts_at") or ev.get("effective_date")
    if gen_asset and starts:
        d = starts.date() if isinstance(starts, datetime) else starts
        if isinstance(d, date_type):
            return f"r{gen_asset}/{d.strftime('%Y%m%d')}{suf}"
    ev_id = ev.get("id")
    if ev_id:
        return f"e{ev_id}{suf}"
    return ""


def _tracking_id_from_parent(parent_event_id: int, suffix: str = "a") -> str:
    return f"e{parent_event_id}-{suffix}"


_FB_ID_COMMENT_RE = re.compile(r"<!--fb:([^->\s]+)-->")


def _outlook_body_with_tag(notes: str, fb_id: str) -> str:
    """Embed tracking ID as an invisible HTML comment in Outlook body.
    If debug mode is on, also append a visible line."""
    comment = f"<!--fb:{fb_id}-->"
    base    = notes or ""
    # Replace existing tag if present, otherwise append
    if _FB_ID_COMMENT_RE.search(base):
        base = _FB_ID_COMMENT_RE.sub(comment, base)
    else:
        base = f"{base}\n{comment}".lstrip("\n")
    if _FB_TAG_DEBUG:
        # Remove any previous debug line then re-append
        base = re.sub(r"\n?\[fb:[^\]]+\]\s*$", "", base)
        base = f"{base}\n[fb:{fb_id}]"
    return base


def _gcal_extended_props(fb_id: str) -> dict:
    """Return extendedProperties block for a GCal event body."""
    return {"extendedProperties": {"private": {"fb_id": fb_id}}}


def _gcal_description_with_debug(description: str, fb_id: str) -> str:
    """In debug mode, append the visible [fb:...] tag to a GCal description."""
    if not _FB_TAG_DEBUG:
        return description
    base = re.sub(r"\n?\[fb:[^\]]+\]\s*$", "", description or "")
    return f"{base}\n[fb:{fb_id}]"


_EVENT_TYPE_CATEGORY = {
    "SCHOOL_DAY":           "SchoolRoutine",
    "school":               "SchoolRoutine",
    "school_activity":      "SchoolActivity",
    "THERAPY_SESSION":      "Therapy",
    "medical":              "MedicalAppointment",
    "MEDICATION_REFILL":    "Medication",
    "MEDICATION_SCRIPT":    "Medication",
    "PICKUP":               "Routine",
    "AFTERCARE":            "Routine",
    "RENT_PAYMENT":         "Property",
    "RENT_REVIEW":          "Property",
    "RENEWAL":              "Renewal",
    "INSURANCE_RENEWAL":    "Renewal",
    "SUBSCRIPTION_RENEWAL": "Renewal",
    "bill":                 "Bill",
    "family":               "Family",
    "manual":               "Manual",
}

_SOURCE_CATEGORY = {
    "asset_rules":      "AssetRule",
    "manual":           "Manual",
    "email_decomposer": "ExtractedFromEmail",
}

_KNOWN_PEOPLE = ["Olivia", "Elliana", "Shannon", "Glenn"]


def _gcal_tag(ev: dict, title: str) -> str:
    """Build a #familybrain/{person}/{category} tag for GCal descriptions."""
    # Person — from title keyword match
    person = "Family"
    title_lower = title.lower()
    for name in _KNOWN_PEOPLE:
        if name.lower() in title_lower:
            person = name
            break

    # Category — event_type takes priority, then calendar_source
    event_type = (ev.get("event_type") or "").strip()
    source = (ev.get("calendar_source") or "").split(":")[0]
    category = (
        _EVENT_TYPE_CATEGORY.get(event_type)
        or _SOURCE_CATEGORY.get(source)
        or "Appointment"
    )

    return f"#familybrain/{person}/{category}"


def _build_gcal_description(ev: dict, title: str, notes: str, writer: str = "appointment_updater") -> str:
    """Combine notes with FamilyBrain management tags."""
    from datetime import timezone
    category_tag = _gcal_tag(ev, title)
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated_tag = f"#familybrain/updated/{updated_at}/{writer}"
    tags = f"{category_tag}\n{updated_tag}"
    if notes:
        return f"{notes}\n\n{tags}"
    return tags


_FB_TAG_RE    = re.compile(r"#familybrain/([^/\s]+)/([^/\s]+)")
_FB_UPDATE_RE = re.compile(r"#familybrain/updated/([^/\s]+)/([^/\s]+)")
_STALE_AFTER  = timedelta(hours=1)   # replace orphan FB events older than this


def _parse_fb_tags(description: str) -> tuple[str | None, str | None, datetime | None, str | None]:
    """Return (person, category, updated_at, writer) from a GCal description, or Nones."""
    person = category = updated_at = writer = None
    m = _FB_TAG_RE.search(description or "")
    if m:
        person, category = m.group(1), m.group(2)
    m2 = _FB_UPDATE_RE.search(description or "")
    if m2:
        try:
            from datetime import timezone
            updated_at = datetime.fromisoformat(m2.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass
        writer = m2.group(2)
    return person, category, updated_at, writer


_FB_ID_TAG_RE = re.compile(r"\[fb:(e\d+)\]")


def _find_gcal_event_by_fb_id(cal_svc, cal_id: str, fb_id: str, starts) -> str | None:
    """Search GCal for ANY event tagged [fb:{fb_id}] near the event start time.

    Unlike _find_orphan_fb_event this has no staleness check — it is used in
    fallback paths where we know an event with this exact tag already exists
    (e.g. a prior fresh-insert in the same cycle) and must be patched rather
    than duplicated.
    """
    try:
        from datetime import timezone, timedelta as _td
        if hasattr(starts, "tzinfo") and starts.tzinfo:
            t_min = (starts - _td(hours=1)).isoformat()
            t_max = (starts + _td(hours=1)).isoformat()
        else:
            t_min = (starts - _td(hours=1)).isoformat() + "Z"
            t_max = (starts + _td(hours=1)).isoformat() + "Z"
        resp = cal_svc.events().list(
            calendarId=cal_id,
            timeMin=t_min,
            timeMax=t_max,
            singleEvents=True,
            maxResults=50,
        ).execute()
    except Exception:
        return None

    tag = f"[fb:{fb_id}]"
    for item in resp.get("items", []):
        desc = (item.get("description") or "") + str(
            item.get("extendedProperties", {}).get("private", {}).get("fb_id", "")
        )
        if tag in desc or fb_id in desc:
            return item["id"]
    return None


def _find_orphan_fb_event(cal_svc, cal_id: str, ev: dict, title: str,
                          starts: datetime, ends: datetime) -> str | None:
    """Search GCal for an existing FamilyBrain event for the same person+category+slot.

    Returns the GCal event ID if a stale orphan is found (no stable DB-backed ID
    already assigned), so the caller can patch it instead of inserting a duplicate.
    Returns None if no orphan found or the existing event is recent enough to skip.
    """
    from datetime import timezone
    our_person   = _gcal_tag(ev, title).split("/")[1]
    our_category = _gcal_tag(ev, title).split("/")[2]
    stable_id    = _stable_gcal_id(ev["id"]) if ev.get("id") else None

    try:
        results = cal_svc.events().list(
            calendarId=cal_id,
            timeMin=starts.isoformat() if starts.tzinfo else starts.isoformat() + "Z",
            timeMax=ends.isoformat()   if ends.tzinfo   else ends.isoformat()   + "Z",
            singleEvents=True,
        ).execute()
    except Exception:
        return None

    now = datetime.now(timezone.utc)
    for item in results.get("items", []):
        gid = item.get("id", "")
        # Skip our own stable ID — that's handled by the 409 path
        if gid == stable_id:
            continue
        person, category, updated_at, _ = _parse_fb_tags(item.get("description") or "")
        if person != our_person or category != our_category:
            continue
        # Same routine slot — is it stale enough to replace?
        if updated_at is None or (now - updated_at) > _STALE_AFTER:
            return gid

    return None


def _write_gcal(cal_svc, cal_id: str, ev: dict, color_id: str | None = None) -> str:
    """Upsert event into GCal, return gcal event id.

    Uses a stable ID derived from the DB event ID so repeated calls are
    idempotent — no duplicate events even if the loop fires multiple times.
    Before inserting, checks for an orphan FamilyBrain event for the same
    person+category+slot and patches it instead of creating a duplicate.
    Events without a DB ID (e.g. holiday day expansions) fall back to insert.
    """
    starts = ev["starts_at"]
    ends   = _effective_end(starts, ev.get("ends_at"))
    ev_id  = ev.get("id")
    title  = ev["title"]
    fb_id  = _tracking_id(ev)

    description = _build_gcal_description(ev, title, ev.get("notes") or "")
    if fb_id:
        description = _gcal_description_with_debug(description, fb_id)

    body = {
        "summary":     title,
        "description": description,
        "start":       _fmt_cal_dt(starts),
        "end":         _fmt_cal_dt(ends),
    }
    if fb_id:
        body.update(_gcal_extended_props(fb_id))
    if color_id:
        body["colorId"] = color_id

    if ev_id:
        stable_id = _stable_gcal_id(ev_id)
        body["id"] = stable_id
        try:
            result = cal_svc.events().insert(calendarId=cal_id, body=body).execute()
            return result["id"]
        except Exception as e:
            if "409" in str(e) or "conflict" in str(e).lower():
                try:
                    cal_svc.events().patch(calendarId=cal_id, eventId=stable_id, body=body).execute()
                    return stable_id
                except Exception:
                    pass  # fall through to fresh insert below
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in (403, 404, 409):
                raise
            # ID reserved in trash — check for an existing event with same fb-tag before inserting fresh
            body.pop("id", None)
            if fb_id:
                existing_id = _find_gcal_event_by_fb_id(cal_svc, cal_id, fb_id, starts)
                if existing_id:
                    cal_svc.events().patch(calendarId=cal_id, eventId=existing_id, body=body).execute()
                    return existing_id
            result = cal_svc.events().insert(calendarId=cal_id, body=body).execute()
            return result["id"]
    else:
        # No DB backing — check for orphan before inserting
        orphan_id = _find_orphan_fb_event(cal_svc, cal_id, ev, title, starts, ends)
        if orphan_id:
            cal_svc.events().patch(calendarId=cal_id, eventId=orphan_id, body=body).execute()
            return orphan_id
        result = cal_svc.events().insert(calendarId=cal_id, body=body).execute()
        return result["id"]


def _patch_gcal(cal_svc, cal_id: str, gcal_id: str, ev: dict,
                 color_id: str | None = None) -> None:
    """Patch an existing GCal event."""
    starts = ev["starts_at"]
    ends   = _effective_end(starts, ev.get("ends_at"))
    title  = ev["title"]
    fb_id  = _tracking_id(ev)

    description = _build_gcal_description(ev, title, ev.get("notes") or "")
    if fb_id:
        description = _gcal_description_with_debug(description, fb_id)

    body = {
        "summary":     title,
        "description": description,
        "start":       _fmt_cal_dt(starts),
        "end":         _fmt_cal_dt(ends),
    }
    if fb_id:
        body.update(_gcal_extended_props(fb_id))
    if color_id:
        body["colorId"] = color_id

    try:
        cal_svc.events().patch(calendarId=cal_id, eventId=gcal_id, body=body).execute()
        return gcal_id
    except Exception as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status in (403, 404, 410):
            # Event was deleted from GCal — check for existing fb-tagged event before inserting fresh
            body.pop("id", None)
            if fb_id:
                existing_id = _find_gcal_event_by_fb_id(cal_svc, cal_id, fb_id, starts)
                if existing_id:
                    cal_svc.events().patch(calendarId=cal_id, eventId=existing_id, body=body).execute()
                    return existing_id
            result = cal_svc.events().insert(calendarId=cal_id, body=body).execute()
            return result["id"]
        else:
            raise


# ── next_update_at rules ──────────────────────────────────────────────────────

def _next_update(ev: dict) -> datetime | None:
    """
    Return when the appointment updater should next revisit this event.
    None = no scheduled re-check (only process if event changes).
    """
    starts = ev.get("starts_at") or ev.get("effective_date")
    if not starts:
        return None

    # Normalise to date
    if isinstance(starts, datetime):
        event_date = starts.date()
    elif isinstance(starts, date):
        event_date = starts
    else:
        return None

    today = datetime.now(timezone.utc).date()
    days_away = (event_date - today).days

    if days_away < 0:
        return None                                         # past — done
    if days_away == 0 or days_away == 1:
        return None                                         # today/tomorrow — final state
    if days_away <= 7:
        # Re-check the day before
        return datetime(event_date.year, event_date.month, event_date.day,
                        6, 0, tzinfo=timezone.utc) - timedelta(days=1)
    # More than a week out — re-check 3 days before
    check = event_date - timedelta(days=3)
    return datetime(check.year, check.month, check.day, 6, 0, tzinfo=timezone.utc)


def _next_update_bill(ev: dict) -> datetime | None:
    """Bills with missing amount get retried tomorrow; otherwise standard schedule."""
    notes = (ev.get("notes") or "").lower()
    title = (ev.get("title") or "").lower()
    # If amount is still unknown, retry tomorrow
    if "amount: " not in notes and "—" not in title:
        return datetime.now(timezone.utc) + timedelta(days=1)
    return _next_update(ev)


# ── Main updater ──────────────────────────────────────────────────────────────

def run_appointment_updater(accounts: list[dict]) -> int:
    """
    Push pending/changed/scheduled events to Google Calendar.
    Returns number of events processed.
    """
    gmail_acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        next((a for a in accounts if a["provider"] == "gmail"), None),  # fallback if flag not set
    )
    if not gmail_acct:
        print("[appt] no Gmail account — skipping")
        return 0

    # Outlook mirror — write new/changed events to Outlook calendar as well
    outlook_acct = next((a for a in accounts if a["provider"] == "outlook"), None)

    # Partner calendar — when connected, Shannon-tagged events mirror here too
    partner_acct = next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_partner_calendar")),
        None,
    )
    partner_cal_svc = None
    partner_routing = None
    if partner_acct:
        try:
            partner_cal_svc = _cal_service(partner_acct)
            partner_routing = load_routing(accounts).get(partner_acct["id"])
        except Exception as e:
            print(f"[appt] partner calendar service init failed: {e}")

    try:
        cal_svc = _cal_service(gmail_acct)
    except Exception as e:
        print(f"[appt] calendar service init failed: {e}")
        return 0

    routing = load_routing(accounts)
    ac      = routing.get(gmail_acct["id"])
    if not ac:
        return 0

    now            = datetime.now(timezone.utc)
    people         = _load_people()
    school_yr_map  = _load_school_year_map()

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as rconn:
        with rconn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, event_type, starts_at, ends_at, effective_date,
                       calendar_source, notes, person_id,
                       gcal_event_id, gcal_calendar_id, calendar_written_at, next_update_at,
                       updated_at
                FROM personal.event
                WHERE (
                    gcal_event_id IS NULL
                    OR updated_at > calendar_written_at
                    OR (next_update_at IS NOT NULL AND next_update_at <= %s)
                )
                AND status NOT IN ('cancelled', 'superseded', 'ingested')
                AND calendar_source NOT LIKE 'gmail:%%'    -- skip events already in GCal source
                AND starts_at >= now() - INTERVAL '1 hour'  -- skip past events
                ORDER BY effective_date ASC NULLS LAST
                LIMIT %s
                """,
                (now, _BATCH),
            )
            events = list(cur.fetchall())

    # --- Cleanup sweep: delete from GCal any events that became superseded/generated/cancelled,
    #     AND any calendar-synced events (outlook/gmail source) that were wrongly pushed back ---
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as rconn:
        with rconn.cursor() as cur:
            cur.execute("""
                SELECT id, gcal_event_id, gcal_calendar_id
                FROM personal.event
                WHERE gcal_event_id IS NOT NULL
                  AND (
                    status IN ('cancelled', 'superseded')
                    OR calendar_source LIKE 'gmail:%'
                  )
            """)
            to_delete = list(cur.fetchall())

    deleted = 0
    for ev in to_delete:
        ok = False
        try:
            cal_svc.events().delete(
                calendarId=ev["gcal_calendar_id"] or "primary",
                eventId=ev["gcal_event_id"],
            ).execute()
            ok = True
            deleted += 1
        except Exception as _de:
            # 404/410 = already gone from GCal, safe to clear DB reference
            if hasattr(_de, "resp") and getattr(_de.resp, "status", None) in ("404", "410", 404, 410):
                ok = True
        if ok:
            with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as rconn:
                with rconn.cursor() as cur:
                    cur.execute(
                        "UPDATE personal.event SET gcal_event_id = NULL, gcal_calendar_id = NULL WHERE id = %s",
                        (ev["id"],),
                    )
                rconn.commit()
    if deleted:
        print(f"[appt] deleted {deleted} stale GCal event(s)")

    if not events:
        return 0

    print(f"[appt] updating {len(events)} event(s)")
    processed = 0

    for ev in events:
        ev_id       = ev["id"]
        notes       = ev["notes"] or ""
        person_name = people.get(ev.get("person_id"))
        title       = _enrich_title(ev["title"] or "", person_name, notes, school_yr_map)

        # Graph-backed enrichment for thin/vague titles
        title, notes = _try_enrich(title, notes, ev["starts_at"])

        try:
            # Detect partner-sourced events — route to Family cal by default
            ev_source      = ev.get("calendar_source") or ""
            source_is_partner = (
                partner_acct is not None
                and ev_source.startswith(f"gmail:{partner_acct['email_address']}")
            )
            route    = classify_event(title, notes, source_is_partner=source_is_partner)
            cal_id   = target_calendar_id(ac, route)
            tag, color_id = tag_family_event(title, notes) if route == "family" else (None, None)

            gcal_id   = ev.get("gcal_event_id")
            stored_cal = ev.get("gcal_calendar_id")

            if gcal_id and stored_cal == cal_id:
                # Already in the right calendar — patch (returns new id if re-created)
                gcal_id = _patch_gcal(cal_svc, cal_id, gcal_id, ev, color_id=color_id)
            elif gcal_id and stored_cal and stored_cal != cal_id:
                # Rerouted to a different calendar — delete old, insert new
                try:
                    cal_svc.events().delete(calendarId=stored_cal, eventId=gcal_id).execute()
                except Exception:
                    pass
                gcal_id = _write_gcal(cal_svc, cal_id, ev, color_id=color_id)
            else:
                # New event
                gcal_id = _write_gcal(cal_svc, cal_id, ev, color_id=color_id)

                # Holiday: also expand individual day events into Family calendar
                if route == "holiday" and ac.family_cal_id:
                    from .calendar_router import expand_holiday_days
                    for day in expand_holiday_days(title, ev["starts_at"], ev["ends_at"]):
                        try:
                            _write_gcal(cal_svc, ac.family_cal_id, {
                                "title":    day["summary"],
                                "starts_at": day["starts_at"],
                                "ends_at":   day["ends_at"],
                                "notes":     notes,
                            }, color_id=_TAG_COLORS["Holiday"])
                        except Exception as de:
                            print(f"[appt] holiday day event failed: {de}")

            # Determine next scheduled re-check
            if ev.get("event_type") == "bill":
                nxt = _next_update_bill(ev)
            else:
                nxt = _next_update(ev)

            with conn() as wconn:
                with wconn.cursor() as wcur:
                    wcur.execute(
                        """
                        UPDATE personal.event
                        SET gcal_event_id       = %s,
                            gcal_calendar_id    = %s,
                            calendar_written_at = now(),
                            next_update_at      = %s,
                            title               = %s,
                            notes               = %s
                        WHERE id = %s
                        """,
                        (gcal_id, cal_id, nxt, title, notes or ev["notes"], ev_id),
                    )
                wconn.commit()

            # Write enriched title+notes back to the original Outlook event (if sourced from Outlook).
            # If the original is gone (404), fall through to create a mirror like any other event.
            source_patched = False
            if ev.get("calendar_source", "").startswith("outlook:"):
                outlook_accounts = [a for a in accounts if a["provider"] == "outlook"]
                source_patched = _patch_outlook_source(ev_id, title, notes, outlook_accounts)

            # Mirror to Outlook: non-Outlook-sourced events, or Outlook-sourced whose original is gone
            if outlook_acct and not source_patched:
                try:
                    with psycopg2.connect(DB_URL,
                                          cursor_factory=psycopg2.extras.RealDictCursor) as _c:
                        with _c.cursor() as _cur:
                            _cur.execute(
                                """SELECT mirror_provider_id FROM personal.calendar_sync_map
                                   WHERE event_id = %s AND mirror_account_id = %s LIMIT 1""",
                                (ev_id, outlook_acct["id"]),
                            )
                            _row = _cur.fetchone()
                    existing_ol_id = _row["mirror_provider_id"] if _row else None
                    outlook_ac = routing.get(outlook_acct["id"])
                    if existing_ol_id:
                        result = _patch_outlook_mirror(outlook_acct, existing_ol_id,
                                                       {**ev, "title": title, "notes": notes})
                        if result == "NOT_FOUND":
                            existing_ol_id = None  # fall through to create below
                    if not existing_ol_id:
                        ol_id = _write_outlook(outlook_acct, {**ev, "title": title, "notes": notes},
                                               outlook_ac=outlook_ac, route=route)
                        if ol_id:
                            from .db import upsert_sync_map
                            # Key must match what outlook.py looks up: "outlook:{email}:{ol_id}"
                            # so the Outlook sync recognises the echo and skips re-importing it
                            upsert_sync_map(ev_id,
                                source_account_id=outlook_acct["id"],
                                source_provider_id=f"outlook:{outlook_acct['email_address']}:{ol_id}",
                                mirror_account_id=outlook_acct["id"],
                                mirror_provider_id=ol_id)
                            print(f"[appt] mirrored '{title[:40]}' → Outlook")
                except Exception as oe:
                    print(f"[appt] outlook mirror error for '{title[:40]}': {oe}")

            # Partner-sourced event that also involves Glenn → also write to his default cal
            if source_is_partner and route == "family":
                attendees = []  # attendee list not stored in personal.event currently
                if partner_event_involves_owner(title, notes, attendees):
                    owner_cal_id = ac.default_cal_id
                    try:
                        _write_gcal(cal_svc, owner_cal_id, {**ev, "title": title, "notes": notes},
                                    color_id=color_id)
                        print(f"[appt] partner event involves owner — also → default cal: '{title[:40]}'")
                    except Exception as oe:
                        print(f"[appt] owner-cal mirror failed for '{title[:40]}': {oe}")

            # Mirror Glenn's events that mention Shannon → her calendar
            # (only for Glenn-sourced events, not Shannon's own events going back to her)
            if partner_cal_svc and partner_routing and _PARTNER_NAMES and not source_is_partner:
                title_lower = title.lower()
                if any(n in title_lower for n in _PARTNER_NAMES):
                    partner_cal_id = target_calendar_id(partner_routing, route)
                    try:
                        _write_gcal(partner_cal_svc, partner_cal_id, ev, color_id=color_id)
                        print(f"[appt] mirrored '{title[:50]}' → partner cal")
                    except Exception as pe:
                        print(f"[appt] partner mirror failed for '{title[:40]}': {pe}")

            print(f"[appt] {'patch' if ev.get('gcal_event_id') else 'write'} "
                  f"'{title[:50]}' → {route} cal"
                  + (f" | next check {nxt.date()}" if nxt else ""))
            processed += 1

        except Exception as e:
            print(f"[appt] failed for event {ev_id} '{title[:40]}': {e}")

    return processed
