"""
variance_pack — routine deviations + asset-rule expectation-vs-observed diffs.
Deterministic, no LLM calls, read-only (Interrogation Layer, Increment 2).

Routine half wraps _routine_pack (a duplicate of wa-agent/src/routine_context_pack.py
— see that module's header for why it's a copy, not an HTTP call).

Asset-rule half is genuinely net-new: confirmed live that
personal.recurring_obligation has 0 rows and no previous/expected-amount
column today, so there's no baseline to diff against yet. This is built
correctly against the schema and will start producing real variances once
that table has data and a baseline column — it is not a bug that it returns
empty now.
"""
from datetime import date

from pydantic import BaseModel

from . import _routine_pack


class Params(BaseModel):
    start: date
    end: date


def run(conn, params: Params) -> dict:
    packs = _routine_pack.assemble_all_packs(conn=conn)
    routine_text = _routine_pack.packs_to_text(packs) if packs else ""

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, category, amount, frequency, next_due FROM personal.recurring_obligation"
        )
        recurring = [dict(r) for r in cur.fetchall()]

    asset_rule_variances = []
    for ro in recurring:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, effective_date, notes
                FROM personal.event
                WHERE lower(title) LIKE lower(%s)
                  AND effective_date BETWEEN %s AND %s
                ORDER BY effective_date DESC LIMIT 1
                """,
                (f"%{ro['name']}%", params.start, params.end),
            )
            recent = cur.fetchone()
        if recent:
            # No stored observed-amount field on personal.event to diff
            # against ro['amount'] yet — surfaced as a candidate for review,
            # not a fabricated number.
            asset_rule_variances.append({"recurring_obligation": ro, "most_recent_event": dict(recent)})

    return {
        "routine_deviations": packs,
        "routine_text": routine_text,
        "asset_rule_variances": asset_rule_variances,
    }


def _fmt_dev_date(dev: dict) -> str:
    if dev.get("interval"):
        return dev["interval"]
    if dev.get("date"):
        return dev["date"].strftime("%-d %b %a")
    return ""


def pack_text(result: dict) -> str:
    """
    Ref-tagged rendering built directly from the structured `routine_deviations`
    list (each pack's `differences`), not `_routine_pack.packs_to_text()`'s
    opaque string — that function has no per-item ref to attach. Routine
    deviations have no personal.event row of their own (they're synthesized
    from rules/gaps/conflicts, not queried occurrences), so the routine itself
    (a personal.asset row) is the only grounded reference available — every
    line for a routine shares that routine's ⟦ref:personal.asset:{id}⟧.
    Each deviation dict already carries its own correct glyph (from
    _classify_deviations), so it's passed through as-is rather than
    re-derived — the model is instructed to copy it verbatim, same as ref
    handles, not invent one.
    """
    lines: list[str] = []
    for pack in result["routine_deviations"]:
        ref = f"⟦ref:personal.asset:{pack['routine_id']}⟧"
        real_devs = [d for d in pack.get("differences", []) if d.get("type") != "NORMAL"]
        if not real_devs:
            lines.append(f"- {pack['routine']} {ref}: normal, no deviations.")
            continue
        for dev in real_devs:
            when = _fmt_dev_date(dev)
            cause = f" — {dev['cause']}" if dev.get("cause") else ""
            lines.append(f"- {dev['glyph']} {pack['routine']} {ref} {when}: {dev['type']}{cause}")

    if result["asset_rule_variances"]:
        for v in result["asset_rule_variances"]:
            ro, ev = v["recurring_obligation"], v["most_recent_event"]
            lines.append(
                f"- Asset-rule review: {ro['name']} (expected {ro['amount']}/{ro['frequency']}) "
                f"— recent activity: {ev['title']} ⟦ref:personal.event:{ev['id']}⟧ on {ev['effective_date']}"
            )

    if not lines:
        return "No routine deviations or asset-rule variances in this window."
    return "\n".join(lines)
