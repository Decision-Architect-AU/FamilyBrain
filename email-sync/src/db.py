"""Database helpers — reads/writes email_account and email_message tables."""
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from typing import Optional

DB_URL = os.environ["DATABASE_URL"]


def conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Account management ─────────────────────────────────────────────────────────

def get_enabled_accounts() -> list[dict]:
    """Return all enabled email accounts."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM personal.email_account WHERE enabled = true ORDER BY id"
            )
            return cur.fetchall()


def update_token(account_id: int, access_token: str, expiry: datetime) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_account
                   SET access_token = %s, token_expiry = %s, updated_at = now()
                   WHERE id = %s""",
                (access_token, expiry, account_id),
            )
        c.commit()


def update_sync_cursor(account_id: int, cursor: str) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_account
                   SET sync_cursor = %s, last_synced_at = now(), updated_at = now()
                   WHERE id = %s""",
                (cursor, account_id),
            )
        c.commit()


def update_calendar_sync_cursor(account_id: int, cursor: str) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_account
                   SET calendar_sync_cursor = %s, updated_at = now()
                   WHERE id = %s""",
                (cursor, account_id),
            )
        c.commit()


def mark_last_synced(account_id: int) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE personal.email_account SET last_synced_at = now() WHERE id = %s",
                (account_id,),
            )
        c.commit()


# ── Message deduplication ──────────────────────────────────────────────────────

def mark_skipped(account_id: int, provider_msg_id: str, from_address: str,
                 subject: str, received_at, reason: str) -> None:
    """Record a skipped message so it's not re-evaluated on every sync run."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personal.email_message
                    (account_id, provider_msg_id, from_address, subject, received_at,
                     ingest_status, ingest_error, ingest_at)
                VALUES (%s, %s, %s, %s, %s, 'skipped', %s, now())
                ON CONFLICT (account_id, provider_msg_id) DO NOTHING
                """,
                (account_id, provider_msg_id, from_address, subject, received_at, reason),
            )
        c.commit()


def is_already_ingested(account_id: int, provider_msg_id: str) -> bool:
    """Returns True if message has already been ingested or intentionally skipped."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT id FROM personal.email_message
                   WHERE account_id = %s AND provider_msg_id = %s
                     AND ingest_status IN ('ingested', 'skipped')""",
                (account_id, provider_msg_id),
            )
            return cur.fetchone() is not None


def get_retryable_messages(account_id: int) -> list[str]:
    """Return provider_msg_ids with status 'error' or 'pending' (have body to re-fetch)."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT provider_msg_id FROM personal.email_message
                   WHERE account_id = %s AND ingest_status IN ('error', 'pending')
                   ORDER BY received_at DESC NULLS LAST
                   LIMIT 500""",
                (account_id,),
            )
            return [r["provider_msg_id"] for r in cur.fetchall()]


def get_ingested_without_label(account_id: int) -> list[tuple[str, str]]:
    """Return (provider_msg_id, category) for ingested messages that need label backfill."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT provider_msg_id, category FROM personal.email_message
                   WHERE account_id = %s AND ingest_status = 'ingested'
                     AND category IS NOT NULL AND schema_routed IS NULL
                   ORDER BY received_at DESC NULLS LAST
                   LIMIT 500""",
                (account_id,),
            )
            return [(r["provider_msg_id"], r["category"]) for r in cur.fetchall()]


def mark_label_applied(account_id: int, provider_msg_id: str) -> None:
    """Mark that the Gmail label has been applied (reuse schema_routed as a flag)."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE personal.email_message SET schema_routed = 'labelled'
                   WHERE account_id = %s AND provider_msg_id = %s""",
                (account_id, provider_msg_id),
            )
        c.commit()


# ── Calendar sync map ──────────────────────────────────────────────────────────

def _effective_date(starts_at):
    """Return the Brisbane local date for an event start — timezone-free."""
    from datetime import date as date_type
    import pytz
    _brisbane = pytz.timezone("Australia/Brisbane")
    if isinstance(starts_at, date_type) and not hasattr(starts_at, "hour"):
        return starts_at  # already a plain date (all-day event)
    if hasattr(starts_at, "tzinfo") and starts_at.tzinfo:
        return starts_at.astimezone(_brisbane).date()
    return starts_at.date()


def _enrich_event_title(title: str) -> str:
    """
    Prefix title with child name based on:
    - Therapy keywords → Child2 (Olivia)
    - 'Year N' mention → whichever child is in that school year (from personal.person)
    """
    import os, re as _re
    tl = title.lower().strip()

    # Child2 therapy keywords
    child2_names = [n.strip() for n in os.environ.get("CHILD2_NAMES", "").split(",") if n.strip()]
    child2_first = child2_names[0] if child2_names else ""
    _therapy_kw = _re.compile(
        r'^(physio|physiotherapy|speech\s+therapy|speech\s+pathology|'
        r'occupational\s+therapy|weekly\s+ot|speech\s+therapy\s+extra\s+session)$', _re.I
    )
    if child2_first and _therapy_kw.match(tl) and not tl.startswith(child2_first.lower()):
        return f"{child2_first} {title}"

    # School year derivation — look up personal.person
    m = _re.search(r'\b(?:year|yr)\s*(\d+)\b', title, _re.I)
    if m:
        try:
            import psycopg2, psycopg2.extras
            with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as c:
                with c.cursor() as cur:
                    cur.execute(
                        "SELECT name FROM personal.person WHERE school_year = %s LIMIT 1",
                        (int(m.group(1)),),
                    )
                    row = cur.fetchone()
            if row:
                first = row["name"].split()[0]
                if not tl.startswith(first.lower()):
                    return f"{first} {title}"
        except Exception:
            pass

    return title


def upsert_event(
    title: str,
    starts_at: datetime,
    ends_at: Optional[datetime],
    event_type: str,
    calendar_source: str,
    calendar_event_id: str,
    notes: str = "",
    ingestor_url: Optional[str] = None,
    item_type: Optional[str] = None,
    category: Optional[str] = None,
    source_slug: Optional[str] = None,
) -> int:
    """
    Upsert into personal.event, return event id.
    Enriches title (person prefix) and deduplicates before storing.
    If ingestor_url provided, also writes (:Event) node to personal_graph.
    """
    title    = _enrich_event_title(title)
    eff_date = _effective_date(starts_at)
    with conn() as c:
        with c.cursor() as cur:
            # Dedup: same event from multiple sources (Gmail+Outlook mirror, recurring sync)
            # Compare in AEST to handle Outlook events stored as UTC (e.g. 22:00 UTC = 08:00 AEST next day)
            cur.execute(
                """
                SELECT id FROM personal.event
                WHERE lower(title) = lower(%s)
                  AND (starts_at AT TIME ZONE 'Australia/Brisbane')::date
                    = (%s::timestamptz AT TIME ZONE 'Australia/Brisbane')::date
                  AND calendar_event_id != %s
                LIMIT 1
                """,
                (title, starts_at, calendar_event_id),
            )
            existing_dup = cur.fetchone()
            if existing_dup:
                return existing_dup["id"]

            cur.execute(
                """
                INSERT INTO personal.event
                    (title, event_type, starts_at, ends_at, calendar_source, calendar_event_id, notes, effective_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (calendar_event_id) DO UPDATE
                    SET title          = EXCLUDED.title,
                        starts_at      = EXCLUDED.starts_at,
                        ends_at        = EXCLUDED.ends_at,
                        notes          = COALESCE(NULLIF(personal.event.notes, ''), EXCLUDED.notes),
                        effective_date = EXCLUDED.effective_date,
                        updated_at     = now()
                RETURNING id, (xmax = 0) AS inserted,
                          (personal.event.starts_at IS DISTINCT FROM EXCLUDED.starts_at) AS date_changed
                """,
                (title, event_type, starts_at, ends_at, calendar_source, calendar_event_id, notes, eff_date),
            )
            row = cur.fetchone()
        c.commit()

    event_id     = row["id"]
    is_new       = row["inserted"]
    date_changed = row.get("date_changed", False)

    # Cascade date change to relative child events
    if date_changed and not is_new:
        try:
            n = cascade_relative_events(event_id)
            if n:
                print(f"[db] cascaded date change to {n} relative event(s) under event {event_id}")
        except Exception as e:
            print(f"[db] cascade failed for event {event_id}: {e}")

    # Materialise next_update_at for new events via channel rules
    if is_new:
        try:
            from .channel_resolver import materialise
            materialise(
                event_id,
                item_type=item_type or event_type or "calendar_event",
                category=category,
                source_slug=source_slug,
                effective_date=eff_date,
            )
        except Exception as e:
            print(f"[db] channel materialise failed for event {event_id}: {e}")

    # Fire-and-forget: write (:Event) node to personal_graph via ingestor
    if ingestor_url:
        try:
            import requests
            requests.post(
                f"{ingestor_url}/ingest/event",
                json={
                    "event_row_id":      event_id,
                    "title":             title,
                    "starts_at":         starts_at.isoformat() if hasattr(starts_at, "isoformat") else str(starts_at),
                    "ends_at":           ends_at.isoformat() if ends_at and hasattr(ends_at, "isoformat") else (str(ends_at) if ends_at else ""),
                    "event_type":        event_type,
                    "calendar_source":   calendar_source,
                    "calendar_event_id": calendar_event_id,
                    "notes":             notes[:500],
                },
                timeout=10,
            )
        except Exception as e:
            print(f"[db] ingest/event graph call failed for '{title}': {e}")

    return event_id


def cascade_relative_events(parent_event_id: int) -> int:
    """
    When a parent event's date changes, recalculate all child events that are
    relative to it (parent_event_id + relative_offset_days).
    Returns number of children updated.
    """
    updated = 0
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT starts_at FROM personal.event WHERE id = %s",
                (parent_event_id,),
            )
            row = cur.fetchone()
            if not row:
                return 0
            parent_start = row["starts_at"]

            cur.execute(
                """SELECT id, relative_offset_days FROM personal.event
                   WHERE parent_event_id = %s AND relative_offset_days IS NOT NULL""",
                (parent_event_id,),
            )
            children = cur.fetchall()

        for child in children:
            from datetime import timedelta, date as date_type
            offset = child["relative_offset_days"]
            if hasattr(parent_start, "date"):
                new_start = parent_start.date() + timedelta(days=offset)
            else:
                new_start = parent_start + timedelta(days=offset)
            with c.cursor() as cur:
                cur.execute(
                    """UPDATE personal.event
                       SET starts_at = %s, ends_at = %s, updated_at = now()
                       WHERE id = %s""",
                    (new_start, new_start, child["id"]),
                )
            updated += 1
            # Recurse for grandchildren
            updated += cascade_relative_events(child["id"])

        c.commit()
    return updated


def get_sync_map(source_account_id: int, source_provider_id: str) -> Optional[dict]:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT * FROM personal.calendar_sync_map
                   WHERE source_account_id = %s AND source_provider_id = %s""",
                (source_account_id, source_provider_id),
            )
            return cur.fetchone()


def upsert_sync_map(
    event_id: int,
    source_account_id: int,
    source_provider_id: str,
    mirror_account_id: Optional[int] = None,
    mirror_provider_id: Optional[str] = None,
    target_cal_provider_id: Optional[str] = None,
    sync_status: str = "synced",
    etag: Optional[str] = None,
) -> None:
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personal.calendar_sync_map
                    (event_id, source_account_id, source_provider_id,
                     mirror_account_id, mirror_provider_id, target_cal_provider_id,
                     sync_status, last_synced_at, etag, last_etag)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
                ON CONFLICT (source_account_id, source_provider_id) DO UPDATE
                    SET mirror_account_id       = COALESCE(EXCLUDED.mirror_account_id, calendar_sync_map.mirror_account_id),
                        mirror_provider_id      = COALESCE(EXCLUDED.mirror_provider_id, calendar_sync_map.mirror_provider_id),
                        target_cal_provider_id  = COALESCE(EXCLUDED.target_cal_provider_id, calendar_sync_map.target_cal_provider_id),
                        sync_status             = EXCLUDED.sync_status,
                        last_synced_at          = now(),
                        etag                    = COALESCE(EXCLUDED.etag, calendar_sync_map.etag),
                        last_etag               = COALESCE(EXCLUDED.last_etag, calendar_sync_map.last_etag)
                """,
                (event_id, source_account_id, source_provider_id,
                 mirror_account_id, mirror_provider_id, target_cal_provider_id,
                 sync_status, etag, etag if target_cal_provider_id else None),
            )
        c.commit()
