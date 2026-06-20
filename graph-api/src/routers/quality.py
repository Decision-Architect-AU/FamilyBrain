"""Response Quality Lab endpoints."""
import os
import time
import uuid
from typing import Any

import httpx
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/quality", tags=["quality"])

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")


def _conn():
    conn = psycopg2.connect(DATABASE_URL)
    psycopg2.extras.register_uuid()
    return conn


class LogEntry(BaseModel):
    sender_id: str
    sender_number: str
    query_text: str
    intent: str
    context_nodes: list[str] = []
    context_snapshot: dict[str, Any] = {}
    prompt_version: str = "unknown"
    response_text: str
    model: str = ""
    latency_ms: int = 0
    whatsapp_message_id: str | None = None
    template_id: str | None = None
    intent_subtype: str | None = None
    intent_depth: str | None = None


class FlagPatch(BaseModel):
    quality_flag: str | None = None
    flag_note: str | None = None
    ideal_response: str | None = None
    added_to_examples: bool | None = None


class ReactionRequest(BaseModel):
    whatsapp_message_id: str
    emoji: str
    reactor_number: str


POSITIVE_EMOJIS = {"👍", "❤️", "✅", "🙏", "😍", "🔥"}
NEGATIVE_EMOJIS = {"👎", "❌", "😡", "🤬", "💔"}
UNCERTAIN_EMOJIS = {"🤔", "❓", "😕", "🧐"}

EMOJI_MAP = {
    **{e: "positive"  for e in POSITIVE_EMOJIS},
    **{e: "negative"  for e in NEGATIVE_EMOJIS},
    **{e: "uncertain" for e in UNCERTAIN_EMOJIS},
}


@router.post("/log")
def log_interaction(entry: LogEntry):
    log_id = str(uuid.uuid4())
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO personal.interaction_log (
                    id, sender_id, sender_number, query_text, intent,
                    context_nodes, context_snapshot, prompt_version,
                    response_text, model, latency_ms, whatsapp_message_id,
                    template_id, intent_subtype, intent_depth
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                log_id, entry.sender_id, entry.sender_number,
                entry.query_text, entry.intent,
                psycopg2.extras.Json(entry.context_nodes),
                psycopg2.extras.Json(entry.context_snapshot),
                entry.prompt_version, entry.response_text,
                entry.model, entry.latency_ms, entry.whatsapp_message_id,
                entry.template_id, entry.intent_subtype, entry.intent_depth,
            ))
    return {"id": log_id}


@router.get("/log")
def list_log(
    domain:  str | None = None,
    flag:    str | None = None,
    limit:   int = Query(default=50, le=200),
    offset:  int = 0,
    search:  str | None = None,
):
    clauses = []
    params: list = []
    if domain:
        clauses.append("intent = %s"); params.append(domain)
    if flag:
        clauses.append("quality_flag = %s"); params.append(flag)
    if search:
        clauses.append("(query_text ILIKE %s OR response_text ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM personal.interaction_log {where} "
                f"ORDER BY logged_at DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            return cur.fetchall()


@router.get("/log/{log_id}")
def get_log_entry(log_id: str):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM personal.interaction_log WHERE id = %s", (log_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.patch("/log/{log_id}")
def patch_log_entry(log_id: str, body: FlagPatch):
    sets = []
    params: list = []
    if body.quality_flag is not None:
        sets.append("quality_flag = %s, reviewed_at = NOW()"); params.append(body.quality_flag)
    if body.flag_note is not None:
        sets.append("flag_note = %s"); params.append(body.flag_note)
    if body.ideal_response is not None:
        sets.append("ideal_response = %s"); params.append(body.ideal_response)
    if body.added_to_examples is not None:
        sets.append("added_to_examples = %s"); params.append(body.added_to_examples)
    if not sets:
        return {"ok": True}
    params.append(log_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE personal.interaction_log SET {', '.join(sets)} WHERE id = %s",
                params,
            )
    return {"ok": True}


@router.post("/log/{log_id}/replay")
def replay(log_id: str):
    row = get_log_entry(log_id)
    context_snapshot = row.get("context_snapshot") or {}
    prompt_version   = row.get("prompt_version", "unknown")
    query_text       = row.get("query_text", "")

    # Fetch current prompt template if available
    template_id = row.get("template_id")
    system = f"You are Family Brain, a household assistant. (Prompt version: {prompt_version})"
    if template_id:
        try:
            with _conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM personal.response_templates WHERE id = %s", (template_id,))
                    tmpl = cur.fetchone()
            if tmpl and tmpl.get("example"):
                system += f"\n\nExample response:\n{tmpl['example']}"
        except Exception:
            pass

    prompt = f"Context:\n{context_snapshot}\n\nQuery: {query_text}"
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": row.get("model", "qwen2.5:14b"), "prompt": prompt,
                  "system": system, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        new_response = resp.json().get("response", "")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "original":  row.get("response_text"),
        "replayed":  new_response,
        "log_id":    log_id,
        "template_id": template_id,
    }


@router.get("/examples")
def get_examples(domain: str | None = None, format: str = "json"):
    clauses = ["added_to_examples = TRUE"]
    params: list = []
    if domain:
        clauses.append("intent = %s"); params.append(domain)
    where = "WHERE " + " AND ".join(clauses)
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT prompt_version, context_snapshot, response_text, ideal_response, intent "
                f"FROM personal.interaction_log {where} ORDER BY logged_at DESC",
                params,
            )
            rows = cur.fetchall()

    if format == "jsonl":
        from fastapi.responses import PlainTextResponse
        lines = [
            '{"prompt_version":' + f'"{r["prompt_version"]}",'
            f'"context":{psycopg2.extras.Json(r["context_snapshot"]).getquoted().decode()},'
            f'"response":{psycopg2.extras.Json(r["response_text"]).getquoted().decode()},'
            f'"ideal":{psycopg2.extras.Json(r["ideal_response"] or "").getquoted().decode()}'
            "}"
            for r in rows
        ]
        return PlainTextResponse("\n".join(lines), media_type="application/jsonl")
    return rows


@router.get("/summary")
def quality_summary():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE quality_flag IS NOT NULL) AS flagged,
                    COUNT(*) FILTER (WHERE emoji_feedback = 'positive') AS positive_reactions,
                    COUNT(*) FILTER (WHERE emoji_feedback = 'negative') AS negative_reactions
                FROM personal.interaction_log
                WHERE logged_at > NOW() - INTERVAL '30 days'
            """)
            totals = cur.fetchone()

            cur.execute("""
                SELECT intent, COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE quality_flag IS NOT NULL) AS flagged,
                    MODE() WITHIN GROUP (ORDER BY quality_flag) AS top_flag
                FROM personal.interaction_log
                WHERE logged_at > NOW() - INTERVAL '30 days'
                GROUP BY intent ORDER BY flagged DESC
            """)
            by_domain = cur.fetchall()

            cur.execute("""
                SELECT quality_flag, COUNT(*) AS count
                FROM personal.interaction_log
                WHERE quality_flag IS NOT NULL
                  AND logged_at > NOW() - INTERVAL '30 days'
                GROUP BY quality_flag ORDER BY count DESC
            """)
            by_flag = cur.fetchall()

    return {"totals": totals, "by_domain": by_domain, "by_flag": by_flag}


@router.post("/reaction")
def handle_reaction(req: ReactionRequest):
    signal = EMOJI_MAP.get(req.emoji)
    if not signal:
        return {"matched": False, "reason": "emoji_ignored"}

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, quality_flag FROM personal.interaction_log "
                "WHERE whatsapp_message_id = %s",
                (req.whatsapp_message_id,),
            )
            row = cur.fetchone()

    if not row:
        return {"matched": False, "reason": "message_not_found"}

    log_id = row["id"]
    new_flag = row.get("quality_flag")
    if signal == "negative" and not new_flag:
        new_flag = "emoji_flagged"
    elif signal == "positive" and not new_flag:
        new_flag = "good"

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE personal.interaction_log SET emoji_feedback = %s, quality_flag = %s "
                "WHERE id = %s",
                (signal, new_flag, log_id),
            )

    return {"matched": True, "log_id": log_id, "flag_set": new_flag, "signal": signal}
