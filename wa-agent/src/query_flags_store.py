"""
Storage for config.query_flags — one row per /query fallback activation.
Written immediately on fallback detection (before any retry logic runs) so a
crash mid-retry still leaves a needs_review row instead of a silent gap; see
family-brain-whatsapp-query-fallback-spec.md P0-2/P0-4. Same connection
pattern as feedback.save_feedback (config schema, curator role, best-effort —
a storage failure here must never block the user's response).
"""
import os
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")


def list_flags(limit: int = 100) -> list[dict]:
    """Recent activations, newest first — dashboard display (P1)."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, created_at, source, original_query_text, concept_guess,
                       structured_retrieval_hits, generic_retrieval_hits, outcome,
                       needs_review, reviewed_at, review_result
                FROM config.query_flags
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


def create_flag(
    source: str,
    original_query_text: str,
    classified_targets: list[str],
    extracted_keywords: list[str],
    concept_guess: str | None,
    structured_retrieval_hits: int,
    first_response_kind: str,
) -> int | None:
    """Returns the new row id, or None if the insert failed."""
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO config.query_flags
                        (source, original_query_text, classified_targets, extracted_keywords,
                         concept_guess, structured_retrieval_hits, first_response_kind)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (source, original_query_text, classified_targets, extracted_keywords,
                      concept_guess, structured_retrieval_hits, first_response_kind))
                flag_id = cur.fetchone()[0]
            conn.commit()
        return flag_id
    except Exception as e:
        print(f"[query_flags] create failed: {e}")
        return None


def update_flag(flag_id: int | None, **fields) -> None:
    """Updates the same row with retry outcome — never inserts a second row
    per activation. Not called yet in Phase 1 (no retry exists); wired up in
    Phase 2 alongside the generic fallback query."""
    if not flag_id or not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE config.query_flags SET {set_clause} WHERE id = %s",
                    (*fields.values(), flag_id),
                )
            conn.commit()
    except Exception as e:
        print(f"[query_flags] update failed: {e}")
