"""
changes_since — meaningful changes since a timestamp. Audit-log FALLBACK, not
audit-log-backed: confirmed live that audit.log has zero write coverage from
graph-api/wa-agent/email-sync (only ingestor/agents/scrapers/podcast-agents
write to it, and the table is empty regardless), so per spec 2.4's own
fallback instructions this is built on personal.event.updated_at /
obligation_status_changed_at deltas and personal.fact_conflict.detected_at
instead. Deterministic SQL, no LLM calls, read-only (Interrogation Layer,
Increment 2).
"""
from datetime import datetime

from pydantic import BaseModel

LIMITATION = (
    "Backed by personal.event.updated_at / obligation_status_changed_at deltas "
    "and personal.fact_conflict.detected_at, not the audit log — audit.log has "
    "zero write coverage from graph-api/wa-agent/email-sync today."
)


class Params(BaseModel):
    since_ts: datetime
    person: str | None = None


def run(conn, params: Params) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.event_type, e.effective_date, e.updated_at, e.status,
                   e.obligation_status, e.obligation_status_changed_at, p.name AS person_name,
                   e.epistemic, e.confidence
            FROM personal.event e
            LEFT JOIN personal.person p ON p.id = e.person_id
            WHERE e.updated_at > %(since)s
              AND (%(person)s::text IS NULL OR p.name = %(person)s)
            ORDER BY e.updated_at DESC
            """,
            {"since": params.since_ts, "person": params.person},
        )
        changed_events = [dict(r) for r in cur.fetchall()]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT node_ref, fact_key, existing_value, new_value, detected_at
            FROM personal.fact_conflict
            WHERE detected_at > %s
            ORDER BY detected_at DESC
            """,
            (params.since_ts,),
        )
        new_conflicts = [dict(r) for r in cur.fetchall()]

    return {
        "changed_events": changed_events,
        "new_conflicts": new_conflicts,
        "limitation": LIMITATION,
    }


def pack_text(result: dict) -> str:
    lines = [f"{len(result['changed_events'])} changed event(s), {len(result['new_conflicts'])} new conflict(s)."]
    for e in result["changed_events"]:
        marker = f" [inferred, {e['confidence']}%]" if e["epistemic"] == "inferred" and e["confidence"] is not None else ""
        lines.append(f"- {e['title']} ⟦ref:personal.event:{e['id']}⟧ (updated {e['updated_at']}){marker}")
    for c in result["new_conflicts"]:
        # node_ref is already stored pre-formatted as "personal.event:{id}" (see
        # postgres/init/46_obligations.sql) — wrap it directly, no new data needed.
        lines.append(f"- CONFLICT on {c['node_ref']}.{c['fact_key']} ⟦ref:{c['node_ref']}⟧: {c['existing_value']!r} vs {c['new_value']!r}")
    lines.append(f"[{result['limitation']}]")
    return "\n".join(lines)
