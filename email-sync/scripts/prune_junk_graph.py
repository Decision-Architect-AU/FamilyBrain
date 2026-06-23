"""
Prune junk email nodes from personal_graph and personal.note.

Targets:
  - Emails with ingest_status IN ('skipped', 'marketing', 'skip') that have a note_id
  - Ingested emails categorised 'personal' from known junk sender patterns
"""
import os, sys
sys.path.insert(0, "/app")

import psycopg2
import psycopg2.extras

DB_URL = os.environ["DATABASE_URL"]
GRAPH  = "personal_graph"

JUNK_SENDER_PATTERN = (
    r'(linkedin\.|amazon\.com\.au|amazon\.com\.au|bookshop\.org|reedsy\.|'
    r'rebelsport\.com|aliexpress\.com|biggestmorningtea\.com|'
    r'commercialready\.com|henderson\.com|coolabah|'
    r'newarrival\.|selections\.aliexpress|deals\.aliexpress|mail\.aliexpress)'
)


def run():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Collect note_ids and provider_msg_ids to prune
    cur.execute("""
        SELECT em.note_id, em.provider_msg_id, em.from_address, em.subject
        FROM personal.email_message em
        WHERE em.note_id IS NOT NULL
          AND (
            em.ingest_status IN ('skipped', 'marketing', 'skip')
            OR (em.ingest_status = 'ingested' AND em.category = 'personal'
                AND em.from_address ~* %(pattern)s)
          )
        ORDER BY em.note_id
    """, {"pattern": JUNK_SENDER_PATTERN})
    rows = cur.fetchall()
    print(f"Found {len(rows)} junk email notes to prune")

    note_ids      = [r["note_id"] for r in rows]
    provider_ids  = [r["provider_msg_id"] for r in rows]

    if not note_ids:
        print("Nothing to prune.")
        return

    # ── Prune AGE graph nodes ─────────────────────────────────────────────────
    # Delete Document nodes keyed by row_id (= note_id) — cascades to edges
    # Also delete by filename = 'email:{provider_msg_id}'
    deleted_graph = 0
    for row in rows:
        note_id = row["note_id"]
        pid     = row["provider_msg_id"]
        try:
            cur.execute(f"""
                SELECT * FROM cypher('{GRAPH}', $$
                    MATCH (d:Document {{row_id: {note_id}}})
                    DETACH DELETE d
                    RETURN count(d) AS n
                $$) AS (n agtype)
            """)
            r1 = cur.fetchone()

            cur.execute(f"""
                SELECT * FROM cypher('{GRAPH}', $$
                    MATCH (m:Message {{msg_key: 'email:{pid}'}})
                    DETACH DELETE m
                    RETURN count(m) AS n
                $$) AS (n agtype)
            """)
            r2 = cur.fetchone()
            deleted_graph += 1
        except Exception as e:
            print(f"  graph delete failed for note {note_id}: {e}")
            conn.rollback()
            # Re-open cursor after rollback
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conn.commit()
    print(f"  deleted {deleted_graph} document/message node pairs from graph")

    # ── Clear note_id on email_message (keep the record, just detach the note) ─
    cur.execute("""
        UPDATE personal.email_message
        SET note_id = NULL
        WHERE note_id = ANY(%s)
    """, (note_ids,))
    conn.commit()

    # ── Delete personal.note rows ─────────────────────────────────────────────
    cur.execute("""
        DELETE FROM personal.note WHERE id = ANY(%s)
    """, (note_ids,))
    deleted_notes = cur.rowcount
    conn.commit()
    print(f"  deleted {deleted_notes} personal.note rows")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    run()
