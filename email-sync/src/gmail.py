"""
Gmail email + calendar sync via Google APIs.

Uses per-account OAuth2 refresh tokens stored in personal.email_account.
Supports multiple Gmail accounts (one row per account).

Email sync:  Gmail API (messages.list + messages.get with history-based incremental sync)
Calendar:    Google Calendar API (events.list with syncToken incremental sync)
"""
import base64
import json
import re
from datetime import datetime, timezone
from typing import Optional

import html2text
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import db
from .filters import should_ingest, reset_cache as reset_filter_cache

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # read + label + send (excludes permanent delete)
    "https://www.googleapis.com/auth/calendar",       # full calendar read/write (create, update, delete events)
]

# Label cache: account_id → {category_name: labelId}
_label_cache: dict[int, dict[str, str]] = {}
LABEL_PARENT = "FamilyBrain"

# Gmail client ID / secret come from env (shared across all personal Gmail accounts)
import os
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_TOKEN_URI     = "https://oauth2.googleapis.com/token"

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False   # preserve hrefs so financial_processor can harvest PDF links
_h2t.ignore_images = True
_h2t.protect_links = True   # keep URLs inline in markdown format
_h2t.body_width = 0


def _creds(account: dict) -> Credentials:
    """Build/refresh OAuth2 credentials for a Gmail account."""
    creds = Credentials(
        token=account["access_token"],
        refresh_token=account["refresh_token"],
        token_uri=GOOGLE_TOKEN_URI,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GMAIL_SCOPES,
    )
    if not creds.valid or creds.expired:
        creds.refresh(Request())
        db.update_token(account["id"], creds.token, creds.expiry)
    return creds


def _gmail_service(account: dict):
    return build("gmail", "v1", credentials=_creds(account), cache_discovery=False)


def _calendar_service(account: dict):
    return build("calendar", "v3", credentials=_creds(account), cache_discovery=False)


# ── Email ──────────────────────────────────────────────────────────────────────

def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
        return _h2t.handle(html)
    # multipart — recurse
    text_parts = []
    for part in payload.get("parts", []):
        t = _extract_body(part)
        if t:
            text_parts.append(t)
    return "\n".join(text_parts)


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _parse_message(msg: dict) -> dict:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject      = _header(headers, "Subject")
    from_raw     = _header(headers, "From")
    to_raw       = _header(headers, "To")
    date_raw     = _header(headers, "Date")
    from_name, from_addr = _parse_address(from_raw)
    to_addrs     = [a.strip() for a in to_raw.split(",") if a.strip()]
    body_text    = _extract_body(payload)
    internal_ms  = int(msg.get("internalDate", 0))
    received_at  = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc).isoformat()
    is_sent      = "SENT" in msg.get("labelIds", [])
    return {
        "provider_msg_id": msg["id"],
        "thread_id":       msg.get("threadId"),
        "from_address":    from_addr,
        "from_name":       from_name,
        "to_addresses":    to_addrs,
        "subject":         subject,
        "received_at":     received_at,
        "body_text":       body_text,
        "attachments":     [],
        "is_sent":         is_sent,
    }


def _parse_address(raw: str) -> tuple[str, str]:
    """Parse 'Name <email>' → (name, email)."""
    m = re.match(r'^(.*?)\s*<([^>]+)>', raw)
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    raw = raw.strip()
    return "", raw


def _get_or_create_label(svc, account_id: int, category: str) -> str:
    """
    Return the Gmail labelId for "FamilyBrain/<category>", creating it if needed.
    Results are cached per account for the lifetime of the sync run.
    """
    cache = _label_cache.setdefault(account_id, {})
    if category in cache:
        return cache[category]

    label_name = f"{LABEL_PARENT}/{category}"

    # List existing labels
    resp = svc.users().labels().list(userId="me").execute()
    for lbl in resp.get("labels", []):
        if lbl["name"].lower() == label_name.lower():
            cache[category] = lbl["id"]
            return lbl["id"]

    # Create missing label (white text on teal background — distinct from Gmail defaults)
    new_label = svc.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
            "color": {"backgroundColor": "#16a765", "textColor": "#ffffff"},
        },
    ).execute()
    cache[category] = new_label["id"]
    print(f"[gmail] Created label '{label_name}' ({new_label['id']})")
    return new_label["id"]


def apply_ingested_label(account: dict, svc, msg_id: str, category: str) -> None:
    """Apply FamilyBrain/<category> label to a Gmail message."""
    try:
        label_id = _get_or_create_label(svc, account["id"], category)
        svc.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except Exception as e:
        print(f"[gmail] Failed to apply label for {msg_id}: {e}")


def sync_email(account: dict, ingestor_url: str) -> int:
    """
    Sync new emails for a Gmail account.
    Uses history-based incremental sync (historyId cursor in sync_cursor field).
    Falls back to listing recent messages if no cursor exists.
    Returns number of messages submitted to ingestor.
    """
    import requests as req

    svc        = _gmail_service(account)
    account_id = account["id"]
    cursor     = account.get("sync_cursor")  # historyId
    ingested   = 0
    skipped    = 0

    reset_filter_cache()

    try:
        if cursor:
            # Incremental: fetch history since last historyId
            try:
                history_resp = svc.users().history().list(
                    userId="me",
                    startHistoryId=cursor,
                    historyTypes=["messageAdded"],
                    maxResults=500,
                ).execute()
            except HttpError as e:
                if e.resp.status == 404:
                    # historyId expired — fall back to full sync
                    print(f"[gmail] historyId expired for {account['email_address']} — full sync")
                    cursor = None
                else:
                    raise

        if not cursor:
            from datetime import timedelta
            since_days = int(os.environ.get("GMAIL_INITIAL_DAYS", "365"))
            since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y/%m/%d")
            query = f"in:inbox OR in:sent -category:promotions -category:social -category:updates -is:spam -is:trash after:{since_date}"
            # Paginate through all results (Gmail caps each page at 500)
            msg_ids = []
            page_token = None
            while True:
                kwargs = {"userId": "me", "maxResults": 500, "q": query}
                if page_token:
                    kwargs["pageToken"] = page_token
                list_resp = svc.users().messages().list(**kwargs).execute()
                msg_ids.extend(m["id"] for m in list_resp.get("messages", []))
                page_token = list_resp.get("nextPageToken")
                if not page_token:
                    break
            print(f"[gmail] full sync: {len(msg_ids)} messages in range ({since_days}d)")
        else:
            msg_ids = []
            for h in history_resp.get("history", []):
                for added in h.get("messagesAdded", []):
                    msg_ids.append(added["message"]["id"])

        for msg_id in msg_ids:
            if db.is_already_ingested(account_id, msg_id):
                continue
            try:
                msg    = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()

                # Skip messages in Promotions / Spam / Trash labels (incremental path)
                msg_labels = set(msg.get("labelIds", []))
                if msg_labels & {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES"}:
                    skipped += 1
                    continue

                parsed = _parse_message(msg)

                # Extract raw headers for bulk-mail heuristics
                raw_headers = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }

                ok, reason = should_ingest(
                    from_address=parsed["from_address"],
                    subject=parsed["subject"],
                    body_text=parsed["body_text"],
                    headers=raw_headers,
                )
                if not ok:
                    print(f"[gmail] skipped {msg_id} ({parsed['from_address']}): {reason}")
                    skipped += 1
                    db.mark_skipped(account_id, msg_id, parsed["from_address"],
                                    parsed["subject"], parsed.get("received_at"), reason)
                    continue

                parsed["account_id"] = account_id
                resp = req.post(f"{ingestor_url}/ingest/email", json=parsed, timeout=60)
                if resp.ok:
                    ingested += 1
                    # Apply FamilyBrain/<category> label so it's visible in Gmail inbox
                    result = resp.json()
                    category = result.get("category", "personal")
                    apply_ingested_label(account, svc, msg_id, category)
                else:
                    print(f"[gmail] ingestor rejected {msg_id}: {resp.text}")
            except Exception as e:
                print(f"[gmail] error processing {msg_id}: {e}")

        # Retry previously failed messages
        retry_ids = db.get_retryable_messages(account_id)
        if retry_ids:
            print(f"[gmail] retrying {len(retry_ids)} error/pending messages for {account['email_address']}")
        for msg_id in retry_ids:
            try:
                msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
                # Drop promotions/social/updates — same guard as the main loop
                msg_labels = set(msg.get("labelIds", []))
                if msg_labels & {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES"}:
                    skipped += 1
                    db.mark_skipped(account_id, msg_id, "", "", None, "non-primary category")
                    continue
                parsed = _parse_message(msg)
                raw_headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                ok, reason = should_ingest(
                    from_address=parsed["from_address"],
                    subject=parsed["subject"],
                    body_text=parsed["body_text"],
                    headers=raw_headers,
                )
                if not ok:
                    skipped += 1
                    db.mark_skipped(account_id, msg_id, parsed["from_address"],
                                    parsed["subject"], parsed.get("received_at"), reason)
                    continue
                parsed["account_id"] = account_id
                resp = req.post(f"{ingestor_url}/ingest/email", json=parsed, timeout=60)
                if resp.ok:
                    ingested += 1
                    result = resp.json()
                    category = result.get("category", "personal")
                    apply_ingested_label(account, svc, msg_id, category)
                    db.mark_label_applied(account_id, msg_id)
                else:
                    print(f"[gmail] retry ingestor rejected {msg_id}: {resp.text}")
            except Exception as e:
                print(f"[gmail] retry error for {msg_id}: {e}")

        # Backfill labels for messages ingested without labels
        backfill = db.get_ingested_without_label(account_id)
        if backfill:
            print(f"[gmail] backfilling labels for {len(backfill)} messages")
        for msg_id, category in backfill:
            try:
                apply_ingested_label(account, svc, msg_id, category or "personal")
                db.mark_label_applied(account_id, msg_id)
            except Exception as e:
                print(f"[gmail] backfill label failed for {msg_id}: {e}")

        # Update cursor to latest historyId
        profile = svc.users().getProfile(userId="me").execute()
        db.update_sync_cursor(account_id, str(profile.get("historyId", "")))

    except Exception as e:
        print(f"[gmail] sync_email failed for {account['email_address']}: {e}")

    if skipped:
        print(f"[gmail] {skipped} messages skipped (junk/filtered) for {account['email_address']}")
    return ingested


# ── Calendar ───────────────────────────────────────────────────────────────────

def _classify_event(summary: str) -> str:
    """Heuristic event type from title."""
    s = summary.lower()
    if any(w in s for w in ["school", "term", "holiday", "excursion", "pickup"]):
        return "school"
    if any(w in s for w in ["gp", "doctor", "physio", "therapy", "ndis", "appointment", "specialist", "hospital"]):
        return "medical"
    if any(w in s for w in ["ndis", "support worker", "occupational", "speech"]):
        return "ndis"
    return "family"


def _parse_cal_dt(dt_obj: dict):
    """
    Parse a Google Calendar start/end object.
    Returns datetime for timed events, date for all-day events.
    Keeping them distinct so _fmt_cal_dt writes the correct field back.
    """
    if not dt_obj:
        return None
    if "dateTime" in dt_obj:
        from dateutil.parser import parse as dtparse
        return dtparse(dt_obj["dateTime"])
    if "date" in dt_obj:
        from datetime import date as date_type
        return date_type.fromisoformat(dt_obj["date"])
    return None


def sync_calendar(account: dict, mirror_accounts: list[dict], ingestor_url: str = "") -> int:
    """
    Sync Google Calendar events for an account into personal.event.
    Routes events to Bills/Holidays/Family calendars and mirrors per routing rules.
    Returns number of events synced.
    """
    from .outlook import create_outlook_event
    from .calendar_router import classify_event, load_routing, target_calendar_id, holiday_stub_summary, _TAG_COLORS

    all_accounts = [account] + mirror_accounts
    routing      = load_routing(all_accounts)
    ac           = routing[account["id"]]

    svc         = _calendar_service(account)
    account_id  = account["id"]
    cursor      = account.get("calendar_sync_cursor")
    synced      = 0

    # Build mirror account lookup
    mirror_by_id = {a["id"]: a for a in mirror_accounts}

    try:
        kwargs = dict(calendarId=ac.default_cal_id, singleEvents=True, maxResults=500)
        if cursor:
            kwargs["syncToken"] = cursor
        else:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            kwargs["timeMin"] = (now - timedelta(days=90)).isoformat()
            kwargs["timeMax"] = (now + timedelta(days=365)).isoformat()
            kwargs["orderBy"] = "startTime"

        try:
            events_resp = svc.events().list(**kwargs).execute()
        except HttpError as e:
            if e.resp.status == 410:
                account["calendar_sync_cursor"] = None
                return sync_calendar(account, mirror_accounts)
            raise

        for ev in events_resp.get("items", []):
            if ev.get("status") == "cancelled":
                provider_id = ev["id"]
                sync_row = db.get_sync_map(account_id, provider_id)
                if sync_row:
                    # Delete from routed calendar (Bills/Family/Holiday copy)
                    if sync_row.get("target_cal_provider_id"):
                        try:
                            svc.events().delete(
                                calendarId=ac.default_cal_id,
                                eventId=sync_row["target_cal_provider_id"],
                            ).execute()
                        except Exception:
                            pass
                    # Delete from Outlook mirror
                    if sync_row.get("mirror_provider_id") and sync_row.get("mirror_account_id"):
                        mirror_acct = mirror_by_id.get(sync_row["mirror_account_id"])
                        if mirror_acct and mirror_acct["provider"] == "outlook":
                            try:
                                from .outlook import _headers, GRAPH_BASE
                                import requests as _req
                                _req.delete(
                                    f"{GRAPH_BASE}/me/events/{sync_row['mirror_provider_id']}",
                                    headers=_headers(mirror_acct), timeout=15,
                                )
                            except Exception:
                                pass
                    # Mark inactive in DB (preserve history)
                    with db.conn() as c:
                        with c.cursor() as cur:
                            cur.execute(
                                "UPDATE personal.event SET status='cancelled' WHERE calendar_event_id=%s",
                                (f"gmail:{account['email_address']}:{provider_id}",),
                            )
                            cur.execute(
                                "UPDATE personal.calendar_sync_map SET sync_status='cancelled' WHERE source_account_id=%s AND source_provider_id=%s",
                                (account_id, provider_id),
                            )
                        c.commit()
                    print(f"[gmail] marked cancelled: {provider_id}")
                continue
            provider_id = ev["id"]
            summary     = ev.get("summary", "(no title)")
            starts_at   = _parse_cal_dt(ev.get("start"))
            ends_at     = _parse_cal_dt(ev.get("end"))
            description = ev.get("description", "")
            etag        = ev.get("etag")

            if not starts_at:
                continue

            from .calendar_router import tag_family_event
            route      = classify_event(summary, description)
            event_type = _classify_event(summary)  # keep existing DB event_type
            cal_key    = f"gmail:{account['email_address']}:{provider_id}"

            # Determine tag + color for Family events
            tag, color_id = tag_family_event(summary, description) if route == "family" else (None, None)

            existing = db.get_sync_map(account_id, provider_id)
            etag_changed = not existing or existing.get("last_etag") != etag

            # Write/update routed calendar (Bills/Holidays/Family/default)
            target_cal = target_calendar_id(ac, route)
            target_cal_id_stored = None
            if target_cal != ac.default_cal_id:
                existing_target_id = (existing or {}).get("target_cal_provider_id")
                try:
                    if existing_target_id and not etag_changed:
                        target_cal_id_stored = existing_target_id  # no change, keep as-is
                    elif existing_target_id:
                        # event changed — patch existing target copy
                        _patch_event_in_calendar(svc, target_cal, existing_target_id,
                                                 summary, starts_at, ends_at, description,
                                                 color_id=color_id)
                        target_cal_id_stored = existing_target_id
                    elif not existing:
                        # brand-new event — insert into target calendar
                        target_cal_id_stored = _write_event_to_calendar(
                            svc, target_cal, summary, starts_at, ends_at, description,
                            color_id=color_id)
                    # else: sync_map exists but target_cal_provider_id not yet tracked — skip to avoid duplicates
                    # Holiday day expansion is handled by appointment_updater, not here
                except Exception as e:
                    print(f"[gmail] failed to write to {route} calendar for '{summary}': {e}")

            event_id = db.upsert_event(
                title=summary,
                starts_at=starts_at,
                ends_at=ends_at,
                event_type=event_type,
                calendar_source=f"gmail:{account['email_address']}",
                calendar_event_id=cal_key,
                notes=description[:500],
                ingestor_url=ingestor_url,
            )

            # Mirror to other accounts (only if not yet mirrored, or event changed)
            if not (existing and existing.get("mirror_provider_id") and not etag_changed):
                for (mirror_acct_id, mirror_slot) in ac.mirror_to:
                    mirror_acct = mirror_by_id.get(mirror_acct_id)
                    if not mirror_acct or not mirror_acct.get("sync_calendar"):
                        continue
                    mirror_ac = routing.get(mirror_acct_id)
                    effective_slot = route if mirror_slot == "route" else mirror_slot
                    mirror_cal = target_calendar_id(mirror_ac, effective_slot) if mirror_ac else None
                    existing_mirror_id = (existing or {}).get("mirror_provider_id")
                    try:
                        if mirror_acct["provider"] == "outlook":
                            mirror_id = create_outlook_event(mirror_acct, summary, starts_at, ends_at, description)
                        elif existing_mirror_id and etag_changed:
                            mirror_svc = _calendar_service(mirror_acct)
                            _patch_event_in_calendar(mirror_svc, mirror_cal or "primary",
                                                     existing_mirror_id, summary, starts_at, ends_at, description)
                            mirror_id = existing_mirror_id
                        elif not existing_mirror_id and (not existing or etag_changed):
                            # new event OR event changed but mirror_id not yet tracked — write once
                            mirror_svc = _calendar_service(mirror_acct)
                            mirror_id = _write_event_to_calendar(
                                mirror_svc, mirror_cal or "primary",
                                summary, starts_at, ends_at, description)
                        else:
                            continue  # mirror exists or event unchanged — skip
                        db.upsert_sync_map(
                            event_id, account_id, provider_id,
                            mirror_account_id=mirror_acct_id,
                            mirror_provider_id=mirror_id,
                            target_cal_provider_id=target_cal_id_stored,
                            sync_status="synced", etag=etag,
                        )
                    except Exception as e:
                        print(f"[gmail] mirror to {mirror_acct['email_address']} failed for '{summary}': {e}")
                        db.upsert_sync_map(event_id, account_id, provider_id,
                                           sync_status="error", etag=etag)
            elif target_cal_id_stored:
                # No mirror needed but store target_cal_provider_id if we just created/updated it
                db.upsert_sync_map(
                    event_id, account_id, provider_id,
                    target_cal_provider_id=target_cal_id_stored,
                    sync_status="synced", etag=etag,
                )

            synced += 1

        next_token = events_resp.get("nextSyncToken")
        if next_token:
            db.update_calendar_sync_cursor(account_id, next_token)

    except Exception as e:
        print(f"[gmail] sync_calendar failed for {account['email_address']}: {e}")

    return synced


def _fmt_cal_dt(dt) -> dict:
    if dt is None:
        return {}
    if hasattr(dt, "hour"):
        return {"dateTime": dt.isoformat(), "timeZone": "Australia/Brisbane"}
    return {"date": dt.strftime("%Y-%m-%d")}


def _find_event_in_calendar(svc, cal_id: str, summary: str, starts_at) -> str | None:
    """
    Search a calendar for an existing event matching summary + date.
    Returns the event ID if found, else None.
    Prevents duplicate inserts when target_cal_provider_id isn't yet tracked.
    """
    from datetime import date as date_type, datetime as datetime_type, timedelta
    try:
        if isinstance(starts_at, datetime_type):
            time_min = (starts_at - timedelta(hours=1)).isoformat()
            time_max = (starts_at + timedelta(hours=1)).isoformat()
        else:
            time_min = f"{starts_at}T00:00:00Z"
            time_max = f"{starts_at + timedelta(days=1)}T00:00:00Z"

        results = svc.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            q=summary[:50],
            singleEvents=True,
            maxResults=10,
        ).execute()
        for ev in results.get("items", []):
            if ev.get("summary", "").lower() == summary.lower():
                return ev["id"]
    except Exception as e:
        print(f"[gmail] calendar search failed for '{summary}': {e}")
    return None


def _write_event_to_calendar(svc, cal_id: str, summary: str, starts_at, ends_at,
                              description: str, color_id: str | None = None) -> str:
    """
    Insert a new event in a Google Calendar, return provider event ID.
    Searches for an existing matching event first to avoid duplicates when
    target_cal_provider_id isn't yet tracked in the sync map.
    """
    existing_id = _find_event_in_calendar(svc, cal_id, summary, starts_at)
    if existing_id:
        # Already exists — patch it and return its ID
        _patch_event_in_calendar(svc, cal_id, existing_id, summary, starts_at, ends_at,
                                  description, color_id=color_id)
        return existing_id

    body = {
        "summary":     summary,
        "description": description,
        "start":       _fmt_cal_dt(starts_at),
        "end":         _fmt_cal_dt(ends_at or starts_at),
    }
    if color_id:
        body["colorId"] = color_id
    result = svc.events().insert(calendarId=cal_id, body=body).execute()
    return result["id"]


def _patch_event_in_calendar(svc, cal_id: str, event_id: str,
                              summary: str, starts_at, ends_at,
                              description: str, color_id: str | None = None) -> None:
    """Patch an existing Google Calendar event in-place."""
    body = {
        "summary":     summary,
        "description": description,
        "start":       _fmt_cal_dt(starts_at),
        "end":         _fmt_cal_dt(ends_at or starts_at),
    }
    if color_id:
        body["colorId"] = color_id
    svc.events().patch(calendarId=cal_id, eventId=event_id, body=body).execute()


def create_gmail_event(account: dict, summary: str, starts_at: datetime,
                       ends_at: Optional[datetime], description: str = "") -> str:
    """Create a Google Calendar event and return its provider event ID."""
    svc    = _calendar_service(account)
    cal_id = account.get("calendar_id") or "primary"

    def _fmt(dt: datetime) -> dict:
        return {"dateTime": dt.isoformat(), "timeZone": "Australia/Brisbane"}

    body = {
        "summary":     summary,
        "description": description,
        "start":       _fmt(starts_at),
        "end":         _fmt(ends_at or starts_at),
    }
    created = svc.events().insert(calendarId=cal_id, body=body).execute()
    return created["id"]
