"""Entity schema + reconcile + bills endpoints."""
import os
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(tags=["schemas"])

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(DATABASE_URL)


class SchemaPatch(BaseModel):
    required_fields: list[dict[str, Any]] | None = None
    optional_fields: list[dict[str, Any]] | None = None
    reminder_rules: list[dict[str, Any]] | None = None
    reconcile_config: dict[str, Any] | None = None


@router.get("/schemas")
def list_schemas():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM personal.entity_schemas ORDER BY entity_type")
            return cur.fetchall()


@router.get("/schemas/{entity_type:path}")
def get_schema(entity_type: str):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM personal.entity_schemas WHERE entity_type = %s",
                (entity_type,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Schema not found")
    return row


@router.patch("/schemas/{entity_type:path}")
def patch_schema(entity_type: str, body: SchemaPatch):
    sets = ["updated_at = NOW()"]
    params: list = []
    if body.required_fields is not None:
        sets.append("required_fields = %s")
        params.append(psycopg2.extras.Json(body.required_fields))
    if body.optional_fields is not None:
        sets.append("optional_fields = %s")
        params.append(psycopg2.extras.Json(body.optional_fields))
    if body.reminder_rules is not None:
        sets.append("reminder_rules = %s")
        params.append(psycopg2.extras.Json(body.reminder_rules))
    if body.reconcile_config is not None:
        sets.append("reconcile_config = %s")
        params.append(psycopg2.extras.Json(body.reconcile_config))
    params.append(entity_type)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE personal.entity_schemas SET {', '.join(sets)} WHERE entity_type = %s",
                params,
            )
    return {"ok": True}


class ReconcileRequest(BaseModel):
    supplier: str
    amount: float
    date: str
    receipt_node_id: str | None = None
    sender: str | None = None


@router.post("/reconcile/match")
def reconcile_match(req: ReconcileRequest):
    from src import db as age_db

    # Find open Bill nodes in AGE
    try:
        result = age_db.cypher_query(
            "MATCH (b:Bill) WHERE b.status IN ['unpaid', 'overdue'] RETURN b"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    best_match = None
    best_score = 0.0

    for node in result["nodes"]:
        props = node.get("properties", {})
        score = 0.0

        # Supplier fuzzy match
        provider = str(props.get("provider", "")).lower()
        supplier = req.supplier.lower()
        if provider == supplier:
            score += 0.5
        elif supplier in provider or provider in supplier:
            score += 0.3

        # Amount match
        try:
            bill_amount = float(props.get("amount", 0))
            if abs(bill_amount - req.amount) < 0.01:
                score += 0.3
            elif abs(bill_amount - req.amount) <= 0.50:
                score += 0.2
            elif abs(bill_amount - req.amount) / max(bill_amount, 1) <= 0.05:
                score += 0.1
        except (TypeError, ValueError):
            pass

        if score > best_score:
            best_score = score
            best_match = node

    if not best_match or best_score < 0.6:
        return {"matched": False, "confidence": best_score}

    return {
        "matched":     True,
        "confidence":  round(best_score, 2),
        "auto":        best_score >= 0.85,
        "bill_id":     best_match["id"],
        "bill_props":  best_match["properties"],
    }


@router.get("/reconcile/unmatched")
def unmatched_receipts():
    from src import db as age_db
    try:
        result = age_db.cypher_query(
            "MATCH (r:Receipt) WHERE r.match_method IS NULL OR r.match_method = 'unmatched' RETURN r"
        )
        return result["nodes"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bills/open")
def open_bills(cost_centre: str | None = None):
    from src import db as age_db
    try:
        where = ""
        if cost_centre:
            where = f" AND b.cost_centre = '{cost_centre}'"
        result = age_db.cypher_query(
            f"MATCH (b:Bill) WHERE b.status IN ['unpaid', 'overdue']{where} RETURN b"
        )
        return result["nodes"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bills/summary")
def bills_summary():
    from src import db as age_db
    try:
        result = age_db.cypher_query(
            "MATCH (b:Bill) WHERE b.status IN ['unpaid', 'overdue'] RETURN b"
        )
        by_centre: dict[str, dict] = {}
        for node in result["nodes"]:
            props = node["properties"]
            centre = props.get("cost_centre", "Unknown")
            if centre not in by_centre:
                by_centre[centre] = {"cost_centre": centre, "total": 0.0, "count": 0, "bills": []}
            try:
                by_centre[centre]["total"] += float(props.get("amount", 0))
                by_centre[centre]["count"] += 1
            except (TypeError, ValueError):
                pass
        return list(by_centre.values())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
