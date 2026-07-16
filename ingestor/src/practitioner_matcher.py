"""
Practitioner resolution for invoice line items.

Matches an extracted practitioner name (e.g. "S. Chen") to an existing
personal.person row, creating a new person only at high confidence. Reuses
the two-tier pattern from asset_writer.find_existing_asset(): exact match
first, then pg_trgm fuzzy similarity — never silently forks a near-duplicate
node, never silently merges two distinct people.
"""
import os
import psycopg2
import psycopg2.extras

from .graph import _cypher1, _merge_edge, build_props, _cypher_val, _build_set

DB_URL = os.environ.get("DATABASE_URL")

FUZZY_THRESHOLD  = 0.6   # matches asset_writer's convention — accept as same person
CREATE_THRESHOLD = 0.3   # below this, no near-miss exists — safe to create a new person
FLIP_MIN_OCCURRENCES = 2  # occurrences required before a new practitioner can override
                          # an established fact_current_<service>


def _conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def resolve_practitioner(name: str, org_slug: str, service_type: str,
                          extraction_confidence: float = 0.7) -> dict:
    """
    Returns {"action": "linked"|"created"|"queued", "person_id": int|None,
             "confidence": int, "match_score": float|None, "reason": str|None}
    """
    name = (name or "").strip()
    if not name:
        return {"action": "queued", "person_id": None, "confidence": 0,
                "match_score": None, "reason": "no name extracted"}

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM personal.person WHERE lower(name) = lower(%s)", (name,))
            exact = cur.fetchone()
            if exact:
                _link(exact["id"], org_slug, service_type, confidence=90)
                return {"action": "linked", "person_id": exact["id"], "confidence": 90, "match_score": 1.0}

            cur.execute(
                """SELECT id, name, similarity(name, %s) AS sim
                   FROM personal.person
                   WHERE relationship = 'provider'
                   ORDER BY sim DESC LIMIT 1""",
                (name,),
            )
            best = cur.fetchone()

    if best and best["sim"] >= FUZZY_THRESHOLD:
        _link(best["id"], org_slug, service_type, confidence=65)
        return {"action": "linked", "person_id": best["id"], "confidence": 65,
                "match_score": float(best["sim"]), "reason": f"fuzzy match to '{best['name']}'"}

    near_miss = best is not None and best["sim"] >= CREATE_THRESHOLD
    if extraction_confidence >= 0.8 and not near_miss:
        person_id = _create_person(name)
        _link(person_id, org_slug, service_type, confidence=60)
        return {"action": "created", "person_id": person_id, "confidence": 60,
                "match_score": float(best["sim"]) if best else None}

    _queue_for_review(name, org_slug, service_type, best)
    return {"action": "queued", "person_id": None, "confidence": 0,
            "match_score": float(best["sim"]) if best else None,
            "reason": "ambiguous fuzzy match" if near_miss else "low extraction confidence"}


def _create_person(name: str) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO personal.person (name, relationship) VALUES (%s, 'provider') RETURNING id",
                (name,),
            )
            person_id = cur.fetchone()["id"]
        conn.commit()
    return person_id


def _link(person_id: int, org_slug: str, service_type: str, confidence: int) -> None:
    """Write/refresh the :Person node and a WORKS_AT edge to the org."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM personal.person WHERE id = %s", (person_id,))
            row = cur.fetchone()
    name = row["name"] if row else str(person_id)
    ref  = f"personal.person:{person_id}"

    node_props = {"ref": ref, "name": name, "role": service_type}
    _cypher1(
        "personal_graph",
        f"MERGE (p:Person {{ref: {_cypher_val('ref', ref)}}}) "
        f"SET {_build_set('p', node_props)} RETURN p",
    )

    if org_slug:
        org_props = build_props({"name": org_slug})
        _cypher1("personal_graph", f"MERGE (o:Organisation {{{org_props}}}) RETURN o")
        _merge_edge(
            "personal_graph",
            f"MATCH (p:Person {{ref: {_cypher_val('ref', ref)}}})",
            f"MATCH (o:Organisation {{name: {_cypher_val('name', org_slug)}}})",
            "WORKS_AT",
            confidence=confidence,
        )


def _queue_for_review(name: str, org_slug: str, service_type: str, best_match: dict | None) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO personal.practitioner_review_queue
                   (extracted_name, org_slug, service_type, suggested_person_id,
                    suggested_person_name, match_score)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (name, org_slug, service_type,
                 best_match["id"] if best_match else None,
                 best_match["name"] if best_match else None,
                 float(best_match["sim"]) if best_match else None),
            )
        conn.commit()


def record_line_item(note_id: int, service_type: str, practitioner_name: str,
                      org_slug: str, line_date: str | None, amount: float | None,
                      subject_person_id: int | None, resolution: dict) -> int:
    """Persist the line item + its resolution outcome for audit and flip-guard counting."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO personal.invoice_line_item
                   (note_id, service_type, practitioner_name, practitioner_person_id,
                    subject_person_id, org_slug, line_date, amount, match_action, match_score)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (note_id, service_type, practitioner_name, resolution.get("person_id"),
                 subject_person_id, org_slug, line_date, amount,
                 resolution.get("action"), resolution.get("match_score")),
            )
            line_item_id = cur.fetchone()["id"]
        conn.commit()
    return line_item_id


def maybe_update_current_service_fact(subject_person_id: int, service_type: str,
                                        practitioner_person_id: int, practitioner_name: str) -> dict:
    """
    Update fact_current_<service> on the subject's asset(s) if this practitioner
    has 2+ recent occurrences for this subject+service — a single locum
    appearance must not displace an established practitioner. Below the
    threshold, the flip is queued for manual confirmation instead of applied.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) AS n FROM personal.invoice_line_item
                   WHERE subject_person_id = %s AND service_type = %s
                     AND practitioner_person_id = %s
                     AND line_date >= (CURRENT_DATE - INTERVAL '180 days')""",
                (subject_person_id, service_type, practitioner_person_id),
            )
            occurrences = cur.fetchone()["n"]

            cur.execute(
                "SELECT id, ref FROM personal.asset WHERE person_id = %s AND status = 'active'",
                (subject_person_id,),
            )
            subject_assets = cur.fetchall()

    if occurrences < FLIP_MIN_OCCURRENCES:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO personal.practitioner_review_queue
                       (extracted_name, service_type, suggested_person_id, suggested_person_name, status)
                       VALUES (%s, %s, %s, %s, 'pending')""",
                    (practitioner_name, service_type, practitioner_person_id,
                     f"{practitioner_name} — only {occurrences} occurrence(s), flip needs confirm"),
                )
            conn.commit()
        return {"flipped": False, "occurrences": occurrences, "queued": True}

    if not subject_assets:
        return {"flipped": False, "occurrences": occurrences, "queued": False,
                "reason": "subject has no asset row to carry the fact"}

    fact_key = f"current_{service_type.lower()}"
    invoice_ref = f"personal.invoice_line_item:subject={subject_person_id}:service={service_type}"
    for a in subject_assets:
        asset_ref = a["ref"] or f"personal.asset:{a['id']}"
        _cypher1(
            "personal_graph",
            f"MATCH (n {{ref: {_cypher_val('ref', asset_ref)}}}) "
            f"SET n.fact_{fact_key} = {_cypher_val('v', practitioner_name)}, "
            f"    n.factsrc_{fact_key} = {_cypher_val('v', [invoice_ref])} "
            f"RETURN n",
        )
    return {"flipped": True, "occurrences": occurrences, "queued": False}
