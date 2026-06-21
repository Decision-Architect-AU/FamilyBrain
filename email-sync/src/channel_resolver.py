"""
Channel resolver.

Given an item (type, category, source_slug, effective_date), finds the first
matching channel_rule for a target outbound channel and returns:
  - next_update_at  (when appointment_updater should first process it)
  - target_slot     (bills | family | holidays | default)
  - color_id        (GCal colorId, or None)

Called at ingest time to materialise next_update_at on personal.event / personal.note.
"""
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, date, timedelta
from typing import Optional

DB_URL = os.environ["DATABASE_URL"]

_AEST = timezone(timedelta(hours=10))


def _parse_schedule(schedule: str, effective_date) -> Optional[datetime]:
    """
    Convert a schedule string + effective_date into a concrete next_update_at.

    Schedules:
      immediate          → now()
      before_event:Nd    → effective_date - N days at 06:00 AEST
      on_due_date        → effective_date at 06:00 AEST
      batch:daily:HH:MM  → next occurrence of HH:MM AEST
      never              → None
    """
    if not schedule or schedule == "never":
        return None

    now = datetime.now(_AEST)

    if schedule == "immediate":
        return now

    # Normalise effective_date to a date object
    if isinstance(effective_date, datetime):
        eff = effective_date.astimezone(_AEST).date()
    elif isinstance(effective_date, date):
        eff = effective_date
    else:
        return now  # no date → push immediately

    if schedule == "on_due_date":
        target = datetime(eff.year, eff.month, eff.day, 6, 0, tzinfo=_AEST)
        return target if target > now else now

    if schedule.startswith("before_event:"):
        try:
            days = int(schedule.split(":")[1].rstrip("d"))
            target_date = eff - timedelta(days=days)
            target = datetime(target_date.year, target_date.month, target_date.day,
                              6, 0, tzinfo=_AEST)
            return target if target > now else now
        except (IndexError, ValueError):
            return now

    if schedule.startswith("batch:daily:"):
        try:
            _, _, hhmm = schedule.split(":")
            hh, mm = int(hhmm[:2]), int(hhmm[2:]) if len(hhmm) > 2 else 0
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        except (ValueError, IndexError):
            return now

    return now  # unknown schedule → push immediately


def resolve(
    item_type: str,
    category: Optional[str] = None,
    source_slug: Optional[str] = None,
    effective_date=None,
    preferred_channel_slug: Optional[str] = None,
) -> dict:
    """
    Find the first matching channel_rule for this item and return:
      {
        "next_update_at": datetime | None,
        "target_slot":    str | None,
        "color_id":       str | None,
        "channel_slug":   str | None,
        "schedule":       str,
      }

    Queries all enabled outbound channels for matching rules, ordered by priority.
    If preferred_channel_slug is given, only that channel is checked.
    """
    slug_filter = "AND c.slug = %(slug)s" if preferred_channel_slug else ""

    query = f"""
        SELECT cr.schedule, cr.target_slot, cr.color_id, c.slug AS channel_slug
        FROM   personal.channel_rule cr
        JOIN   personal.channel c ON c.id = cr.channel_id
        WHERE  c.direction IN ('outbound', 'both')
          AND  c.enabled = true
          AND  cr.enabled = true
          AND  (cr.item_type  IS NULL OR cr.item_type  = %(item_type)s)
          AND  (cr.category   IS NULL OR cr.category   = %(category)s)
          AND  (cr.source_slug IS NULL OR cr.source_slug = %(source_slug)s)
          {slug_filter}
        ORDER BY cr.priority ASC
        LIMIT 1
    """
    try:
        with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
            with c.cursor() as cur:
                cur.execute(query, {
                    "item_type":   item_type,
                    "category":    category or "",
                    "source_slug": source_slug or "",
                    "slug":        preferred_channel_slug,
                })
                row = cur.fetchone()
    except Exception as e:
        print(f"[channel] rule lookup failed: {e}")
        row = None

    if not row:
        # Default: push immediately
        return {
            "next_update_at": datetime.now(_AEST),
            "target_slot":    "default",
            "color_id":       None,
            "channel_slug":   None,
            "schedule":       "immediate",
        }

    return {
        "next_update_at": _parse_schedule(row["schedule"], effective_date),
        "target_slot":    row["target_slot"],
        "color_id":       row["color_id"],
        "channel_slug":   row["channel_slug"],
        "schedule":       row["schedule"],
    }


def materialise(event_id: int, item_type: str, category: Optional[str] = None,
                source_slug: Optional[str] = None, effective_date=None) -> None:
    """
    Resolve rules for an event and write next_update_at directly to personal.event.
    Call this right after inserting/upserting a personal.event row.
    """
    result = resolve(item_type, category, source_slug, effective_date)
    nxt    = result["next_update_at"]

    try:
        with psycopg2.connect(DB_URL) as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    UPDATE personal.event
                    SET next_update_at = %s
                    WHERE id = %s AND (next_update_at IS NULL OR next_update_at > %s)
                    """,
                    (nxt, event_id, nxt),
                )
            c.commit()
    except Exception as e:
        print(f"[channel] materialise failed for event {event_id}: {e}")
