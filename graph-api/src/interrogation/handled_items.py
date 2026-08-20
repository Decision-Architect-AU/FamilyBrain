"""
handled_items — completed obligations, resolved bills, and appointments whose
linked obligations are ALL complete (the "already handled" set). Deterministic
SQL, no LLM calls, read-only (Interrogation Layer, Increment 2).
"""
from datetime import date

from pydantic import BaseModel

_BILL_EVENT_TYPES = ("RENT_PAYMENT", "INSURANCE_RENEWAL", "SUBSCRIPTION_RENEWAL", "RENEWAL")


class Params(BaseModel):
    start: date
    end: date


def run(conn, params: Params) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, obligation_status, effective_date, epistemic, confidence
            FROM personal.event
            WHERE event_type = 'obligation'
              AND obligation_status IN ('completed', 'cancelled')
              AND effective_date BETWEEN %(start)s AND %(end)s
            """,
            {"start": params.start, "end": params.end},
        )
        completed_obligations = [dict(r) for r in cur.fetchall()]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, effective_date, event_type, epistemic, confidence
            FROM personal.event
            WHERE event_type = ANY(%(types)s)
              AND status = 'completed'
              AND effective_date BETWEEN %(start)s AND %(end)s
            """,
            {"types": list(_BILL_EVENT_TYPES), "start": params.start, "end": params.end},
        )
        resolved_bills = [dict(r) for r in cur.fetchall()]

    # Appointments (any non-obligation event) whose REQUIRED_FOR-linked
    # obligations exist and are ALL completed/cancelled — NOT EXISTS an
    # incomplete one, per spec.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.effective_date, e.event_type, e.epistemic, e.confidence
            FROM personal.event e
            WHERE e.event_type <> 'obligation'
              AND e.effective_date BETWEEN %(start)s AND %(end)s
              AND e.status NOT IN ('cancelled', 'superseded')
              AND EXISTS (
                  SELECT 1 FROM personal.event ob
                  WHERE ob.required_for_event_id = e.id AND ob.event_type = 'obligation'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM personal.event ob
                  WHERE ob.required_for_event_id = e.id AND ob.event_type = 'obligation'
                    AND ob.obligation_status NOT IN ('completed', 'cancelled')
              )
            """,
            {"start": params.start, "end": params.end},
        )
        fully_handled_appointments = [dict(r) for r in cur.fetchall()]

    return {
        "completed_obligations": completed_obligations,
        "resolved_bills": resolved_bills,
        "fully_handled_appointments": fully_handled_appointments,
    }


def _marker(item: dict) -> str:
    if item["epistemic"] == "inferred" and item["confidence"] is not None:
        return f" [inferred, {item['confidence']}%]"
    return ""


def pack_text(result: dict) -> str:
    n = (len(result["completed_obligations"]) + len(result["resolved_bills"])
         + len(result["fully_handled_appointments"]))
    lines = [f"{n} handled item(s):"]
    for o in result["completed_obligations"]:
        lines.append(f"- obligation done: {o['title']} ⟦ref:personal.event:{o['id']}⟧{_marker(o)}")
    for b in result["resolved_bills"]:
        lines.append(f"- bill resolved: {b['title']} ⟦ref:personal.event:{b['id']}⟧{_marker(b)}")
    for a in result["fully_handled_appointments"]:
        lines.append(f"- fully handled: {a['title']} ⟦ref:personal.event:{a['id']}⟧{_marker(a)}")
    return "\n".join(lines)
