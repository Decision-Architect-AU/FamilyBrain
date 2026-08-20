"""
events_in_window — events (any type) within a date window, with linked-
obligation counts and any open per-fact conflicts. Deterministic SQL, no LLM
calls, read-only (Interrogation Layer, Increment 2).
"""
from datetime import date

from pydantic import BaseModel, field_validator

from ._params import split_list


class Params(BaseModel):
    start: date
    end: date
    person: str | None = None
    event_types: list[str] | None = None

    _split_event_types = field_validator("event_types", mode="before")(split_list)


def run(conn, params: Params) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.event_type, e.starts_at, e.effective_date,
                   e.person_id, p.name AS person_name, e.location, e.status,
                   e.obligation_status, e.epistemic, e.confidence,
                   COALESCE(ob.cnt, 0) AS linked_obligations
            FROM personal.event e
            LEFT JOIN personal.person p ON p.id = e.person_id
            LEFT JOIN (
                SELECT required_for_event_id, count(*) AS cnt
                FROM personal.event
                WHERE event_type = 'obligation' AND required_for_event_id IS NOT NULL
                GROUP BY required_for_event_id
            ) ob ON ob.required_for_event_id = e.id
            WHERE e.effective_date BETWEEN %(start)s AND %(end)s
              AND e.status NOT IN ('cancelled', 'superseded')
              AND (%(person)s::text IS NULL OR p.name = %(person)s)
              AND (%(event_types)s::text[] IS NULL OR e.event_type = ANY(%(event_types)s))
            ORDER BY e.effective_date, e.starts_at
            """,
            {"start": params.start, "end": params.end,
             "person": params.person, "event_types": params.event_types},
        )
        events = [dict(r) for r in cur.fetchall()]

    # Fact-conflict flags via a lighter-weight relational join on
    # personal.fact_conflict.node_ref, rather than a full AGE traversal (see
    # ingestor/src/graph.py's dossier-merge precedent for the established
    # relational+graph merge pattern this mirrors).
    refs = [f"personal.event:{e['id']}" for e in events]
    conflicts_by_ref: dict[str, list] = {}
    if refs:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_ref, fact_key, existing_value, new_value FROM personal.fact_conflict "
                "WHERE node_ref = ANY(%s) AND resolved_at IS NULL",
                (refs,),
            )
            for row in cur.fetchall():
                conflicts_by_ref.setdefault(row["node_ref"], []).append({
                    "fact_key": row["fact_key"],
                    "existing_value": row["existing_value"],
                    "new_value": row["new_value"],
                })
    for e in events:
        e["fact_conflicts"] = conflicts_by_ref.get(f"personal.event:{e['id']}", [])

    return {"events": events, "count": len(events)}


def pack_text(result: dict) -> str:
    lines = [f"{result['count']} event(s):"]
    for e in result["events"]:
        when = e["effective_date"].strftime("%a %-d %b") if e["effective_date"] else "(no date)"
        ref = f" ⟦ref:personal.event:{e['id']}⟧"
        # Conflict takes priority over the plain epistemic marker — a
        # conflicting fact is a stronger, more specific signal than "inferred".
        if e["fact_conflicts"]:
            marker = " [CONFLICT]"
        elif e["epistemic"] == "inferred":
            marker = f" [inferred, {e['confidence']}%]" if e["confidence"] is not None else " [inferred]"
        else:
            marker = ""
        obl = f" — {e['linked_obligations']} obligation(s)" if e["linked_obligations"] else ""
        lines.append(f"- {when}: {e['title']}{ref}{obl}{marker}")
    return "\n".join(lines)
