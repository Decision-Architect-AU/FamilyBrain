"""
dependency_chain — "what could stop X": an event, its REQUIRED_FOR-linked
obligations, and each obligation's DEPENDS_ON chain with completion state.
Deterministic SQL (recursive CTE), no LLM calls, read-only (Interrogation
Layer, Increment 2).
"""
from pydantic import BaseModel


class Params(BaseModel):
    event_ref: str  # "personal.event:{id}"


def _event_id(event_ref: str) -> int:
    try:
        return int(event_ref.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        raise ValueError(f"invalid event_ref (expected 'personal.event:{{id}}'): {event_ref}")


def run(conn, params: Params) -> dict:
    event_id = _event_id(params.event_ref)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, effective_date, status FROM personal.event WHERE id = %s",
            (event_id,),
        )
        target = cur.fetchone()
    if not target:
        return {"event": None, "required_obligations": []}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, obligation_status, effective_date, owner, waiting_on,
                   epistemic, confidence
            FROM personal.event
            WHERE event_type = 'obligation' AND required_for_event_id = %s
            """,
            (event_id,),
        )
        obligations = [dict(r) for r in cur.fetchall()]

    for ob in obligations:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE chain AS (
                    SELECT od.obligation_event_id, od.depends_on_event_id, 1 AS depth
                    FROM personal.obligation_dependency od
                    WHERE od.obligation_event_id = %(ob_id)s
                    UNION ALL
                    SELECT od.obligation_event_id, od.depends_on_event_id, c.depth + 1
                    FROM personal.obligation_dependency od
                    JOIN chain c ON od.obligation_event_id = c.depends_on_event_id
                    WHERE c.depth < 20
                )
                SELECT c.depth, e.id, e.title, e.obligation_status
                FROM chain c
                JOIN personal.event e ON e.id = c.depends_on_event_id
                ORDER BY c.depth
                """,
                {"ob_id": ob["id"]},
            )
            ob["depends_on_chain"] = [dict(r) for r in cur.fetchall()]

    return {"event": dict(target), "required_obligations": obligations}


def pack_text(result: dict) -> str:
    if not result["event"]:
        return "Event not found."
    ev = result["event"]
    lines = [f"What could stop \"{ev['title']}\" ⟦ref:personal.event:{ev['id']}⟧:"]
    if not result["required_obligations"]:
        lines.append("- No linked obligations.")
    for ob in result["required_obligations"]:
        marker = f" [inferred, {ob['confidence']}%]" if ob["epistemic"] == "inferred" and ob["confidence"] is not None else ""
        lines.append(f"- [{ob['obligation_status']}] {ob['title']} ⟦ref:personal.event:{ob['id']}⟧{marker}")
        for dep in ob["depends_on_chain"]:
            lines.append(f"    ↳ depends on [{dep['obligation_status']}] {dep['title']} ⟦ref:personal.event:{dep['id']}⟧")
    return "\n".join(lines)
