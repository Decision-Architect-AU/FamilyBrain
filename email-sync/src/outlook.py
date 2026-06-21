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
_h2t.ignore_links = True
_h2t.ignore_images = True
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

_OUTLOOK_COLOURS = {
    "ndis":      "purple",
    "health":    "red",
    "finance":   "green",
    "property":  "orange",
    "insurance": "blue",
    "travel":    "teal",
    "vehicle":   "yellow",
    "school":    "lightGray",
    "legal":     "darkRed",
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
            requests.post(
                f"{GRAPH_BASE}/me/outlook/masterCategories",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"displayName": label_name, "color": colour},
                timeout=30,
            )
            print(f"[outlook] Created category '{label_name}'")
        _outlook_category_cache.add(label_name)
    except Exception as e:
        print(f"[outlook] Failed to ensure category '{label_name}': {e}")


def apply_ingested_category(account: dict, msg_id: str, category: str) -> None:
    """Tag an Outlook message with FamilyBrain/<category> category."""
    try:
        token      = _token(account)
        label_name = f"FamilyBrain/{category}"
        _ensure_outlook_category(token, category)
        requests.patch(
            f"{GRAPH_BASE}/me/messages/{msg_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"categories": [label_name]},
            timeout=15,
        )
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
            url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages?$top=50&$orderby=receivedDateTime+desc&$select=id,subject,from,toRecipients,receivedDateTime,body,conversationId&$filter=receivedDateTime+ge+{since}"

        while url:
            resp = requests.get(url, headers=_headers(account), timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for msg in data.get("value", []):
                msg_id = msg["id"]
                if db.is_already_ingested(account_id, msg_id):
                    continue

                from_name, from_addr = _parse_address(msg.get("from", {}))
                to_addrs = [
                    r["emailAddress"]["address"]
                    for r in msg.get("toRecipients", [])
                    if r.get("emailAddress", {}).get("address")
                ]
                body_obj  = msg.get("body", {})
                body_text = (
                    _strip_html(body_obj.get("content", ""))
                    if body_obj.get("contentType", "").lower() == "html"
                    else body_obj.get("content", "")
                )

                subject = msg.get("subject", "(no subject)")

                # Only sync Focused inbox — skip Other, except financial/property domains
                if msg.get("inferenceClassification") == "other":
                    domain = from_addr.split("@")[-1].lower() if "@" in from_addr else ""
                    _FINANCIAL_DOMAINS = {
                        "propertyme.com", "console.com.au", "myrealestatediary.com",
                        "propertyware.com", "energyaustralia.com.au", "ergon.com.au",
                        "originenergy.com.au", "agl.com.au", "origin.com.au",
                        "ato.gov.au", "qro.qld.gov.au", "ndia.gov.au", "ndis.gov.au",
                        "strataunit.com.au", "bodycopcorp.com.au",
                    }
                    if domain not in _FINANCIAL_DOMAINS:
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
                # Initial fetch via regular endpoint — no deltaLink returned.
                # Seed a delta cursor from now so future runs pick up new messages only.
                if not cursor:
                    seed_resp = requests.get(
                        f"{GRAPH_BASE}/me/mailFolders/inbox/messages/delta?$top=1&$select=id",
                        headers=_headers(account), timeout=30,
                    )
                    seed_data = seed_resp.json()
                    seed_delta = seed_data.get("@odata.deltaLink") or seed_data.get("@odata.nextLink")
                    # Walk to end of delta to get the terminal deltaLink
                    while seed_data.get("@odata.nextLink"):
                        seed_resp = requests.get(seed_data["@odata.nextLink"], headers=_headers(account), timeout=30)
                        seed_data = seed_resp.json()
                    if seed_data.get("@odata.deltaLink"):
                        db.update_sync_cursor(account_id, seed_data["@odata.deltaLink"])
                url = None

    except Exception as e:
        print(f"[outlook] sync_email failed for {account['email_address']}: {e}")

    if skipped:
        print(f"[outlook] {skipped} messages skipped (junk/filtered) for {account['email_address']}")
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
    if any(w in s for w in ["school", "term", "holiday", "excursion"]):
        return "school"
    if any(w in s for w in ["gp", "doctor", "physio", "therapy", "ndis", "appointment", "specialist"]):
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

                if is_new_event:
                    for (mirror_acct_id, mirror_slot) in ac.mirror_to:
                        mirror_acct = mirror_by_id.get(mirror_acct_id)
                        if not mirror_acct or not mirror_acct.get("sync_calendar"):
                            continue
                        mirror_ac  = routing.get(mirror_acct_id)
                        effective_slot = route if mirror_slot == "route" else mirror_slot
                        mirror_cal = target_calendar_id(mirror_ac, effective_slot) if mirror_ac else None
                        try:
                            if mirror_acct["provider"] == "gmail":
                                from .gmail import _calendar_service, _write_event_to_calendar
                                mirror_svc = _calendar_service(mirror_acct)
                                mirror_id  = _write_event_to_calendar(
                                    mirror_svc, mirror_cal or "primary",
                                    summary, starts_at, ends_at, description
                                )
                            else:
                                mirror_id = create_outlook_event(mirror_acct, summary, starts_at, ends_at, description)
                            db.upsert_sync_map(
                                event_id, account_id, provider_id,
                                mirror_account_id=mirror_acct_id,
                                mirror_provider_id=mirror_id,
                                target_cal_provider_id=target_cal_id_stored,
                                sync_status="synced",
                            )
                        except Exception as e:
                            print(f"[outlook] mirror to {mirror_acct['email_address']} failed for '{summary}': {e}")
                            db.upsert_sync_map(event_id, account_id, provider_id, sync_status="error")
                elif target_cal_id_stored:
                    db.upsert_sync_map(
                        event_id, account_id, provider_id,
                        target_cal_provider_id=target_cal_id_stored,
                        sync_status="synced",
                    )

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
