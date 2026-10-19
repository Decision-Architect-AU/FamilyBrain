"""
Google Tasks sync orchestration (Increment 5) — the three flows: outbound
(push new/changed tasks), inbound-add (pull manually-created Google Tasks),
inbound-completion (pull completions, Google -> FamilyBrain only in v1).

Mirrors appointment_updater.py's poll-and-push shape, but against the Tasks
API instead of Calendar, and keyed through calendar_sync_map's
channel='gtask_primary' rows rather than the gcal_event_id/gcal_calendar_id
columns calendar sync uses — tasks were never given dedicated columns on
personal.event the way calendar sync has; the whole point of generalizing
calendar_sync_map (postgres/init/50_google_tasks.sql) was to reuse one
mapping table for both instead of adding a parallel one.
"""
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

from . import db
from . import google_tasks

DB_URL = os.environ["DATABASE_URL"]
_BATCH = 50


def _resolve_task_account(accounts: list[dict]) -> dict | None:
    """Same convention as appointment_updater.py/weekly_digest.py: the
    household's primary Gmail account, regardless of which connected
    account any given task originated from."""
    return next(
        (a for a in accounts if a["provider"] == "gmail" and a.get("is_primary_calendar")),
        None,
    )


def push_outbound(accounts: list[dict]) -> int:
    """
    Push event_type='task' rows due for sync — next_update_at reached (set
    by channel_resolver.materialise(), same mechanism every other outbound
    item already uses), never yet synced, or changed since the last push —
    to the household's Google Tasks list.
    """
    account = _resolve_task_account(accounts)
    if not account:
        print("[task_sync] no primary Gmail account — skipping outbound push")
        return 0

    now = datetime.now(timezone.utc)
    pushed = 0

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.title, e.notes, e.effective_date, e.task_status, e.updated_at,
                       sm.source_provider_id AS task_id, sm.last_synced_at
                FROM personal.event e
                LEFT JOIN personal.calendar_sync_map sm
                    ON sm.event_id = e.id AND sm.channel = 'gtask_primary'
                       AND sm.source_account_id = %(account_id)s
                WHERE e.event_type = 'task'
                  AND e.status NOT IN ('cancelled', 'superseded')
                  AND (
                        sm.id IS NULL
                        OR (e.next_update_at IS NOT NULL AND e.next_update_at <= %(now)s)
                        OR e.updated_at > sm.last_synced_at
                      )
                ORDER BY e.created_at ASC
                LIMIT %(batch)s
                """,
                {"account_id": account["id"], "now": now, "batch": _BATCH},
            )
            rows = list(cur.fetchall())

    for row in rows:
        try:
            _list_id, task_id = google_tasks.push_task(
                account, row["title"], row.get("notes") or "",
                due_date=row.get("effective_date"),
                task_status=row.get("task_status") or "open",
                existing_task_id=row["task_id"],
            )
            db.upsert_sync_map(
                event_id=row["id"], source_account_id=account["id"],
                source_provider_id=task_id, sync_status="synced",
                channel="gtask_primary",
            )
            pushed += 1
        except Exception as e:
            print(f"[task_sync] push failed for event {row['id']} ({row['title'][:40]!r}): {e}")

    if pushed:
        print(f"[task_sync] pushed {pushed} task(s) to Google Tasks")
    return pushed


def pull_inbound(accounts: list[dict]) -> int:
    """
    Pull the Google Tasks list, reconcile against calendar_sync_map:
      - a Google task with no matching sync-map row -> new personal.event
        (manually added directly in Google Tasks).
      - a Google task WITH a sync-map row, marked completed, whose linked
        event isn't yet task_status='done' -> mark it done. v1 is
        Google -> FamilyBrain only for completion; marking done on the
        FamilyBrain side does not push back to Google yet (out of scope,
        per spec).
    """
    account = _resolve_task_account(accounts)
    if not account:
        print("[task_sync] no primary Gmail account — skipping inbound pull")
        return 0

    try:
        tasks = google_tasks.pull_tasks(account)
    except Exception as e:
        print(f"[task_sync] pull_tasks failed: {e}")
        return 0

    created = completed = 0
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            for t in tasks:
                task_id = t["id"]
                title   = t.get("title") or "(untitled)"
                is_done = t.get("status") == "completed"

                cur.execute(
                    """SELECT event_id FROM personal.calendar_sync_map
                       WHERE channel = 'gtask_primary' AND source_account_id = %s AND source_provider_id = %s""",
                    (account["id"], task_id),
                )
                existing = cur.fetchone()

                if not existing:
                    due = t.get("due")
                    effective_date = due[:10] if due else None
                    cur.execute(
                        """
                        INSERT INTO personal.event
                            (title, event_type, status, provenance, calendar_source,
                             notes, task_status, effective_date)
                        VALUES (%s, 'task', 'confirmed', 'google_tasks', 'google_tasks',
                                %s, %s, %s)
                        RETURNING id
                        """,
                        (title, t.get("notes") or "", "done" if is_done else "open", effective_date),
                    )
                    event_id = cur.fetchone()["id"]
                    conn.commit()
                    db.upsert_sync_map(
                        event_id=event_id, source_account_id=account["id"],
                        source_provider_id=task_id, sync_status="synced",
                        channel="gtask_primary",
                    )
                    created += 1
                elif is_done:
                    cur.execute(
                        """UPDATE personal.event SET task_status = 'done'
                           WHERE id = %s AND event_type = 'task' AND task_status IS DISTINCT FROM 'done'""",
                        (existing["event_id"],),
                    )
                    if cur.rowcount:
                        completed += 1
                    conn.commit()

    if created or completed:
        print(f"[task_sync] inbound: {created} new task(s), {completed} completion(s) synced")
    return created + completed


def run_task_sync(accounts: list[dict]) -> int:
    """Entry point called from main.py's task_loop — one full outbound+inbound pass."""
    n = 0
    try:
        n += push_outbound(accounts)
    except Exception as e:
        print(f"[task_sync] outbound pass failed: {e}")
    try:
        n += pull_inbound(accounts)
    except Exception as e:
        print(f"[task_sync] inbound pass failed: {e}")
    return n
