"""
Outlook / Hotmail email + calendar sync via Microsoft Graph API.

Uses per-account OAuth2 refresh tokens (MSAL) stored in personal.email_account.
Supports multiple Outlook/Hotmail accounts.

Email sync:   /me/mailFolders/inbox/messages with delta query
Calendar:     /me/events with delta query
"""
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import html2text
import msal
import requests

from . import db
from .filters import should_ingest, reset_cache as reset_filter_cache

TENANT_ID     = os.environ.get("MICROSOFT_TENANT_ID", "consumers")  # 'consumers' for personal MSA
CLIENT_ID     = os.environ["MICROSOFT_CLIENT_ID"]
CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "")  # public client can omit

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Calendars.ReadWrite",
]

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False   # preserve hrefs so financial_processor can harvest PDF links
_h2t.ignore_images = True
_h2t.protect_links = True
_h2t.body_width = 0


def _get_access_token(account: dict) -> str:
    """Refresh the access token via MSAL and persist it."""
    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    )
    result = app.acquire_token_by_refresh_token(
        refresh_token=account["refresh_token"],
        scopes=OUTLOOK_SCOPES,
    )
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token refresh failed: {result.get('error_description')}")

    expiry = datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 3600))
    db.update_token(account["id"], result["access_token"], expiry)
    return result["access_token"]


def _token(account: dict) -> str:
    expiry = account.get("token_expiry")
    if expiry and account.get("access_token"):
        if isinstance(expiry, str):
            from dateutil.parser import parse as dtparse
            expiry = dtparse(expiry)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > datetime.now(timezone.utc) + timedelta(minutes=5):
            return account["access_token"]
    return _get_access_token(account)


def _headers(account: dict) -> dict:
    return {"Authorization": f"Bearer {_token(account)}", "Content-Type": "application/json"}


def _strip_html(html: str) -> str:
    return _h2t.handle(html)


# ── Inbox labelling ────────────────────────────────────────────────────────────

# Graph API masterCategories color must be a preset name, NOT a CSS color name.
# Valid values: none, preset0–preset24
# preset0=red, preset1=orange, preset2=yellow, preset3=green, preset4=teal,
# preset5=olive, preset6=blue, preset7=purple, preset8=cranberry, preset9=steel,
# preset10=darkSteel, preset11=gray, preset12=warmGray, preset13=darkGray,
# preset14=black, preset15=darkRed, preset16=darkOrange, preset17=darkYellow,
# preset18=darkGreen, preset19=darkTeal, preset20=darkOlive, preset21=darkBlue,
# preset22=darkPurple, preset23=darkCranberry, preset24=darkSteel2
_OUTLOOK_COLOURS = {
    "ndis":      "preset7",   # purple
    "health":    "preset0",   # red
    "finance":   "preset3",   # green
    "property":  "preset1",   # orange
    "insurance": "preset6",   # blue
    "travel":    "preset4",   # teal
    "vehicle":   "preset2",   # yellow
    "school":    "preset11",  # gray
    "legal":     "preset15",  # darkRed
    "personal":  "none",
}
_outlook_category_cache: set[str] = set()


def _ensure_outlook_category(token: str, category: str) -> None:
    """Create the FamilyBrain/<category> master category if it doesn't exist."""
    label_name = f"FamilyBrain/{category}"
    if label_name in _outlook_category_cache:
        return
    colour = _OUTLOOK_COLOURS.get(category, "none")
    try:
        resp     = requests.get(
            f"{GRAPH_BASE}/me/outlook/masterCategories",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        existing = {c["displayName"] for c in resp.json().get("value", [])}
        if label_name not in existing:
            cr = requests.post(
                f"{GRAPH_BASE}/me/outlook/masterCategories",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"displayName": label_name, "color": colour},
                timeout=30,
            )
            if cr.ok:
                print(f"[outlook] Created category '{label_name}'")
            else:
                print(f"[outlook] Failed to create category '{label_name}': {cr.status_code} {cr.text[:100]}")
                return
        _outlook_category_cache.add(label_name)
    except Exception as e:
        print(f"[outlook] Failed to ensure category '{label_name}': {e}")


def apply_ingested_category(account: dict, msg_id: str, category: str) -> None:
    """Tag an Outlook message with FamilyBrain/<category> category."""
    try:
        token      = _token(account)
        label_name = f"FamilyBrain/{category}"
        _ensure_outlook_category(token, category)
        resp = requests.patch(
            f"{GRAPH_BASE}/me/messages/{msg_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"categories": [label_name]},
            timeout=15,
        )
        if not resp.ok:
            print(f"[outlook] Category patch failed for {msg_id}: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"[outlook] Failed to apply category for {msg_id}: {e}")


# ── Email ──────────────────────────────────────────────────────────────────────

def _parse_address(addr_obj: dict) -> tuple[str, str]:
    ea = addr_obj.get("emailAddress", {})
    return ea.get("name", ""), ea.get("address", "")


def sync_email(account: dict, ingestor_url: str) -> int:
    """
    Sync new Outlook/Hotmail emails for an account.
    Uses Graph delta query (deltaLink stored in sync_cursor).
    Returns number of messages submitted to ingestor.
    """
    account_id = account["id"]
    cursor     = account.get("sync_cursor")  # deltaLink URL
    ingested   = 0
    skipped    = 0

    reset_filter_cache()

    try:
        if cursor:
            url = cursor  # deltaLink is a complete URL
        else:
            initial_days = int(os.environ.get("OUTLOOK_INITIAL_DAYS", "90"))
            since = (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Use regular messages endpoint for initial fetch — delta ignores $filter
            url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages?$top=50&$orderby=receivedDateTime+desc&$select=id,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId&$filter=receivedDateTime+ge+{since}"

        while url:
            resp = requests.get(url, headers=_headers(account), timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for msg in data.get("value", []):
                msg_id = msg["id"]
                if db.is_already_ingested(account_id, msg_id):
                    continue

                from_name, from_addr = _parse_address(msg.get("from") or {})
                to_addrs = [
                    r["emailAddress"]["address"]
                    for r in msg.get("toRecipients") or []
                    if r.get("emailAddress", {}).get("address")
                ]
                body_obj  = msg.get("body") or {}
                body_text = (
                    _strip_html(body_obj.get("content", ""))
                    if (body_obj.get("contentType") or "").lower() == "html"
                    else body_obj.get("content", "")
                )
                # Fall back to Graph API bodyPreview when HTML stripping yields nothing
                # (happens with image-only or layout-only emails)
                if not body_text.strip():
                    body_text = msg.get("bodyPreview", "")

                subject = msg.get("subject") or "(no subject)"

                # Skip locally if still no body — avoids ingestor round-trip rejection
                if not body_text.strip():
                    skipped += 1
                    db.mark_skipped(account_id, msg_id, from_addr, subject,
                                    msg.get("receivedDateTime"), "outlook:empty_body")
                    continue

                # Self-sent emails (e.g. scanner to self) always pass through
                acct_addr = account.get("email_address", "").lower()
                is_self_sent = from_addr.lower() == acct_addr

                # Only sync Focused inbox — skip Other unless domain is in financial_domain DB table
                if not is_self_sent and msg.get("inferenceClassification") == "other":
                    domain = from_addr.split("@")[-1].lower() if "@" in from_addr else ""
                    try:
                        import psycopg2
                        _db_url = os.environ.get("DATABASE_URL")
                        with psycopg2.connect(_db_url) as _conn:
                            with _conn.cursor() as _cur:
                                _cur.execute(
                                    "SELECT 1 FROM personal.financial_domain WHERE %s ILIKE '%%' || domain || '%%' LIMIT 1",
                                    (domain,)
                                )
                                _in_whitelist = _cur.fetchone() is not None
                    except Exception:
                        _in_whitelist = False
                    if not _in_whitelist:
                        skipped += 1
                        db.mark_skipped(account_id, msg_id, from_addr, subject,
                                        msg.get("receivedDateTime"), "outlook:other")
                        continue

                ok, reason = should_ingest(
                    from_address=from_addr,
                    subject=subject,
                    body_text=body_text,
                )
                if not ok:
                    print(f"[outlook] skipped {msg_id} ({from_addr}): {reason}")
                    skipped += 1
                    db.mark_skipped(account_id, msg_id, from_addr, subject,
                                    msg.get("receivedDateTime"), reason)
                    continue

                payload = {
                    "account_id":      account_id,
                    "provider_msg_id": msg_id,
                    "thread_id":       msg.get("conversationId"),
                    "from_address":    from_addr,
                    "from_name":       from_name,
                    "to_addresses":    to_addrs,
                    "subject":         subject,
                    "received_at":     msg.get("receivedDateTime"),
                    "body_text":       body_text,
                    "attachments":     [],
                    "is_sent":         False,
                }
                r2 = requests.post(f"{ingestor_url}/ingest/email", json=payload, timeout=60)
                if r2.ok:
                    ingested += 1
                    # Apply FamilyBrain/<category> Outlook category tag
                    category = r2.json().get("category", "personal")
                    apply_ingested_category(account, msg_id, category)
                else:
                    print(f"[outlook] ingestor rejected {msg_id}: {r2.text}")

            # Pagination — save nextLink as cursor so retries resume mid-backfill
            next_link  = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")

            if next_link:
                url = next_link
                db.update_sync_cursor(account_id, next_link)  # persist progress
            elif delta_link:
                db.update_sync_cursor(account_id, delta_link)
                url = None
            else:
                # No nextLink or deltaLink — end of a regular-messages page set.
                # Seed a proper delta cursor so future runs use incremental sync.
                # Also triggers when cursor was stuck as a non-delta URL.
                cursor_is_delta = cursor and ("deltaToken" in cursor or "/delta" in cursor)
                if not cursor_is_delta:
                    print(f"[outlook] seeding delta cursor for {account.get('email_address', account_id)}")
                    # Microsoft Graph bakes the $select projection into the deltaLink
                    # itself — every future poll using this cursor is permanently
                    # limited to whatever fields were requested here. Seeding with
                    # $select=id (as this used to do) meant every subsequent
                    # incremental sync came back with id only: no subject, no body,
                    # no from/to — silently skipped forever as "empty_body" with
                    # "(no subject)". Must match the fields the real sync loop needs.
                    seed_resp = requests.get(
                        f"{GRAPH_BASE}/me/mailFolders/inbox/messages/delta"
                        f"?$top=1&$select=id,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId",
                        headers=_headers(account), timeout=30,
                    )
                    seed_data = seed_resp.json()
                    # Walk to end of delta to get the terminal deltaLink
                    while seed_data.get("@odata.nextLink"):
                        seed_resp = requests.get(seed_data["@odata.nextLink"], headers=_headers(account), timeout=30)
                        seed_data = seed_resp.json()
                    if seed_data.get("@odata.deltaLink"):
                        db.update_sync_cursor(account_id, seed_data["@odata.deltaLink"])
                        print(f"[outlook] delta cursor seeded")
                url = None

    except Exception as e:
        print(f"[outlook] sync_email failed for {account['email_address']}: {e}")

    if skipped:
        print(f"[outlook] {skipped} messages skipped (junk/filtered) for {account['email_address']}")

    # Sync SentItems folder separately
    ingested += _sync_sent_items(account, ingestor_url)

    return ingested


def _sync_sent_items(account: dict, ingestor_url: str) -> int:
    """Sync Outlook SentItems folder using its own delta cursor."""
    import requests as req_lib
    account_id = account["id"]
    cursor     = account.get("sent_sync_cursor")
    ingested   = 0

    try:
        if cursor:
            url = cursor
        else:
            initial_days = int(os.environ.get("OUTLOOK_INITIAL_DAYS", "90"))
            since = (datetime.now(timezone.utc) - timedelta(days=initial_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = (
                f"{GRAPH_BASE}/me/mailFolders/SentItems/messages"
                f"?$top=50&$orderby=receivedDateTime+desc"
                f"&$select=id,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId"
                f"&$filter=receivedDateTime+ge+{since}"
            )

        while url:
            resp = req_lib.get(url, headers=_headers(account), timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for msg in data.get("value", []):
                msg_id = msg["id"]
                if db.is_already_ingested(account_id, msg_id):
                    continue

                from_name, from_addr = _parse_address(msg.get("from") or {})
                to_addrs = [
                    r["emailAddress"]["address"]
                    for r in msg.get("toRecipients") or []
                    if r.get("emailAddress", {}).get("address")
                ]
                body_obj  = msg.get("body") or {}
                body_text = (
                    _strip_html(body_obj.get("content", ""))
                    if (body_obj.get("contentType") or "").lower() == "html"
                    else body_obj.get("content", "")
                )
                if not body_text.strip():
                    body_text = msg.get("bodyPreview", "")
                subject = msg.get("subject") or "(no subject)"

                ok, reason = should_ingest(
                    from_address=from_addr,
                    subject=subject,
                    body_text=body_text,
                )
                if not ok:
                    db.mark_skipped(account_id, msg_id, from_addr, subject,
                                    msg.get("receivedDateTime"), reason)
                    continue

                payload = {
                    "account_id":      account_id,
                    "provider_msg_id": msg_id,
                    "thread_id":       msg.get("conversationId"),
                    "from_address":    from_addr,
                    "from_name":       from_name,
                    "to_addresses":    to_addrs,
                    "subject":         subject,
                    "received_at":     msg.get("receivedDateTime"),
                    "body_text":       body_text,
                    "attachments":     [],
                    "is_sent":         True,
                }
                r2 = req_lib.post(f"{ingestor_url}/ingest/email", json=payload, timeout=60)
                if r2.ok:
                    ingested += 1
                else:
                    print(f"[outlook] sent ingestor rejected {msg_id}: {r2.text}")

            next_link  = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")
            if next_link:
                url = next_link
                db.update_sent_sync_cursor(account_id, next_link)
            elif delta_link:
                db.update_sent_sync_cursor(account_id, delta_link)
                url = None
            else:
                if not cursor:
                    seed_resp = req_lib.get(
                        f"{GRAPH_BASE}/me/mailFolders/SentItems/messages/delta?$top=1&$select=id",
                        headers=_headers(account), timeout=30,
                    )
                    seed_data = seed_resp.json()
                    while seed_data.get("@odata.nextLink"):
                        seed_resp = req_lib.get(seed_data["@odata.nextLink"], headers=_headers(account), timeout=30)
                        seed_data = seed_resp.json()
                    if seed_data.get("@odata.deltaLink"):
                        db.update_sent_sync_cursor(account_id, seed_data["@odata.deltaLink"])
                url = None

    except Exception as e:
        print(f"[outlook] _sync_sent_items failed for {account['email_address']}: {e}")

    return ingested


# ── Calendar ───────────────────────────────────────────────────────────────────

def _parse_dt(dt_str: Optional[str], tz_str: Optional[str] = None) -> Optional[datetime]:
    if not dt_str:
        return None
    from dateutil.parser import parse as dtparse
    dt = dtparse(dt_str)
    if dt.tzinfo is None and tz_str:
        import pytz
        try:
            dt = pytz.timezone(tz_str).localize(dt)
        except Exception:
            dt = dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _classify_event(subject: str) -> str:
    s = subject.lower()
    if any(w in s for w in ["pickup", "pick up", "pick-up", "after school care", "aftercare"]):
        return "PICKUP" if "pickup" in s or "pick" in s else "AFTERCARE"
    if any(w in s for w in ["school holiday", "term", "holiday", "excursion", "varsity", "class "]):
        return "school"
    if any(w in s for w in ["gp", "doctor", "physio", "therapy", "ndis", "appointment", "specialist", "rehab", "ot session", "speech"]):
        return "medical"
    return "family"


def sync_calendar(account: dict, mirror_accounts: list[dict], ingestor_url: str = "") -> int:
    """
    Sync Outlook Calendar events into personal.event.
    Routes events to Bills/Holidays/Family calendars and mirrors per routing rules.
    Returns count of events synced.
    """
    from .gmail import create_gmail_event, _write_event_to_calendar, _calendar_service
    from .calendar_router import classify_event, load_routing, target_calendar_id, holiday_stub_summary

    all_accounts = [account] + mirror_accounts
    routing      = load_routing(all_accounts)
    ac           = routing[account["id"]]

    account_id   = account["id"]
    cursor       = account.get("calendar_sync_cursor")
    synced       = 0
    mirror_by_id = {a["id"]: a for a in mirror_accounts}

    try:
        if cursor:
            url = cursor
        else:
            now   = datetime.now(timezone.utc)
            start = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end   = (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url   = (
                f"{GRAPH_BASE}/me/calendarView/delta"
                f"?startDateTime={start}&endDateTime={end}&$select=id,subject,start,end,body,changeKey"
            )

        while url:
            resp = requests.get(url, headers=_headers(account), timeout=30)
            if resp.status_code == 410:
                account["calendar_sync_cursor"] = None
                return sync_calendar(account, mirror_accounts)
            resp.raise_for_status()
            data = resp.json()

            for ev in data.get("value", []):
                provider_id = ev["id"]
                summary     = ev.get("subject", "(no title)")
                is_all_day  = ev.get("isAllDay", False)
                description = ev.get("body", {}).get("content", "")[:500]

                if is_all_day:
                    # Use date objects so _fmt_cal_dt writes {"date": ...} not {"dateTime": ...}
                    from datetime import date as date_type
                    raw_start = ev.get("start", {}).get("dateTime", "")[:10]
                    raw_end   = ev.get("end",   {}).get("dateTime", "")[:10]
                    starts_at = date_type.fromisoformat(raw_start) if raw_start else None
                    ends_at   = date_type.fromisoformat(raw_end)   if raw_end   else None
                else:
                    starts_at = _parse_dt(ev.get("start", {}).get("dateTime"), ev.get("start", {}).get("timeZone"))
                    ends_at   = _parse_dt(ev.get("end",   {}).get("dateTime"), ev.get("end",   {}).get("timeZone"))

                if not starts_at:
                    continue

                from .calendar_router import tag_family_event
                route      = classify_event(summary, description)
                event_type = _classify_event(summary)
                cal_key    = f"outlook:{account['email_address']}:{provider_id}"
                tag, color_id = tag_family_event(summary, description) if route == "family" else (None, None)

                existing     = db.get_sync_map(account_id, provider_id)
                # Outlook delta sync doesn't provide etags; use presence of sync_map as change signal
                is_new_event = not existing

                # Skip events we mirrored out — appointment_updater created them, re-importing
                # them would create duplicate personal.event rows and a feedback loop.
                if db.is_mirror_event(account_id, provider_id):
                    continue

                # Write/update routed Outlook calendar (Bills/Holidays/Family/default)
                target_cal = target_calendar_id(ac, route)
                target_cal_id_stored = None
                if target_cal != ac.default_cal_id:
                    existing_target_id = (existing or {}).get("target_cal_provider_id")
                    try:
                        if existing_target_id:
                            _patch_outlook_event(account, existing_target_id,
                                                 summary, starts_at, ends_at, description)
                            target_cal_id_stored = existing_target_id
                        elif not existing:
                            target_cal_id_stored = _write_outlook_event(
                                account, target_cal, summary, starts_at, ends_at, description,
                                is_all_day=is_all_day)
                            # Holiday day expansion handled by appointment_updater
                    except Exception as e:
                        print(f"[outlook] failed to write to {route} calendar for '{summary}': {e}")

                event_id = db.upsert_event(
                    title=summary,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    event_type=event_type,
                    calendar_source=f"outlook:{account['email_address']}",
                    calendar_event_id=cal_key,
                    notes=description,
                    ingestor_url=ingestor_url,
                )

                # No direct calendar-to-calendar mirroring here.
                # All events flow: ingest → personal.event → appointment_updater → GCal/Outlook.
                # appointment_updater is the sole writer to output calendars.

                synced += 1

            next_link  = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")
            if next_link:
                url = next_link
            elif delta_link:
                db.update_calendar_sync_cursor(account_id, delta_link)
                url = None
            else:
                url = None

    except Exception as e:
        print(f"[outlook] sync_calendar failed for {account['email_address']}: {e}")

    return synced


def _write_outlook_event(account: dict, calendar_id: str, summary: str,
                         starts_at, ends_at, description: str,
                         is_all_day: bool = False) -> str:
    """Write an event to a specific Outlook calendar, return provider event ID."""
    from datetime import date as date_type
    if is_all_day or isinstance(starts_at, date_type):
        def _fmt(d) -> dict:
            return {"dateTime": f"{d}T00:00:00", "timeZone": "UTC"}
        body = {
            "subject":  summary,
            "body":     {"contentType": "text", "content": description},
            "isAllDay": True,
            "start":    _fmt(starts_at),
            "end":      _fmt(ends_at or starts_at),
        }
    else:
        def _fmt(dt: datetime) -> dict:
            return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "AUS Eastern Standard Time"}
        body = {
            "subject": summary,
            "body":    {"contentType": "text", "content": description},
            "start":   _fmt(starts_at),
            "end":     _fmt(ends_at or starts_at),
        }
    resp = requests.post(
        f"{GRAPH_BASE}/me/calendars/{calendar_id}/events",
        headers=_headers(account),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _patch_outlook_event(account: dict, event_id: str, summary: str,
                         starts_at: datetime, ends_at: Optional[datetime], description: str) -> None:
    """Patch an existing Outlook Calendar event in-place."""
    def _fmt(dt: datetime) -> dict:
        return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "AUS Eastern Standard Time"}

    body = {
        "subject": summary,
        "body":    {"contentType": "text", "content": description},
        "start":   _fmt(starts_at),
        "end":     _fmt(ends_at or starts_at),
    }
    resp = requests.patch(
        f"{GRAPH_BASE}/me/events/{event_id}",
        headers=_headers(account),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()


def create_outlook_event(account: dict, summary: str, starts_at: datetime,
                         ends_at: Optional[datetime], description: str = "") -> str:
    """Create an Outlook Calendar event and return its provider event ID."""
    def _fmt(dt: datetime) -> dict:
        return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "AUS Eastern Standard Time"}

    body = {
        "subject": summary,
        "body":    {"contentType": "text", "content": description},
        "start":   _fmt(starts_at),
        "end":     _fmt(ends_at or starts_at),
    }
    resp = requests.post(
        f"{GRAPH_BASE}/me/events",
        headers=_headers(account),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]
