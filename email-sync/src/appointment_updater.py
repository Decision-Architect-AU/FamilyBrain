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
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, date, timedelta
from googleapiclient.discovery import build

from .db import get_enabled_accounts, conn
from .gmail import _gmail_service, _fmt_cal_dt
from .calendar_router import (
    classify_event, target_calendar_id, tag_family_event,
    expand_holiday_days, load_routing, _TAG_COLORS,
)

DB_URL   = os.environ["DATABASE_URL"]
_BATCH   = 50


# ── Google Calendar helpers ───────────────────────────────────────────────────

def _cal_service(gmail_account: dict):
    svc   = _gmail_service(gmail_account)
    creds = svc._http.credentials
    return build("calendar", "v3", credentials=creds)


def _write_gcal(cal_svc, cal_id: str, ev: dict, color_id: str | None = None) -> str:
    """Insert event, return gcal event id."""
    from datetime import date as date_type
    starts = ev["starts_at"]
    ends   = ev["ends_at"] or ev["starts_at"]

    body = {
        "summary":     ev["title"],
        "description": ev.get("notes") or "",
        "start":       _fmt_cal_dt(starts),
        "end":         _fmt_cal_dt(ends),
    }
    if color_id:
        body["colorId"] = color_id

    result = cal_svc.events().insert(calendarId=cal_id, body=body).execute()
    return result["id"]


def _patch_gcal(cal_svc, cal_id: str, gcal_id: str, ev: dict,
                 color_id: str | None = None) -> None:
    """Patch an existing GCal event."""
    starts = ev["starts_at"]
    ends   = ev["ends_at"] or ev["starts_at"]

    body = {
        "summary":     ev["title"],
        "description": ev.get("notes") or "",
        "start":       _fmt_cal_dt(starts),
        "end":         _fmt_cal_dt(ends),
    }
    if color_id:
        body["colorId"] = color_id

    cal_svc.events().patch(calendarId=cal_id, eventId=gcal_id, body=body).execute()


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
    gmail_acct = next((a for a in accounts if a["provider"] == "gmail"), None)
    if not gmail_acct:
        print("[appt] no Gmail account — skipping")
        return 0

    try:
        cal_svc = _cal_service(gmail_acct)
    except Exception as e:
        print(f"[appt] calendar service init failed: {e}")
        return 0

    routing = load_routing(accounts)
    ac      = routing.get(gmail_acct["id"])
    if not ac:
        return 0

    now = datetime.now(timezone.utc)

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as rconn:
        with rconn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, event_type, starts_at, ends_at, effective_date,
                       calendar_source, notes,
                       gcal_event_id, gcal_calendar_id, calendar_written_at, next_update_at,
                       updated_at
                FROM personal.event
                WHERE (
                    gcal_event_id IS NULL
                    OR updated_at > calendar_written_at
                    OR (next_update_at IS NOT NULL AND next_update_at <= %s)
                )
                AND calendar_source NOT LIKE 'gmail:%%'    -- skip events already in GCal source
                ORDER BY effective_date ASC NULLS LAST
                LIMIT %s
                """,
                (now, _BATCH),
            )
            events = list(cur.fetchall())

    if not events:
        return 0

    print(f"[appt] updating {len(events)} event(s)")
    processed = 0

    for ev in events:
        ev_id  = ev["id"]
        title  = ev["title"] or ""
        notes  = ev["notes"] or ""

        try:
            route    = classify_event(title, notes)
            cal_id   = target_calendar_id(ac, route)
            tag, color_id = tag_family_event(title, notes) if route == "family" else (None, None)

            gcal_id   = ev.get("gcal_event_id")
            stored_cal = ev.get("gcal_calendar_id")

            if gcal_id and stored_cal == cal_id:
                # Already in the right calendar — patch
                _patch_gcal(cal_svc, cal_id, gcal_id, ev, color_id=color_id)
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
                            next_update_at      = %s
                        WHERE id = %s
                        """,
                        (gcal_id, cal_id, nxt, ev_id),
                    )
                wconn.commit()

            print(f"[appt] {'patch' if ev.get('gcal_event_id') else 'write'} "
                  f"'{title[:50]}' → {route} cal"
                  + (f" | next check {nxt.date()}" if nxt else ""))
            processed += 1

        except Exception as e:
            print(f"[appt] failed for event {ev_id} '{title[:40]}': {e}")

    return processed
