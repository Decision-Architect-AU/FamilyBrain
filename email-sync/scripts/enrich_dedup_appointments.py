"""
1. Enrich event titles with person name prefix (e.g. "Physio" → "Olivia Physio")
2. Dedupe events (keep highest-notes copy per title+date)
3. Delete all events after 2026-07-01 from GCal + DB + graph
4. Delete :Event nodes from personal_graph for removed events
5. Reset calendar sync cursors for clean resync
"""
import os, sys, re
sys.path.insert(0, "/app")

import psycopg2
import psycopg2.extras
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

DB_URL               = os.environ["DATABASE_URL"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GRAPH                = "personal_graph"
CUTOFF               = "2026-07-01"

CHILD2_NAMES = [n.strip() for n in os.environ.get("CHILD2_NAMES", "").split(",") if n.strip()]
CHILD2_FIRST = CHILD2_NAMES[0] if CHILD2_NAMES else "Olivia"

CHILD2_KW = re.compile(
    r'\b(physio|physiotherapy|speech\s+therapy|speech\s+pathology|'
    r'occupational\s+therapy|weekly\s+ot)\b', re.I
)


def get_gcal_service(refresh_token):
    creds = Credentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def enrich_title(title, person_first):
    if not person_first:
        return title
    if title.lower().startswith(person_first.lower()):
        return title
    if CHILD2_KW.search(title):
        return f"{CHILD2_FIRST} {title}"
    return f"{person_first} {title}"


def main():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Gmail credentials for GCal API
    cur.execute("""
        SELECT refresh_token FROM personal.email_account
        WHERE provider = 'gmail' AND sync_calendar = true LIMIT 1
    """)
    row = cur.fetchone()
    service = get_gcal_service(row["refresh_token"]) if row else None

    # ── Step 1: Enrich titles ─────────────────────────────────────────────────
    print("\n── Step 1: Enrich event titles ──")
    cur.execute("""
        SELECT e.id, e.title, e.gcal_event_id, e.gcal_calendar_id,
               split_part(p.name, ' ', 1) AS person_first
        FROM personal.event e
        JOIN personal.person p ON p.id = e.person_id
        WHERE e.title NOT ILIKE split_part(p.name,' ',1) || ' %'
    """)
    to_enrich = cur.fetchall()

    # Also enrich keyword-matching events without person_id
    cur.execute("""
        SELECT id, title, gcal_event_id, gcal_calendar_id, NULL as person_first
        FROM personal.event
        WHERE person_id IS NULL
          AND (title ~* 'physio|physiotherapy|speech therapy|speech pathology|occupational therapy|weekly ot')
          AND title NOT ILIKE %s
    """, (f"{CHILD2_FIRST} %",))
    to_enrich += list(cur.fetchall())

    enriched = 0
    for ev in to_enrich:
        new_title = enrich_title(ev["title"], ev.get("person_first") or "")
        if new_title == ev["title"]:
            continue
        cur.execute("UPDATE personal.event SET title = %s, updated_at = now() WHERE id = %s",
                    (new_title, ev["id"]))
        # Update graph node title
        try:
            cur.execute(f"""
                SELECT * FROM cypher('{GRAPH}', $$
                    MATCH (e:Event {{event_row_id: {ev['id']}}})
                    SET e.title = '{new_title.replace("'", "\\'")}'
                    RETURN count(e)
                $$) AS (n agtype)
            """)
        except Exception as ge:
            print(f"  graph title update failed for {ev['id']}: {ge}")
            conn.rollback()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("UPDATE personal.event SET title = %s, updated_at = now() WHERE id = %s",
                        (new_title, ev["id"]))
        print(f"  '{ev['title']}' → '{new_title}'")
        enriched += 1
    conn.commit()
    print(f"  enriched {enriched} event titles")

    # ── Step 2: Dedupe all events ─────────────────────────────────────────────
    print("\n── Step 2: Dedupe duplicate events ──")
    cur.execute("""
        SELECT e.id, e.gcal_event_id, e.gcal_calendar_id, e.title, e.starts_at::date as day
        FROM personal.event e
        WHERE (e.title, e.starts_at::date) IN (
            SELECT title, starts_at::date FROM personal.event
            GROUP BY title, starts_at::date HAVING COUNT(*) > 1
        )
        AND e.id NOT IN (
            SELECT DISTINCT ON (title, starts_at::date) id
            FROM personal.event
            ORDER BY title, starts_at::date, length(notes) DESC NULLS LAST, id ASC
        )
    """)
    dupes = cur.fetchall()
    print(f"  found {len(dupes)} duplicate rows")

    gcal_deleted = 0
    for ev in dupes:
        if ev["gcal_event_id"] and service:
            try:
                service.events().delete(
                    calendarId=ev["gcal_calendar_id"] or "primary",
                    eventId=ev["gcal_event_id"]
                ).execute()
                gcal_deleted += 1
            except HttpError as e:
                if e.resp.status != 410:
                    print(f"  GCal delete failed {ev['gcal_event_id']}: {e}")
        # Remove graph node
        try:
            cur.execute(f"""
                SELECT * FROM cypher('{GRAPH}', $$
                    MATCH (e:Event {{event_row_id: {ev['id']}}}) DETACH DELETE e RETURN count(e)
                $$) AS (n agtype)
            """)
        except Exception:
            conn.rollback()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    dupe_ids = [ev["id"] for ev in dupes]
    if dupe_ids:
        cur.execute("DELETE FROM personal.event WHERE id = ANY(%s)", (dupe_ids,))
    conn.commit()
    print(f"  removed {len(dupes)} dupes from DB, {gcal_deleted} from GCal")

    # ── Step 3: Delete all future events (>= 1/7/2026) ───────────────────────
    print(f"\n── Step 3: Delete events from {CUTOFF} onwards ──")
    cur.execute("""
        SELECT id, gcal_event_id, gcal_calendar_id, title, starts_at::date as day
        FROM personal.event WHERE starts_at >= %s
    """, (CUTOFF,))
    future = cur.fetchall()
    print(f"  found {len(future)} future events")

    gcal_del2 = 0
    for ev in future:
        if ev["gcal_event_id"] and service:
            try:
                service.events().delete(
                    calendarId=ev["gcal_calendar_id"] or "primary",
                    eventId=ev["gcal_event_id"]
                ).execute()
                gcal_del2 += 1
                if gcal_del2 % 20 == 0:
                    print(f"  {gcal_del2}/{len(future)} GCal events deleted...")
            except HttpError as e:
                if e.resp.status != 410:
                    print(f"  GCal delete failed {ev['gcal_event_id']}: {e}")
        # Remove graph node
        try:
            cur.execute(f"""
                SELECT * FROM cypher('{GRAPH}', $$
                    MATCH (e:Event {{event_row_id: {ev['id']}}}) DETACH DELETE e RETURN count(e)
                $$) AS (n agtype)
            """)
        except Exception:
            conn.rollback()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    future_ids = [ev["id"] for ev in future]
    if future_ids:
        cur.execute("DELETE FROM personal.event WHERE id = ANY(%s)", (future_ids,))
    conn.commit()
    print(f"  deleted {len(future)} from DB, {gcal_del2} from GCal")

    # ── Step 4: Reset calendar sync cursors ──────────────────────────────────
    print("\n── Step 4: Reset calendar sync cursors ──")
    cur.execute("UPDATE personal.email_account SET calendar_sync_cursor = NULL")
    conn.commit()
    print("  cursors cleared — next email-sync run will do full resync")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
