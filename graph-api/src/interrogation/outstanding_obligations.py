"""
outstanding_obligations — obligations with their effective status (blocked/
stale derived at query time, never stored) and effective deadline (derived
from a REQUIRED_FOR parent when the obligation has no date of its own, never
copied onto the row). Deterministic SQL, no LLM calls, read-only
(Interrogation Layer, Increment 2).
"""
from datetime import date

from pydantic import BaseModel, field_validator

from ._params import split_list

DEFAULT_STATUSES = ["active", "waiting", "blocked"]


class Params(BaseModel):
    person: str | None = None
    statuses: list[str] | None = None
    start: date | None = None
    end: date | None = None

    _split_statuses = field_validator("statuses", mode="before")(split_list)


def run(conn, params: Params) -> dict:
    statuses = params.statuses or DEFAULT_STATUSES
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH stale_cfg AS (
                SELECT COALESCE(
                    (SELECT value::int FROM personal.event_config WHERE key = 'OBLIGATION_STALE_DAYS'),
                    21
                ) AS days
            ),
            derived AS (
                SELECT e.id, e.title, e.owner, e.waiting_on, e.person_id, p.name AS person_name,
                       e.epistemic, e.confidence,
                       e.obligation_status AS stored_status,
                       e.effective_date, e.required_for_event_id,
                       parent.effective_date AS parent_effective_date,
                       e.obligation_status_changed_at,
                       EXISTS (
                           SELECT 1 FROM personal.obligation_dependency od
                           JOIN personal.event dep ON dep.id = od.depends_on_event_id
                           WHERE od.obligation_event_id = e.id
                             AND dep.obligation_status NOT IN ('completed', 'cancelled')
                       ) AS has_incomplete_dependency,
                       (e.obligation_status = 'active'
                        AND e.obligation_status_changed_at < now() - ((SELECT days FROM stale_cfg) || ' days')::interval
                       ) AS is_stale
                FROM personal.event e
                LEFT JOIN personal.person p ON p.id = e.person_id
                LEFT JOIN personal.event parent ON parent.id = e.required_for_event_id
                WHERE e.event_type = 'obligation'
            ),
            classified AS (
                SELECT *,
                    CASE
                        WHEN stored_status IN ('completed', 'cancelled') THEN stored_status
                        WHEN has_incomplete_dependency THEN 'blocked'
                        WHEN is_stale THEN 'stale'
                        ELSE stored_status
                    END AS effective_status,
                    COALESCE(effective_date, parent_effective_date) AS effective_deadline
                FROM derived
            )
            SELECT * FROM classified
            WHERE effective_status = ANY(%(statuses)s)
              AND (%(person)s::text IS NULL OR person_name = %(person)s)
              AND (%(start)s::date IS NULL OR effective_deadline IS NULL OR effective_deadline >= %(start)s)
              AND (%(end)s::date IS NULL OR effective_deadline IS NULL OR effective_deadline <= %(end)s)
            ORDER BY effective_deadline NULLS LAST
            """,
            {"statuses": statuses, "person": params.person,
             "start": params.start, "end": params.end},
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"obligations": rows, "count": len(rows)}


def pack_text(result: dict) -> str:
    lines = [f"{result['count']} outstanding obligation(s):"]
    for o in result["obligations"]:
        ref = f" ⟦ref:personal.event:{o['id']}⟧"
        who = f" ({o['owner']})" if o["owner"] else ""
        waiting = f" — waiting on {o['waiting_on']}" if o["waiting_on"] else ""
        when = o["effective_deadline"].strftime("%a %-d %b") if o["effective_deadline"] else "no deadline"
        marker = f" [inferred, {o['confidence']}%]" if o["epistemic"] == "inferred" and o["confidence"] is not None else ""
        lines.append(f"- [{o['effective_status']}] {o['title']}{ref}{who} — due {when}{waiting}{marker}")
    return "\n".join(lines)
