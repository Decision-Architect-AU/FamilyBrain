"""
WhatsApp conversation-session store (Interrogation Layer 1.3).

Holds the last executed interrogation plan + result refs + focus entity per
sender, so a follow-up WhatsApp message ("what about the second one") can be
resolved against recent context. Increment 3 builds the actual drill-down
resolution logic that reads this; this module is just the real (not stub)
read/write helpers with the TTL already baked in, so Increment 3 doesn't have
to guess the intended semantics.
"""
import json


def get_session(conn, sender: str) -> dict | None:
    """
    Returns the sender's session dict, or None if there isn't one or it's
    older than 30 minutes (TTL enforced at read time — no cron cleanup at this
    scale, per spec).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT last_plan, last_result_refs, focus_entity, last_answer_summary, updated_at
            FROM personal.wa_session
            WHERE sender = %s AND updated_at > now() - interval '30 minutes'
            """,
            (sender,),
        )
        row = cur.fetchone()
    if not row:
        return None
    last_plan, last_result_refs, focus_entity, last_answer_summary, updated_at = row
    return {
        "last_plan": last_plan,
        "last_result_refs": last_result_refs,
        "focus_entity": focus_entity,
        "last_answer_summary": last_answer_summary,
        "updated_at": updated_at,
    }


def save_session(
    conn,
    sender: str,
    last_plan: dict | list | None = None,
    last_result_refs: list | None = None,
    focus_entity: str | None = None,
    last_answer_summary: str | None = None,
) -> None:
    """
    Upsert the sender's session, resetting updated_at (and so the TTL).
    last_answer_summary is written in the same upsert as everything else —
    keeping it out of this statement would let it fall out of sync with the
    rest of the row's TTL (a write that only touched last_plan would leave a
    stale summary sitting past its actual freshness window).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO personal.wa_session
                (sender, last_plan, last_result_refs, focus_entity, last_answer_summary, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (sender) DO UPDATE SET
                last_plan = EXCLUDED.last_plan,
                last_result_refs = EXCLUDED.last_result_refs,
                focus_entity = EXCLUDED.focus_entity,
                last_answer_summary = EXCLUDED.last_answer_summary,
                updated_at = now()
            """,
            (sender, json.dumps(last_plan) if last_plan is not None else None,
             json.dumps(last_result_refs) if last_result_refs is not None else None,
             focus_entity, last_answer_summary),
        )
    conn.commit()
