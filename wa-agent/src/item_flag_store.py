"""
Storage for personal.item_flag — force-review flags on events (and eventually
note/asset). wa-agent's WhatsApp "flag <description>" command inserts here
directly; email-sync's review_loop polls the table and does the actual
recovery work. See postgres/init/45_item_flag.sql and email-sync/src/item_review.py.
"""
import os
import re
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")

# Words a user's natural phrasing wraps around the actual title ("flag MY kooza
# BOOKING") that would otherwise never appear in the title itself — plainto_tsquery
# ANDs every word together, so leaving these in means "my kooza booking" only
# matches a title containing literally all three words, which real titles rarely do.
_FILLER_WORDS = {
    "my", "the", "a", "an", "for", "review", "booking", "event", "that",
    "this", "please", "flag", "favourite", "favorite",
}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    return [w for w in words if w.lower() not in _FILLER_WORDS]


def resolve_event_by_title(text: str) -> dict | None:
    """
    Best-effort match of a free-text description ("my kooza booking") to a
    personal.event row, for the WhatsApp flag command. Strips filler words and
    OR-matches the remaining keywords (plainto_tsquery's default AND would
    otherwise require every filler word to also appear in the real title),
    ranked by how many keywords matched; falls back to a plain ILIKE on the
    original text if every word turned out to be filler.
    """
    keywords = _keywords(text)
    tsquery = " | ".join(keywords) if keywords else None

    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            if tsquery:
                cur.execute(
                    """
                    SELECT id, title, effective_date, status,
                           ts_rank(title_tsv, to_tsquery('english', %s)) AS rank
                    FROM personal.event
                    WHERE title_tsv @@ to_tsquery('english', %s)
                    ORDER BY rank DESC, effective_date DESC NULLS LAST
                    LIMIT 1
                    """,
                    (tsquery, tsquery),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)

            cur.execute(
                """
                SELECT id, title, effective_date, status
                FROM personal.event
                WHERE title ILIKE '%%' || %s || '%%'
                ORDER BY effective_date DESC NULLS LAST
                LIMIT 1
                """,
                (text,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def create_flag(
    entity_type: str,
    entity_id: int,
    reason: str | None = None,
    source: str = "whatsapp",
    requested_by: str | None = None,
) -> int | None:
    """Returns the new row id, or None if one is already active for this item."""
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO personal.item_flag (entity_type, entity_id, reason, source, requested_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (entity_type, entity_id)
                        WHERE status IN ('pending', 'reviewing', 'needs_user_input')
                    DO NOTHING
                    RETURNING id
                    """,
                    (entity_type, entity_id, reason, source, requested_by),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as e:
        print(f"[item_flag_store] create failed: {e}")
        return None
