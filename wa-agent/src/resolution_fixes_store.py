"""
Storage + application for config.resolution_fixes — approval-gated fixes
staged by the nightly review pass (family-brain-whatsapp-query-fallback-spec.md
P0-6). No graph write happens anywhere except approve_fix(), and only for a
row that is currently 'pending' — the whole point of staging is that nothing
changes until a human taps approve.
"""
import os
import json
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("DATABASE_URL")


def list_fixes(status: str | None = None) -> list[dict]:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT rf.*, qf.original_query_text FROM config.resolution_fixes rf "
                    "JOIN config.query_flags qf ON qf.id = rf.flag_id "
                    "WHERE rf.status = %s ORDER BY rf.created_at DESC",
                    (status,),
                )
            else:
                cur.execute(
                    "SELECT rf.*, qf.original_query_text FROM config.resolution_fixes rf "
                    "JOIN config.query_flags qf ON qf.id = rf.flag_id "
                    "ORDER BY rf.created_at DESC LIMIT 100"
                )
            return [dict(r) for r in cur.fetchall()]


def get_fix(fix_id: int) -> dict | None:
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM config.resolution_fixes WHERE id = %s", (fix_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def _decide(fix_id: int, status: str) -> bool:
    """Returns False if the row wasn't 'pending' (already decided) — the
    caller treats that as a no-op, not an error, since a double-tap in the
    dashboard shouldn't re-apply anything."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE config.resolution_fixes SET status = %s, decided_at = now() "
                "WHERE id = %s AND status = 'pending'",
                (status, fix_id),
            )
            applied = cur.rowcount > 0
        conn.commit()
    return applied


def approve_fix(fix_id: int) -> dict:
    fix = get_fix(fix_id)
    if not fix:
        return {"ok": False, "error": "not found"}
    if fix["status"] != "pending":
        return {"ok": False, "error": f"already {fix['status']}"}

    payload = fix["payload"] if isinstance(fix["payload"], dict) else json.loads(fix["payload"])

    if fix["fix_type"] == "alias":
        from src.query_flags_review import apply_alias_fix
        apply_alias_fix(payload["graph"], payload["from_name"], payload["to_name"])
    # fix_type == "pattern": no write needed here — approval itself is what
    # makes the regex active; main.py's query-time gate reads status='approved'
    # rows directly.

    applied = _decide(fix_id, "approved")
    return {"ok": applied}


def reject_fix(fix_id: int) -> dict:
    applied = _decide(fix_id, "rejected")
    return {"ok": applied}


def approved_pattern_fixes() -> list[dict]:
    """Cached by the caller (main.py) — this is a plain read, no caching here."""
    with psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM config.resolution_fixes WHERE fix_type = 'pattern' AND status = 'approved'"
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        out.append(p)
    return out
