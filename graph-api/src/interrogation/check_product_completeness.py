"""
check_product_completeness — "relevance without completeness" detector for
Increment 4 (Product Entity & Investigation Lifecycle). Pure lookup: does the
Product row have the fact field a matching investigation_rule requires?
Deterministic, no LLM calls, read-only. States what is documented and what is
missing — never infers or concludes a warranty status.
"""
from pydantic import BaseModel

# Fixed allowlist mapping investigation_rule.required_fact_field values to
# real personal.product columns — required_fact_field is operator-authored
# rule config, not end-user input, but interpolating an arbitrary column name
# into SQL is still the wrong shape; this keeps the set of checkable fields
# explicit and query-safe.
_CHECKABLE_FIELDS = {
    "warranty_period_months",
    "warranty_expiry_date",
    "install_date",
    "cost",
    "vendor_org_ref",
}


class Params(BaseModel):
    product_id: int
    trigger_event_type: str


def run(conn, params: Params) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, asset_id, name, category, install_date, cost,
                   vendor_org_ref, warranty_period_months, warranty_expiry_date,
                   source_doc_ref
            FROM personal.product
            WHERE id = %(product_id)s
            """,
            {"product_id": params.product_id},
        )
        product = cur.fetchone()
        if not product:
            return {"is_complete": True, "missing_field": None, "evidence": {}, "rule_matched": None,
                     "error": f"no product with id {params.product_id}"}

        # Same wildcard-match idiom as channel_rule: NULL column = matches
        # anything, lowest priority number wins, first row taken.
        cur.execute(
            """
            SELECT id, trigger_event_type, product_category, required_fact_field,
                   reason_template, follow_up_action, priority
            FROM personal.investigation_rule
            WHERE enabled
              AND trigger_event_type = %(trigger_event_type)s
              AND (product_category IS NULL OR product_category = %(category)s)
            ORDER BY priority ASC
            LIMIT 1
            """,
            {"trigger_event_type": params.trigger_event_type, "category": product["category"]},
        )
        rule = cur.fetchone()

    evidence = {
        "install_date": product["install_date"],
        "cost": product["cost"],
        "vendor": product["vendor_org_ref"],
        "source_doc_ref": product["source_doc_ref"],
    }

    if not rule:
        return {"is_complete": True, "missing_field": None, "evidence": evidence, "rule_matched": None}

    field = rule["required_fact_field"]
    if field not in _CHECKABLE_FIELDS:
        # Rule config references a field this primitive doesn't know how to
        # check — fail toward "complete" (no false gap) rather than guessing.
        return {"is_complete": True, "missing_field": None, "evidence": evidence,
                "rule_matched": dict(rule),
                "error": f"required_fact_field {field!r} is not a checkable product column"}

    is_missing = product[field] is None
    return {
        "is_complete": not is_missing,
        "missing_field": field if is_missing else None,
        "evidence": evidence,
        "rule_matched": dict(rule),
    }


def pack_text(result: dict) -> str:
    if result.get("error"):
        return f"Product completeness check: {result['error']}"
    if result["is_complete"]:
        return "Product completeness check: no gap found."
    rule = result["rule_matched"] or {}
    reason = rule.get("reason_template") or f"missing {result['missing_field']}"
    ev = result["evidence"]
    ev_bits = [f"{k}: {v}" for k, v in ev.items() if v is not None]
    ev_line = ", ".join(ev_bits) if ev_bits else "no other evidence on file"
    return f"Product completeness gap — {reason} ({ev_line})"
