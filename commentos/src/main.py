"""CommentOS — identify (ED reframe), organise (channels + campaigns), publish."""
import json
import os

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import pipeline

DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="CommentOS", version="0.2.0")


def _db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


class AnalyzeRequest(BaseModel):
    comment: str
    channel_id: str | None = None
    campaign_id: str | None = None
    brand_id: str | None = None
    source_url: str | None = None
    source_author: str | None = None


class InboundCreate(BaseModel):
    source_text: str
    brand_id: str | None = None
    channel_id: str | None = "linkedin"
    keyword: str | None = None
    source_url: str | None = None
    source_author: str | None = None


class InboundDecision(BaseModel):
    campaign_id: str | None = None
    reply_text: str | None = None   # approve with an already-edited draft


class QueueRequest(BaseModel):
    analysis_id: int
    reply_text: str
    target_url: str | None = None
    channel_id: str | None = None


class StatusRequest(BaseModel):
    status: str


class IngestRequest(BaseModel):
    domain: str
    text: str
    source: str | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/brands")
def brands():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decision_os.brand WHERE enabled ORDER BY short")
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/inbound")
def list_inbound(status: str | None = None, brand_id: str | None = None, limit: int = 100):
    q = "SELECT * FROM decision_os.inbound_item WHERE 1=1"
    params: list = []
    if status:
        q += " AND status = %s"; params.append(status)
    if brand_id:
        q += " AND brand_id = %s"; params.append(brand_id)
    q += " ORDER BY discovered_at DESC LIMIT %s"; params.append(limit)
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            return [dict(r, discovered_at=str(r["discovered_at"]),
                         decided_at=str(r["decided_at"]) if r["decided_at"] else None)
                    for r in cur.fetchall()]


@app.post("/api/inbound")
def create_inbound(req: InboundCreate):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO decision_os.inbound_item
                    (brand_id, channel_id, keyword, source_url, source_author, source_text)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (channel_id, source_url) DO NOTHING
                RETURNING id
            """, (req.brand_id, req.channel_id, req.keyword, req.source_url,
                  req.source_author, req.source_text.strip()))
            row = cur.fetchone()
    return {"id": row["id"] if row else None, "duplicate": row is None}


@app.post("/api/inbound/{inbound_id}/draft")
def draft_inbound(inbound_id: int, req: InboundDecision):
    """Run the ED pipeline against an inbound candidate; store the analysis."""
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decision_os.inbound_item WHERE id=%s", (inbound_id,))
            item = cur.fetchone()
            if not item:
                raise HTTPException(404, "not found")
            campaign = brand = None
            if req.campaign_id:
                cur.execute("SELECT * FROM decision_os.campaign WHERE id=%s", (req.campaign_id,))
                campaign = cur.fetchone()
            if item["brand_id"]:
                cur.execute("SELECT * FROM decision_os.brand WHERE id=%s", (item["brand_id"],))
                brand = cur.fetchone()
    try:
        result = pipeline.analyze(item["source_text"], campaign, item["channel_id"], brand)
    except Exception as e:
        raise HTTPException(502, f"pipeline error: {e}")
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO decision_os.comment_analysis
                    (comment_text, source_url, source_author, hypothesis, lenses, matches,
                     restatement, insight, channel_id, campaign_id, brand_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (item["source_text"], item["source_url"], item["source_author"],
                  result["hypothesis"], result["lenses"], json.dumps(result["matches"], default=str),
                  result["restatement"], result["insight"], item["channel_id"],
                  req.campaign_id, item["brand_id"]))
            aid = cur.fetchone()["id"]
            cur.execute("UPDATE decision_os.inbound_item SET analysis_id=%s WHERE id=%s",
                        (aid, inbound_id))
    return {"analysis_id": aid, **result}


@app.post("/api/inbound/{inbound_id}/approve")
def approve_inbound(inbound_id: int, req: InboundDecision):
    """Approve: push the (edited) reply into the publish queue."""
    if not req.reply_text or not req.reply_text.strip():
        raise HTTPException(400, "reply_text required")
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decision_os.inbound_item WHERE id=%s", (inbound_id,))
            item = cur.fetchone()
            if not item:
                raise HTTPException(404, "not found")
            cur.execute("""
                INSERT INTO decision_os.queue_item
                    (analysis_id, reply_text, target_url, channel_id, brand_id)
                VALUES (%s,%s,%s,%s,%s) RETURNING id
            """, (item["analysis_id"], req.reply_text.strip(), item["source_url"],
                  item["channel_id"], item["brand_id"]))
            qid = cur.fetchone()["id"]
            cur.execute("""UPDATE decision_os.inbound_item
                           SET status='approved', decided_at=now() WHERE id=%s""", (inbound_id,))
            if item["analysis_id"]:
                cur.execute("UPDATE decision_os.comment_analysis SET status='queued' WHERE id=%s",
                            (item["analysis_id"],))
    return {"queue_id": qid}


@app.post("/api/inbound/{inbound_id}/reject")
def reject_inbound(inbound_id: int):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE decision_os.inbound_item
                           SET status='rejected', decided_at=now() WHERE id=%s RETURNING id""",
                        (inbound_id,))
            if not cur.fetchone():
                raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/channels")
def channels():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decision_os.channel WHERE enabled ORDER BY id")
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/campaigns")
def campaigns():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decision_os.campaign WHERE active ORDER BY id")
            return [dict(r, created_at=str(r["created_at"])) for r in cur.fetchall()]


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    text = req.comment.strip()
    if not text:
        raise HTTPException(400, "empty comment")
    campaign = brand = None
    with _db() as conn:
        with conn.cursor() as cur:
            if req.campaign_id:
                cur.execute("SELECT * FROM decision_os.campaign WHERE id=%s", (req.campaign_id,))
                campaign = cur.fetchone()
            if req.brand_id:
                cur.execute("SELECT * FROM decision_os.brand WHERE id=%s", (req.brand_id,))
                brand = cur.fetchone()
    try:
        result = pipeline.analyze(text, campaign, req.channel_id, brand)
    except Exception as e:
        raise HTTPException(502, f"pipeline error: {e}")
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO decision_os.comment_analysis
                    (comment_text, source_url, source_author, hypothesis, lenses, matches,
                     restatement, insight, channel_id, campaign_id, brand_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, created_at
            """, (text, req.source_url, req.source_author, result["hypothesis"],
                  result["lenses"], json.dumps(result["matches"], default=str),
                  result["restatement"], result["insight"], req.channel_id,
                  req.campaign_id, req.brand_id))
            row = cur.fetchone()
    return {"id": row["id"], "created_at": str(row["created_at"]), **result}


@app.get("/api/analyses")
def list_analyses(status: str | None = None, channel_id: str | None = None, limit: int = 50):
    q = "SELECT * FROM decision_os.comment_analysis WHERE 1=1"
    params: list = []
    if status:
        q += " AND status = %s"
        params.append(status)
    if channel_id:
        q += " AND channel_id = %s"
        params.append(channel_id)
    q += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            return [dict(r, created_at=str(r["created_at"])) for r in cur.fetchall()]


@app.post("/api/queue")
def queue(req: QueueRequest):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO decision_os.queue_item (analysis_id, reply_text, target_url, channel_id)
                VALUES (%s,%s,%s,
                        COALESCE(%s, (SELECT channel_id FROM decision_os.comment_analysis WHERE id=%s)))
                RETURNING id
            """, (req.analysis_id, req.reply_text, req.target_url, req.channel_id, req.analysis_id))
            qid = cur.fetchone()["id"]
            cur.execute("UPDATE decision_os.comment_analysis SET status='queued' WHERE id=%s",
                        (req.analysis_id,))
    return {"queue_id": qid}


@app.get("/api/queue")
def list_queue(status: str = "approved", channel_id: str | None = None):
    q = """SELECT q.*, a.comment_text, a.lenses, a.campaign_id
           FROM decision_os.queue_item q
           LEFT JOIN decision_os.comment_analysis a ON a.id = q.analysis_id
           WHERE q.status = %s"""
    params: list = [status]
    if channel_id:
        q += " AND q.channel_id = %s"
        params.append(channel_id)
    q += " ORDER BY q.approved_at DESC"
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            return [dict(r, approved_at=str(r["approved_at"]),
                         published_at=str(r["published_at"]) if r["published_at"] else None)
                    for r in cur.fetchall()]


@app.post("/api/queue/{queue_id}/status")
def set_queue_status(queue_id: int, req: StatusRequest):
    if req.status not in ("approved", "published", "skipped"):
        raise HTTPException(400, "bad status")
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE decision_os.queue_item
                SET status=%s, published_at = CASE WHEN %s='published' THEN now() ELSE published_at END
                WHERE id=%s RETURNING id
            """, (req.status, req.status, queue_id))
            if not cur.fetchone():
                raise HTTPException(404, "not found")
    return {"ok": True}


# ── Knowledge: ingest external frameworks, view the crosswalk ────────────────

@app.post("/api/ingest")
def ingest(req: IngestRequest):
    domain = req.domain.strip().lower().replace(" ", "-")
    if not domain or not req.text.strip():
        raise HTTPException(400, "domain and text required")
    try:
        return pipeline.ingest_knowledge(domain, req.text, req.source)
    except Exception as e:
        raise HTTPException(502, f"ingest error: {e}")


@app.get("/api/knowledge")
def knowledge(domain: str | None = None):
    q = """SELECT ec.id, ec.domain, ec.term, ec.description, ec.source,
                  str_agg.mappings
           FROM decision_os.external_concept ec
           LEFT JOIN LATERAL (
               SELECT json_agg(json_build_object('ed_concept', ce.name, 'score', cw.score)
                               ORDER BY cw.score DESC) AS mappings
               FROM decision_os.concept_crosswalk cw
               JOIN decision_os.concept_embedding ce ON ce.graph_node_id = cw.graph_node_id
               WHERE cw.external_id = ec.id
           ) str_agg ON true
           WHERE 1=1"""
    params: list = []
    if domain:
        q += " AND ec.domain = %s"
        params.append(domain)
    q += " ORDER BY ec.domain, ec.term"
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            return [dict(r) for r in cur.fetchall()]


# Static frontend
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")


@app.get("/")
def index():
    return FileResponse("static/index.html")
