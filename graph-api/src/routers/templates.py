"""Response template endpoints."""
import os
import uuid
from typing import Any

import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/templates", tags=["templates"])

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")


def _conn():
    return psycopg2.connect(DATABASE_URL)


class TemplateCreate(BaseModel):
    id: str
    domain: str
    subtype: str
    depth: str
    description: str = ""
    sections: list[dict[str, Any]]
    max_length: int = 400
    tone: str = ""
    example: str = ""


class TemplatePatch(BaseModel):
    description: str | None = None
    sections: list[dict[str, Any]] | None = None
    max_length: int | None = None
    tone: str | None = None
    example: str | None = None


@router.get("")
def list_templates():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT t.*,
                    COUNT(il.id) AS usage_count,
                    ROUND(
                        100.0 * COUNT(il.id) FILTER (WHERE il.quality_flag IS NOT NULL)
                        / NULLIF(COUNT(il.id), 0), 1
                    ) AS flag_rate_pct
                FROM personal.response_templates t
                LEFT JOIN personal.interaction_log il ON il.template_id = t.id
                GROUP BY t.id ORDER BY t.domain, t.subtype
            """)
            return cur.fetchall()


@router.get("/{template_id:path}")
def get_template(template_id: str):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM personal.response_templates WHERE id = %s", (template_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return row


@router.post("")
def create_template(body: TemplateCreate):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO personal.response_templates
                    (id, domain, subtype, depth, description, sections, max_length, tone, example)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                body.id, body.domain, body.subtype, body.depth,
                body.description,
                psycopg2.extras.Json(body.sections),
                body.max_length, body.tone, body.example,
            ))
    return {"id": body.id}


@router.patch("/{template_id:path}")
def patch_template(template_id: str, body: TemplatePatch):
    sets = ["version = version + 1", "updated_at = NOW()"]
    params: list = []
    if body.description is not None:
        sets.append("description = %s"); params.append(body.description)
    if body.sections is not None:
        sets.append("sections = %s"); params.append(psycopg2.extras.Json(body.sections))
    if body.max_length is not None:
        sets.append("max_length = %s"); params.append(body.max_length)
    if body.tone is not None:
        sets.append("tone = %s"); params.append(body.tone)
    if body.example is not None:
        sets.append("example = %s"); params.append(body.example)
    params.append(template_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE personal.response_templates SET {', '.join(sets)} WHERE id = %s",
                params,
            )
    return {"ok": True}


@router.get("/{template_id:path}/interactions")
def template_interactions(template_id: str, limit: int = 20):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM personal.interaction_log WHERE template_id = %s "
                "ORDER BY logged_at DESC LIMIT %s",
                (template_id, limit),
            )
            return cur.fetchall()


@router.post("/{template_id:path}/test")
def test_template(template_id: str):
    row = get_template(template_id)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM personal.interaction_log WHERE template_id = %s "
                "ORDER BY logged_at DESC LIMIT 1",
                (template_id,),
            )
            last = cur.fetchone()
    if not last:
        raise HTTPException(status_code=404, detail="No interactions found for this template")

    system = (
        f"You are Family Brain, a household assistant responding via WhatsApp.\n\n"
        f"Response format ({row['id']}):\n"
        f"Max length: {row['max_length']} characters\n"
        f"Tone: {row.get('tone', '')}\n\n"
        f"Example:\n{row.get('example', '')}\n\n"
        f"Context:\n{last['context_snapshot']}"
    )

    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": last.get("model", "qwen2.5:14b"),
                  "prompt": last["query_text"],
                  "system": system, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        new_response = resp.json().get("response", "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "template_id": template_id,
        "original":    last["response_text"],
        "replayed":    new_response,
        "log_id":      last["id"],
    }
